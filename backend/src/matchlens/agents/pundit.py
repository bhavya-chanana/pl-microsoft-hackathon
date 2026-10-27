"""Ask the Pundit: fans' questions about the live match, answered from the data.

1. **Router (code, no model).** Reads the question for what it is about (the
   score, who is on top, a player, the best player, pressing, chances, how the
   game has changed) in English, Hindi or Spanish, and refuses anything that is
   not about this match.
2. **Pack (code).** Builds the facts for that question from the same match-state
   tools the MCP server exposes: header, momentum and rhythm, team stats,
   player stats and data ratings, and the key moments with their ids.
3. **Pundit agent (fast model).** Answers in the fan's language and mode, citing
   the moments it relies on, with checkable claims.
4. **Fact-Checker.** Verifies every claim and number against the engine. An
   unsupported answer is blocked and the data-only answer is shown instead.

The data-only answer (``template``) needs no model, so the pundit works offline
and in the recorded demo.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from matchlens.agents.context import match_header, momentum_brief, player_brief
from matchlens.agents.contracts import (
    AgentClaim,
    FactCheckReport,
    FactCheckStatus,
    FanProfile,
    KeyNumber,
    _Strict,
)
from matchlens.agents.factchecker import check_text
from matchlens.domain.models import EventType
from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.ratings import data_rating, has_played
from matchlens.interpret.signals import SignalType

Intent = Literal["score", "momentum", "rhythm", "best_player", "player", "pressure", "chances", "change",
                 "team", "prediction", "discipline", "summary", "off_topic"]

MAX_QUESTION = 200
PUNDIT_TIMEOUT_S = 15.0


class PunditDraft(_Strict):
    answer: str = Field(description="2-4 sentences that answer the question directly")
    points: list[str] = Field(description="0-3 short supporting points, each with a number from context")
    key_numbers: list[KeyNumber] = Field(description="0-3 numbers that back the answer")
    cited_moments: list[str] = Field(description="signal_id values from context.moments the answer relies on")
    claims: list[AgentClaim]


class CitedMoment(BaseModel):
    insight_id: str
    minute_label: str
    type: str
    team_id: str | None


class PunditAnswer(BaseModel):
    question: str
    intent: Intent
    answer: str
    points: list[str]
    key_numbers: list[KeyNumber]
    cited_moments: list[CitedMoment]
    minute_label: str
    profile: FanProfile
    fact_check: FactCheckReport
    source: Literal["agent", "data"]
    agents: list[str]
    latency_ms: float | None = None


# ------------------------------------------------------------------- routing

_WORDS: dict[Intent, tuple[str, ...]] = {
    "best_player": ("best", "standout", "man of the match", "player of the match", "motm", "star", "mvp",
                    "सबसे अच्छा", "बेहतरीन", "सर्वश्रेष्ठ", "स्टार", "mejor", "figura", "destacado"),
    "prediction": ("will win", "who wins", "predict", "going to win", "final score", "जीतेगा", "जीतेगी",
                   "ganará", "va a ganar", "predicción"),
    "discipline": ("card", "yellow", "booking", "booked", "sent off", "sending off", "dismiss", "foul",
                   "कार्ड", "पीला", "पीले", "फाउल", "tarjeta", "amarilla", "roja", "expuls", "falta"),
    "rhythm": ("chaos", "chaotic", "control", "rhythm", "tempo", "messy", "अफरा", "नियंत्रण", "लय", "caos",
               "ritmo", "controla"),
    "pressure": ("press", "pressure", "pressing", "दबाव", "प्रेस", "presión", "presiona"),
    "chances": ("shot", "chance", "xg", "attack", "on target", "मौक", "शॉट", "ocasi", "tiro", "disparo"),
    "change": ("change", "changed", "half-time", "half time", "second half", "since", "turn", "comeback", "come back",
               "बदल", "हाफ", "वापसी", "cambi", "descanso", "segunda parte", "remontada"),
    "momentum": ("momentum", "on top", "dominat", "why", "winning the", "better side", "मोमेंटम", "हावी",
                 "क्यों", "बेहतर", "por qué", "domina", "mejor equipo", "impulso"),
    "score": ("score", "result", "winning", "who leads", "leading", "स्कोर", "आगे", "marcador", "resultado",
              "gana", "va ganando"),
    "summary": ("what happened", "summary", "so far", "story", "recap", "क्या हुआ", "अब तक", "qué pasó",
                "resumen", "hasta ahora"),
}
_FOOTBALL = ("match", "game", "goal", "team", "player", "ball", "pass", "keeper", "defen", "midfield", "striker",
             "card", "half", "minute", "football", "मैच", "गोल", "टीम", "खिलाड़ी", "खेल", "partido", "gol",
             "equipo", "jugador", "balón", "portero")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _player_in(question: str, engine: MatchEngine) -> str | None:
    q = _norm(question)
    best: tuple[int, str] | None = None
    for pid, p in engine.meta.players.items():
        for name in {p.name, p.short_name, *p.name.split()}:
            n = name.lower()
            if len(n) >= 4 and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", q) and (best is None or len(n) > best[0]):
                best = (len(n), pid)
    return best[1] if best else None


def _team_in(question: str, engine: MatchEngine) -> str | None:
    q = _norm(question)
    for team in (engine.meta.home, engine.meta.away):
        names = {team.name.lower(), team.short_name.lower(), team.name.split()[0].lower()}
        if any(len(n) >= 3 and n in q for n in names):
            return team.team_id
    return None


def route(question: str, engine: MatchEngine) -> tuple[Intent, str | None, str | None]:
    """(intent, player_id, team_id) for a question."""
    q = _norm(question)
    player = _player_in(question, engine)
    team = _team_in(question, engine)
    for intent in ("best_player", "prediction", "discipline", "rhythm", "pressure", "chances", "change", "momentum", "score",
                   "summary"):
        if any(w in q for w in _WORDS[intent]):  # type: ignore[index]
            if intent in ("best_player",) or not player:
                return intent, player, team  # type: ignore[return-value]
    if player:
        return "player", player, team
    if team:
        return "team", None, team
    if any(w in q for w in _FOOTBALL):
        return "summary", None, None
    return "off_topic", None, None


# --------------------------------------------------------------------- pack


def _ratings(engine: MatchEngine) -> list[dict[str, Any]]:
    score = engine.score()
    finished = engine.state()["status"] == "full_time"
    rows = []
    for pid in engine.meta.players:
        stats = engine.player_stats(pid)
        if not has_played(stats):
            continue
        team = str(stats["team_id"])
        conceded = sum(v for t, v in score.items() if t != team)
        rows.append({"player_id": pid, "name": stats["name"], "team_id": team,
                     "data_rating": data_rating(stats, conceded=conceded, finished=finished).value})
    return sorted(rows, key=lambda r: -r["data_rating"])[:5]


def _pressing(engine: MatchEngine, minutes: float = 10) -> dict[str, int]:
    since = engine.c.elapsed - minutes * 60
    out = {engine.meta.home.team_id: 0, engine.meta.away.team_id: 0}
    for e in engine.events(since=since, types={EventType.PRESSURE}):
        if e.team_id in out:
            out[e.team_id] += 1
    return out


def _moments(engine: MatchEngine, insights: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in engine.signals():
        if s.priority > 2 or s.type in (SignalType.PERIOD, SignalType.CORRECTION):
            continue
        ins = insights.get(s.signal_id)
        if ins and ins.get("stage") == "retracted":
            continue
        out.append({"signal_id": s.signal_id, "minute": s.minute_label, "elapsed": s.elapsed,
                    "type": s.type.value, "team_id": s.team_id, "player_ids": s.player_ids, "facts": s.facts,
                    "headline": ins["headline"] if ins else None})
    return out[-14:]


def _discipline_focus(question: str) -> list[str]:
    """What a discipline question is about: cards, fouls, or both."""
    q = _norm(question)
    fouls = any(w in q for w in ("foul", "फाउल", "falta"))
    cards = any(w in q for w in _WORDS["discipline"] if w not in ("foul", "फाउल", "falta"))
    return [k for k, on in (("cards", cards or not fouls), ("fouls", fouls)) if on]


def _cards(engine: MatchEngine) -> list[dict[str, Any]]:
    """Every card shown so far, with the event id a claim can cite, the minute and the player's name."""
    out = []
    for e in engine.events(types={EventType.CARD}):
        player = engine.meta.players.get(e.player_id or "")
        out.append({"event_id": e.event_id, "minute": e.minute_label, "team_id": e.team_id,
                    "player": player.name if player else None, "card": e.outcome.value})
    return out


def pundit_pack(engine: MatchEngine, insights: dict[str, dict[str, Any]], intent: Intent, player_id: str | None,
                team_id: str | None, question: str = "") -> dict[str, Any]:
    teams = {}
    for tid in (engine.meta.home.team_id, engine.meta.away.team_id):
        stats = engine.team_stats(tid)
        teams[tid] = {k: stats[k] for k in ("goals", "possession_pct", "shots", "shots_on_target", "big_chances",
                                            "xg", "passes", "pass_accuracy", "high_turnovers", "players_on_pitch",
                                            "yellow_cards", "red_cards", "fouls")
                      if stats.get(k) is not None}
    ratings = _ratings(engine)
    focus = player_id or (ratings[0]["player_id"] if intent == "best_player" and ratings else None)
    return {
        "match": match_header(engine),
        "intent": intent,
        "team_focus": team_id,
        "momentum": momentum_brief(engine, minutes=15, step_minutes=3),
        "teams": teams,
        "pressing_last_10_min": _pressing(engine),
        "top_rated": ratings,
        "player": player_brief(engine, focus) if focus else None,
        "moments": _moments(engine, insights),
        **({"cards": _cards(engine), "asked_about": _discipline_focus(question)} if intent == "discipline" else {}),
    }


# ----------------------------------------------------------------- templates

_T: dict[str, dict[str, str]] = {
    "score": {
        "en": "It's {home} {hs}-{as_} {away} at {minute}.",
        "hi": "{minute} पर स्कोर {home} {hs}-{as_} {away} है।",
        "es": "{home} {hs}-{as_} {away} en el {minute}.",
    },
    "leads": {
        "en": "{team} lead.", "hi": "{team} आगे है।", "es": "{team} va por delante.",
    },
    "level": {
        "en": "It's level.", "hi": "मुकाबला बराबरी पर है।", "es": "Van empatados.",
    },
    "on_top": {
        "en": "{team} are on top right now: the momentum index is {idx} (±100 is total control).",
        "hi": "इस समय {team} हावी है: मोमेंटम इंडेक्स {idx} है (±100 का मतलब पूरा नियंत्रण)।",
        "es": "{team} domina ahora mismo: el índice de impulso está en {idx} (±100 es control total).",
    },
    "even": {
        "en": "Neither side is on top: the momentum index is {idx}, close to level.",
        "hi": "कोई भी टीम हावी नहीं है: मोमेंटम इंडेक्स {idx} है, लगभग बराबर।",
        "es": "Ningún equipo domina: el índice de impulso está en {idx}, casi igualado.",
    },
    "shots_line": {
        "en": "{team} have {shots} shots ({xg} xG) and {poss}% of the ball; {opp} have {oshots} shots ({oxg} xG).",
        "hi": "{team} के {shots} शॉट ({xg} xG) और {poss}% कब्ज़ा; {opp} के {oshots} शॉट ({oxg} xG)।",
        "es": "{team} suma {shots} tiros ({xg} xG) y un {poss}% de posesión; {opp}, {oshots} tiros ({oxg} xG).",
    },
    "rhythm_chaos": {
        "en": "The game is chaotic: {tpm} a minute, so neither side can keep the ball.",
        "hi": "खेल में अफरा-तफरी है: हर मिनट {tpm}, कोई टीम गेंद नहीं रख पा रही।",
        "es": "El partido es caótico: {tpm} por minuto, nadie retiene el balón.",
    },
    "rhythm_control": {
        "en": "{team} are controlling it: long spells on the ball, only {tpm} a minute.",
        "hi": "{team} खेल पर नियंत्रण रखे हुए है: लंबे समय तक गेंद, हर मिनट सिर्फ़ {tpm}।",
        "es": "{team} controla el partido: largas posesiones y solo {tpm} por minuto.",
    },
    "rhythm_balanced": {
        "en": "The tempo is steady: {tpm} a minute.",
        "hi": "खेल की गति स्थिर है: हर मिनट {tpm}।",
        "es": "El ritmo es estable: {tpm} por minuto.",
    },
    "cards_none": {
        "en": "No cards have been shown yet.", "hi": "अभी तक कोई कार्ड नहीं दिखाया गया।",
        "es": "Todavía no se ha mostrado ninguna tarjeta.",
    },
    "cards_total": {
        "en": "So far: {y} and {r}.", "hi": "अब तक: {y} और {r}।", "es": "Hasta ahora: {y} y {r}.",
    },
    "cards_team": {
        "en": "{team}: {y} yellow, {r} red.", "hi": "{team}: {y} पीले, {r} लाल।",
        "es": "{team}: amarillas {y}, rojas {r}.",
    },
    "cards_focus": {
        "en": "{team} have {y} and {r} so far.", "hi": "{team} के अब तक {y} और {r} हैं।",
        "es": "{team} lleva {y} y {r}.",
    },
    "sent_off": {
        "en": "Sent off: {name} ({team}, {minute}).", "hi": "बाहर भेजे गए: {name} ({team}, {minute})।",
        "es": "Expulsado: {name} ({team}, {minute}).",
    },
    "fouls": {
        "en": "Fouls: {team} {n}, {opp} {on}.", "hi": "फाउल: {team} {n}, {opp} {on}।",
        "es": "Faltas: {team} {n}, {opp} {on}.",
    },
    "best": {
        "en": "{name} has been the standout so far, with a data rating of {rating}.",
        "hi": "अब तक {name} सबसे शानदार रहे हैं, डेटा रेटिंग {rating}।",
        "es": "{name} es el más destacado hasta ahora, con una nota de datos de {rating}.",
    },
    "player": {
        "en": "{name} so far: {stats}.",
        "hi": "{name} अब तक: {stats}।",
        "es": "{name} hasta ahora: {stats}.",
    },
    "player_quiet": {
        "en": "{name} has not been involved much yet.",
        "hi": "{name} अभी तक ज़्यादा खेल में शामिल नहीं रहे।",
        "es": "{name} apenas ha participado todavía.",
    },
    "pressure": {
        "en": "In the last 10 minutes {team} pressed {n} times to {on} by {opp}.",
        "hi": "पिछले 10 मिनट में {team} ने {n} बार प्रेस किया, {opp} ने {on} बार।",
        "es": "En los últimos 10 minutos {team} presionó {n} veces, por {on} de {opp}.",
    },
    "turnovers": {
        "en": "{team} have won the ball high up the pitch {n} times.",
        "hi": "{team} ने {n} बार ऊपर मैदान में गेंद छीनी है।",
        "es": "{team} ha recuperado el balón arriba {n} veces.",
    },
    "chances": {
        "en": "{team}: {shots} shots, {sot} on target, {xg} xG. {opp}: {oshots} shots, {osot} on target, {oxg} xG.",
        "hi": "{team}: {shots} शॉट, {sot} निशाने पर, {xg} xG। {opp}: {oshots} शॉट, {osot} निशाने पर, {oxg} xG।",
        "es": "{team}: {shots} tiros, {sot} a puerta, {xg} xG. {opp}: {oshots} tiros, {osot} a puerta, {oxg} xG.",
    },
    "change": {
        "en": "The big turns so far: {moments}.",
        "hi": "अब तक के बड़े मोड़: {moments}।",
        "es": "Los grandes giros hasta ahora: {moments}.",
    },
    "no_moments": {
        "en": "Nothing has turned the game yet.",
        "hi": "अभी तक खेल में कोई बड़ा मोड़ नहीं आया।",
        "es": "Todavía no ha habido un giro importante.",
    },
    "prediction": {
        "en": "I don't predict results; I explain what the data shows.",
        "hi": "मैं नतीजों की भविष्यवाणी नहीं करता; मैं बताता हूँ कि डेटा क्या दिखाता है।",
        "es": "No pronostico resultados; explico lo que muestran los datos.",
    },
    "off_topic": {
        "en": "I can only answer questions about this match: the score, who's on top, players, chances and how the game has changed.",
        "hi": "मैं सिर्फ़ इस मैच के बारे में सवालों का जवाब दे सकता हूँ: स्कोर, कौन हावी है, खिलाड़ी, मौके और खेल कैसे बदला।",
        "es": "Solo respondo preguntas sobre este partido: el marcador, quién domina, los jugadores, las ocasiones y cómo ha cambiado.",
    },
}

_STAT_LABELS: dict[str, dict[str, str]] = {
    "goals": {"en": "goals", "hi": "गोल", "es": "goles"},
    "assists": {"en": "assists", "hi": "असिस्ट", "es": "asistencias"},
    "shots": {"en": "shots", "hi": "शॉट", "es": "tiros"},
    "key_passes": {"en": "key passes", "hi": "की पास", "es": "pases clave"},
    "pass_accuracy": {"en": "% pass accuracy", "hi": "% पास सटीकता", "es": "% de acierto en el pase"},
    "tackles": {"en": "tackles", "hi": "टैकल", "es": "entradas"},
    "saves": {"en": "saves", "hi": "बचाव", "es": "paradas"},
    "distance_km": {"en": "km covered", "hi": "किमी दौड़े", "es": "km recorridos"},
    "top_speed_kmh": {"en": "km/h top speed", "hi": "किमी/घं. टॉप स्पीड", "es": "km/h de punta"},
    "rating": {"en": "Data rating", "hi": "डेटा रेटिंग", "es": "Nota de datos"},
    "momentum": {"en": "Momentum", "hi": "मोमेंटम", "es": "Impulso"},
    "xg": {"en": "xG", "hi": "xG", "es": "xG"},
    "pressing": {"en": "Pressing actions", "hi": "प्रेसिंग", "es": "Presiones"},
    "turnovers": {"en": "Turnovers a minute", "hi": "टर्नओवर/मिनट", "es": "Pérdidas por minuto"},
}
# Counts of one read "1 goal", not "1 goals" (Hindi nouns do not change).
_SINGULAR: dict[str, dict[str, str]] = {
    "goals": {"en": "goal", "es": "gol"},
    "assists": {"en": "assist", "es": "asistencia"},
    "shots": {"en": "shot", "es": "tiro"},
    "key_passes": {"en": "key pass", "es": "pase clave"},
    "tackles": {"en": "tackle", "es": "entrada"},
    "saves": {"en": "save", "es": "parada"},
}
# (one, many) per language, for counts that must read "1 yellow card" and "2 yellow cards".
_UNITS: dict[str, dict[str, tuple[str, str]]] = {
    "yellow": {"en": ("yellow card", "yellow cards"), "hi": ("पीला कार्ड", "पीले कार्ड"),
               "es": ("tarjeta amarilla", "tarjetas amarillas")},
    "red": {"en": ("red card", "red cards"), "hi": ("लाल कार्ड", "लाल कार्ड"),
            "es": ("tarjeta roja", "tarjetas rojas")},
    "turnover": {"en": ("turnover", "turnovers"), "hi": ("टर्नओवर", "टर्नओवर"), "es": ("pérdida", "pérdidas")},
}
PLAYER_STATS = ("goals", "assists", "shots", "key_passes", "tackles", "saves", "pass_accuracy", "distance_km",
                "top_speed_kmh")


def _t(key: str, lang: str, **values: Any) -> str:
    table = _T[key]
    return (table.get(lang) or table["en"]).format(**values)


def _count(n: Any, unit: str, lang: str) -> str:
    """"1 turnover", "1.4 turnovers", "0 red cards": the noun agrees with the number."""
    one, many = _UNITS[unit].get(lang) or _UNITS[unit]["en"]
    return f"{_num(n)} {one if n == 1 else many}"


def _label(key: str, lang: str) -> str:
    return _STAT_LABELS[key].get(lang) or _STAT_LABELS[key]["en"]


def _num(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _names(pack: dict[str, Any]) -> dict[str, str]:
    m = pack["match"]
    return {m["home"]["team_id"]: m["home"]["name"], m["away"]["team_id"]: m["away"]["name"]}


def _focus_team(pack: dict[str, Any]) -> tuple[str, str]:
    """(team, opponent): the asked-about team, else the side on top, else home."""
    m = pack["match"]
    home, away = m["home"]["team_id"], m["away"]["team_id"]
    team = pack.get("team_focus")
    if team not in (home, away):
        team = away if pack["momentum"]["now"] < 0 else home
    return team, away if team == home else home


def _player_line(pack: dict[str, Any], lang: str) -> str:
    p = pack["player"]
    stats = p["stats"]
    parts = []
    for k in PLAYER_STATS:
        v = stats.get(k)
        if v in (None, 0, 0.0):
            continue
        label = _SINGULAR.get(k, {}).get(lang) if v == 1 else None
        parts.append(f"{_num(v)}{_label(k, lang)}" if k == "pass_accuracy" else f"{_num(v)} {label or _label(k, lang)}")
        if len(parts) == 4:
            break
    if not parts:
        return _t("player_quiet", lang, name=p["name"])
    return _t("player", lang, name=p["name"], stats=", ".join(parts))


def _cite(pack: dict[str, Any], intent: Intent) -> list[str]:
    moments = pack["moments"]
    player = (pack.get("player") or {}).get("player_id")
    if intent in ("player", "best_player") and player:
        picked = [m for m in moments if player in m["player_ids"]]
    elif intent in ("momentum", "change"):
        picked = [m for m in moments if m["type"] in ("momentum_swing", "goal", "card", "pressure_spell")]
    elif intent == "rhythm":
        picked = [m for m in moments if m["type"] in ("chaos_spell", "control_spell")]
    elif intent == "pressure":
        picked = [m for m in moments if m["type"] == "pressure_spell"]
    elif intent == "chances":
        picked = [m for m in moments if m["type"] in ("goal", "big_chance", "big_save")]
    elif intent == "discipline":
        picked = [m for m in moments if m["type"] == "card"]
    elif intent in ("score", "summary", "team", "prediction"):
        picked = [m for m in moments if m["type"] in ("goal", "card", "momentum_swing")]
    else:
        picked = []
    return [m["signal_id"] for m in picked[-3:]]


def _discipline(pack: dict[str, Any], lang: str, names: dict[str, str],
                focus: str | None) -> tuple[list[str], list[KeyNumber]]:
    """Cards and fouls counted from the match data (team stats and the card events), never estimated."""
    teams = pack["teams"]
    home, away = pack["match"]["home"]["team_id"], pack["match"]["away"]["team_id"]
    shown = [focus] if focus in (home, away) else [home, away]
    y = sum(teams[t].get("yellow_cards", 0) for t in shown)
    r = sum(teams[t].get("red_cards", 0) for t in shown)
    asked = pack.get("asked_about") or ["cards"]
    sentences: list[str] = []
    numbers: list[KeyNumber] = []
    if "cards" in asked:
        if not (y or r):
            sentences.append(_t("cards_none", lang))
        elif len(shown) == 1:
            sentences.append(_t("cards_focus", lang, team=names[focus], y=_count(y, "yellow", lang),
                                r=_count(r, "red", lang)))
        else:
            sentences.append(_t("cards_total", lang, y=_count(y, "yellow", lang), r=_count(r, "red", lang)))
            sentences += [_t("cards_team", lang, team=names[t], y=teams[t].get("yellow_cards", 0),
                             r=teams[t].get("red_cards", 0)) for t in shown]
        for c in pack.get("cards", []):
            if c["card"] in ("red", "second_yellow") and c["team_id"] in shown:
                sentences.append(_t("sent_off", lang, name=c["player"] or "?", team=names[c["team_id"]],
                                    minute=c["minute"]))
        numbers += [KeyNumber(label=_UNITS["yellow"].get(lang, _UNITS["yellow"]["en"])[1].capitalize(),
                              value=_num(y), unit=None),
                    KeyNumber(label=_UNITS["red"].get(lang, _UNITS["red"]["en"])[1].capitalize(),
                              value=_num(r), unit=None)]
    if "fouls" in asked:
        other = away if shown == [home] else home
        a, b = (shown[0], other) if len(shown) == 1 else (home, away)
        sentences.append(_t("fouls", lang, team=names[a], n=teams[a].get("fouls", 0), opp=names[b],
                            on=teams[b].get("fouls", 0)))
    return sentences, numbers


def template(pack: dict[str, Any], lang: str) -> PunditDraft:
    """The data-only answer: every number is copied from the pack."""
    intent: Intent = pack["intent"]
    m = pack["match"]
    names = _names(pack)
    home, away = m["home"]["team_id"], m["away"]["team_id"]
    hs, as_ = m["score"][home], m["score"][away]
    idx = pack["momentum"]["now"]
    team, opp = _focus_team(pack)
    ts, os_ = pack["teams"][team], pack["teams"][opp]
    sentences: list[str] = []
    points: list[str] = []
    numbers: list[KeyNumber] = []

    def score_line() -> str:
        lead = _t("level", lang) if hs == as_ else _t("leads", lang, team=names[home] if hs > as_ else names[away])
        return f"{_t('score', lang, home=names[home], away=names[away], hs=hs, as_=as_, minute=m['minute'])} {lead}"

    def on_top() -> str:
        leader = home if idx >= 10 else away if idx <= -10 else None
        return _t("on_top", lang, team=names[leader], idx=_num(idx)) if leader else _t("even", lang, idx=_num(idx))

    def shots_line() -> str:
        return _t("shots_line", lang, team=names[team], opp=names[opp], shots=ts.get("shots", 0),
                  xg=_num(ts.get("xg", 0)), poss=_num(ts.get("possession_pct", 0)), oshots=os_.get("shots", 0),
                  oxg=_num(os_.get("xg", 0)))

    def rhythm_line() -> str:
        r = pack["momentum"].get("rhythm") or {"state": "balanced", "turnovers_per_min": 0, "controlling_team": None}
        tpm = _count(r["turnovers_per_min"], "turnover", lang)
        if r["state"] == "chaos":
            return _t("rhythm_chaos", lang, tpm=tpm)
        if r["state"] == "control" and r["controlling_team"]:
            return _t("rhythm_control", lang, team=names[r["controlling_team"]], tpm=tpm)
        return _t("rhythm_balanced", lang, tpm=tpm)

    if intent == "off_topic":
        return PunditDraft(answer=_t("off_topic", lang), points=[], key_numbers=[], cited_moments=[], claims=[])
    if intent == "score":
        sentences = [score_line(), on_top()]
    elif intent == "momentum":
        sentences = [on_top(), shots_line()]
        numbers.append(KeyNumber(label=_label("momentum", lang), value=_num(idx), unit=None))
    elif intent == "rhythm":
        sentences = [rhythm_line(), on_top()]
        r = pack["momentum"].get("rhythm")
        if r:
            numbers.append(KeyNumber(label=_label("turnovers", lang), value=_num(r["turnovers_per_min"]), unit=None))
    elif intent in ("best_player", "player") and pack.get("player"):
        p = pack["player"]
        rated = next((r for r in pack["top_rated"] if r["player_id"] == p["player_id"]), None)
        if intent == "best_player" and rated:
            sentences.append(_t("best", lang, name=p["name"], rating=_num(rated["data_rating"])))
        sentences.append(_player_line(pack, lang))
        if rated:
            numbers.append(KeyNumber(label=_label("rating", lang), value=_num(rated["data_rating"]), unit=None))
    elif intent == "pressure":
        press = pack["pressing_last_10_min"]
        sentences.append(_t("pressure", lang, team=names[team], n=press[team], opp=names[opp], on=press[opp]))
        if ts.get("high_turnovers"):
            sentences.append(_t("turnovers", lang, team=names[team], n=ts["high_turnovers"]))
        numbers.append(KeyNumber(label=_label("pressing", lang), value=str(press[team]), unit=None))
    elif intent == "chances":
        sentences.append(_t("chances", lang, team=names[team], opp=names[opp], shots=ts.get("shots", 0),
                            sot=ts.get("shots_on_target", 0), xg=_num(ts.get("xg", 0)), oshots=os_.get("shots", 0),
                            osot=os_.get("shots_on_target", 0), oxg=_num(os_.get("xg", 0))))
        numbers.append(KeyNumber(label=_label("xg", lang), value=_num(ts.get("xg", 0)), unit=None))
    elif intent == "change":
        turns = [mm for mm in pack["moments"] if mm["type"] in ("goal", "momentum_swing", "card")][-3:]
        listed = "; ".join(f"{mm['minute']} {mm['headline'] or mm['type'].replace('_', ' ')}" for mm in turns)
        sentences.append(_t("change", lang, moments=listed) if turns else _t("no_moments", lang))
        sentences.append(on_top())
    elif intent == "discipline":
        sentences, numbers = _discipline(pack, lang, names, pack.get("team_focus"))
    elif intent == "prediction":
        sentences = [_t("prediction", lang), score_line(), on_top()]
    else:  # team, summary, or a player question without a player
        sentences = [score_line(), on_top(), shots_line()]
    if intent in ("momentum", "change", "summary", "team"):
        points.append(rhythm_line())
    return PunditDraft(answer=" ".join(sentences), points=points, key_numbers=numbers[:3],
                       cited_moments=_cite(pack, intent), claims=[])


# ------------------------------------------------------------------ service


# Bump when the router, pack, templates or prompt change what an answer says: answers cached under an
# older version (Cosmos ``fan_outputs`` outlives the process) are then never served again.
ANSWER_VERSION = 2


def _key(engine: MatchEngine, question: str, profile: FanProfile) -> str:
    raw = [ANSWER_VERSION, int(engine.c.elapsed // 60), len(engine.signals()), _norm(question),
           profile.model_dump_json()]
    return hashlib.sha256(json.dumps(raw).encode()).hexdigest()[:32]


async def ask(team: Any, session: Any, question: str, profile: FanProfile) -> PunditAnswer:
    """Answer one fan question. ``team`` is the AgentTeam, ``session`` the live MatchSession."""
    from matchlens.agents.pipeline import call_agent  # the pipeline imports this module's siblings

    engine: MatchEngine = session.engine
    question = question.strip()[:MAX_QUESTION]
    key = _key(engine, question, profile)
    if (cached := await session.recorder.fan_cached(key)) is not None:
        return PunditAnswer.model_validate(cached)
    started = time.perf_counter()
    intent, player_id, team_id = route(question, engine)
    if not player_id and intent == "player" and profile.player_id:
        player_id = profile.player_id
    # "How many yellow cards?" is about the match; only a team named in the question narrows it.
    if (not team_id and intent != "discipline"
            and profile.club_id in (engine.meta.home.team_id, engine.meta.away.team_id)):
        team_id = profile.club_id
    pack = pundit_pack(engine, session.insights, intent, player_id, team_id, question)
    lang = profile.language.value
    draft: PunditDraft | None = None
    report = FactCheckReport(status=FactCheckStatus.DATA_ONLY)
    if intent != "off_topic":
        payload = {"task": "pundit", "question": question, "context": pack, "language": lang,
                   "mode": profile.mode.value}
        draft, err = await call_agent(team.pundit, payload, PunditDraft, timeout=PUNDIT_TIMEOUT_S)
        if draft is not None:
            outcome = check_text(engine, pack, [draft.answer, *draft.points], draft.claims)
            report = outcome.report
            if outcome.problems:
                report.actions += ["; ".join(outcome.problems), "agent answer blocked; data-only answer shown"]
                draft = None
        else:
            report.actions.append(f"fallback: {err}")
    source: Literal["agent", "data"] = "agent" if draft is not None and team.label != "offline-templates" else "data"
    agents = ["pundit_router", *(["pundit", "fact_checker"] if draft is not None else [])]
    if draft is None:
        draft = template(pack, lang)
    by_id = {mm["signal_id"]: mm for mm in pack["moments"]}
    cited = [CitedMoment(insight_id=sid, minute_label=by_id[sid]["minute"], type=by_id[sid]["type"],
                         team_id=by_id[sid]["team_id"]) for sid in draft.cited_moments if sid in by_id][:3]
    result = PunditAnswer(
        question=question, intent=intent, answer=draft.answer, points=draft.points[:3],
        key_numbers=draft.key_numbers[:3], cited_moments=cited, minute_label=pack["match"]["minute"],
        profile=profile, fact_check=report, source=source, agents=agents,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    # A live model that was blocked or failed may succeed on a retry, so that fallback is not remembered.
    if source == "agent" or team.label == "offline-templates":
        session.recorder.fan_output(key, "pundit", result.model_dump(mode="json"))
    session.recorder.agent_run({"kind": "pundit", "outcome": source, "model": team.label,
                                "fact_check": report.status.value, "intent": intent,
                                "latency_ms": result.latency_ms})
    return result

"""Deterministic, data-only text (FR-045 fallback).

These functions turn a context pack into drafts without any model. They are
used for (1) instant data cards before agent output arrives, (2) low-priority
signals that do not justify an LLM call, (3) the fallback when a model call
fails, times out or is blocked by the Fact-Checker, and (4) the offline model
client used in tests and keyless local runs. Every number comes straight from
the pack, so this text always passes fact-checking.
"""

from __future__ import annotations

from typing import Any

from matchlens.agents.coach import template_draft as coach_template
from matchlens.agents.contracts import (
    AgentClaim,
    CatchupDraft,
    ClubAngle,
    KeyNumber,
    Language,
    Mode,
    NarrativeDraft,
    OverlayKind,
    OverlayPlan,
    PersonalisedDraft,
    RecapDraft,
    RewriteDraft,
    TacticalDraft,
    Variant,
)

Pack = dict[str, Any]

# ---------------------------------------------------------------- phrase table
# (headline, body) per signal type and language. Placeholders come from _vars().

PHRASES: dict[str, dict[str, tuple[str, str]]] = {
    "goal": {
        "en": ("GOAL! {player} scores for {team}", "{score_line} at {minute}. Chance quality {xg} xG."),
        "hi": ("गोल! {team} के लिए {player} का गोल", "{minute} पर स्कोर {score_line}। मौके की गुणवत्ता {xg} xG।"),
        "es": ("¡GOL! {player} marca para {team}", "{score_line} en el {minute}. Calidad de la ocasión: {xg} xG."),
    },
    "big_chance": {
        "en": ("Big chance for {team}", "{player} had a {xg} xG chance at {minute}, but it was {outcome}."),
        "hi": ("{team} के लिए बड़ा मौका", "{minute} पर {player} के पास {xg} xG का मौका था, पर नतीजा: {outcome}।"),
        "es": ("Gran ocasión para {team}", "{player} tuvo una ocasión de {xg} xG en el {minute}: {outcome}."),
    },
    "big_save": {
        "en": ("Big save from {player}", "{player} kept out a {xg_faced} xG chance at {minute}."),
        "hi": ("{player} का शानदार बचाव", "{minute} पर {player} ने {xg_faced} xG का मौका रोका।"),
        "es": ("Gran parada de {player}", "{player} detuvo una ocasión de {xg_faced} xG en el {minute}."),
    },
    "card": {
        "en": ("{card_name} for {player}", "{team} now have {on_pitch} players on the pitch ({minute})."),
        "hi": ("{player} को {card_name}", "{minute} पर {team} के मैदान में अब {on_pitch} खिलाड़ी हैं।"),
        "es": ("{card_name} para {player}", "{team} juega ahora con {on_pitch} jugadores ({minute})."),
    },
    "substitution": {
        "en": ("{on_name} on for {off_name}", "{team} make a change at {minute}."),
        "hi": ("{off_name} की जगह {on_name}", "{minute} पर {team} ने बदलाव किया।"),
        "es": ("Entra {on_name} por {off_name}", "Cambio de {team} en el {minute}."),
    },
    "momentum_swing": {
        "en": ("Momentum swings to {team}", "The momentum index moved from {from_index} to {to_index} since {since}."),
        "hi": ("लय {team} की ओर", "{since} से मोमेंटम इंडेक्स {from_index} से {to_index} पर पहुँचा।"),
        "es": ("El impulso cambia hacia {team}", "El índice de impulso pasó de {from_index} a {to_index} desde el {since}."),
    },
    "pressure_spell": {
        "en": ("{team} turning up the pressure", "{pressing_actions} pressing actions and {turnovers_won} turnovers won in a short spell."),
        "hi": ("{team} का दबाव बढ़ा", "थोड़े समय में {pressing_actions} प्रेसिंग एक्शन और {turnovers_won} टर्नओवर।"),
        "es": ("{team} aprieta", "{pressing_actions} acciones de presión y {turnovers_won} recuperaciones en poco tiempo."),
    },
    "chaos_spell": {
        "en": ("End-to-end chaos", "{turnovers_per_min} turnovers per minute: neither side can keep the ball."),
        "hi": ("खेल में अफरा-तफरी", "प्रति मिनट {turnovers_per_min} टर्नओवर: कोई टीम गेंद नहीं रख पा रही।"),
        "es": ("Partido caótico", "{turnovers_per_min} pérdidas por minuto: nadie retiene el balón."),
    },
    "control_spell": {
        "en": ("{team} in control", "{pass_share_pct}% of recent passes at {pass_accuracy_pct}% accuracy."),
        "hi": ("{team} का नियंत्रण", "हाल के {pass_share_pct}% पास, {pass_accuracy_pct}% सटीकता के साथ।"),
        "es": ("{team} tiene el control", "{pass_share_pct}% de los pases recientes con {pass_accuracy_pct}% de acierto."),
    },
    "milestone": {
        "en": ("{player}: {value} career goals", "A landmark goal at {minute}."),
        "hi": ("{player}: करियर के {value} गोल", "{minute} पर एक यादगार गोल।"),
        "es": ("{player}: {value} goles en su carrera", "Un gol histórico en el {minute}."),
    },
    "speed_badge": {
        "en": ("{player} hits {speed_kmh} km/h", "Fastest sprints are tracked live ({minute})."),
        "hi": ("{player} की रफ़्तार {speed_kmh} किमी/घं", "{minute} पर तेज़ स्प्रिंट।"),
        "es": ("{player} alcanza {speed_kmh} km/h", "Sprint registrado en el {minute}."),
    },
    "distance_badge": {
        "en": ("{player} has covered {distance_km} km", "Workrate milestone at {minute}."),
        "hi": ("{player} ने {distance_km} किमी दौड़ लगाई", "{minute} तक की मेहनत।"),
        "es": ("{player} ya recorrió {distance_km} km", "Hito de esfuerzo en el {minute}."),
    },
    "shot_speed_badge": {
        "en": ("{player} strikes at {shot_speed_kmh} km/h", "A powerful effort at {minute}."),
        "hi": ("{player} का {shot_speed_kmh} किमी/घं का शॉट", "{minute} पर दमदार शॉट।"),
        "es": ("Disparo de {player} a {shot_speed_kmh} km/h", "Un remate potente en el {minute}."),
    },
    "hard_pass": {
        "en": ("Elite pass from {player}", "Difficulty {difficulty}/100 over {distance_m} m ({minute})."),
        "hi": ("{player} का बेहतरीन पास", "{minute} पर {distance_m} मीटर का पास, कठिनाई {difficulty}/100।"),
        "es": ("Pase de élite de {player}", "Dificultad {difficulty}/100 en {distance_m} m ({minute})."),
    },
    "correction": {
        "en": ("Correction at {minute}", "Updated: {previous_name} is now {value_name}."),
        "hi": ("{minute} पर सुधार", "अपडेट: {previous_name} की जगह अब {value_name}।"),
        "es": ("Corrección en el {minute}", "Actualizado: {previous_name} pasa a ser {value_name}."),
    },
    "kickoff": {
        "en": ("Kick-off", "{home} v {away} is under way."),
        "hi": ("किक-ऑफ़", "{home} बनाम {away} शुरू।"),
        "es": ("Comienza el partido", "{home} contra {away} ya está en marcha."),
    },
    "half_time": {
        "en": ("Half-time", "{score_line} at the break."),
        "hi": ("हाफ़-टाइम", "ब्रेक पर स्कोर {score_line}।"),
        "es": ("Descanso", "{score_line} al descanso."),
    },
    "full_time": {
        "en": ("Full-time", "Final score: {score_line}."),
        "hi": ("फ़ुल-टाइम", "अंतिम स्कोर: {score_line}।"),
        "es": ("Final del partido", "Resultado final: {score_line}."),
    },
}

CARD_NAMES = {
    "yellow": {"en": "Yellow card", "hi": "पीला कार्ड", "es": "Tarjeta amarilla"},
    "second_yellow": {"en": "Second yellow, red card", "hi": "दूसरा पीला, लाल कार्ड", "es": "Doble amarilla, expulsión"},
    "red": {"en": "Red card", "hi": "लाल कार्ड", "es": "Tarjeta roja"},
}
OUTCOMES = {
    "saved": {"en": "saved", "hi": "बचा लिया गया", "es": "atajada"},
    "off_target": {"en": "off target", "hi": "निशाने से बाहर", "es": "fuera"},
    "blocked": {"en": "blocked", "hi": "ब्लॉक", "es": "bloqueada"},
}
BOTH_SIDES = {"en": "Both sides", "hi": "दोनों टीमें", "es": "Ambos equipos"}


class _Safe(dict):  # leaves unknown placeholders readable instead of raising
    def __missing__(self, key: str) -> str:
        return "-"


def phrase_key(pack: Pack) -> str:
    sig = pack["signal"]
    if sig["type"] == "period":
        return str(sig["facts"].get("phase", "kickoff"))
    return sig["type"]


def _player_names(pack: Pack) -> dict[str, str]:
    return {p["player_id"]: p["name"] for p in pack.get("players", [])}


def _team_names(pack: Pack) -> dict[str, str]:
    m = pack["match"]
    return {m["home"]["team_id"]: m["home"]["name"], m["away"]["team_id"]: m["away"]["name"]}


def score_line(pack: Pack, score: dict[str, int] | None = None) -> str:
    m = pack["match"]
    s = score or m["score"]
    h, a = m["home"]["team_id"], m["away"]["team_id"]
    return f"{m['home']['name']} {s[h]}-{s[a]} {m['away']['name']}"


def _vars(pack: Pack, lang: str) -> _Safe:
    sig = pack["signal"]
    facts = sig["facts"]
    names = _player_names(pack)
    teams = _team_names(pack)
    main = sig["player_ids"][0] if sig["player_ids"] else facts.get("scorer")
    team_id = sig["team_id"]
    v = _Safe({k: v for k, v in facts.items() if v is not None})
    m = pack["match"]
    score = m["score"]
    if sig["type"] == "goal" and "home_score" in facts:
        score = {m["home"]["team_id"]: facts["home_score"], m["away"]["team_id"]: facts["away_score"]}
    if sig["type"] == "period" and "home_score" in facts:
        score = {m["home"]["team_id"]: facts["home_score"], m["away"]["team_id"]: facts["away_score"]}
    v.update(
        player=names.get(str(facts.get("scorer") or main), "-"),
        team=teams.get(team_id or "", BOTH_SIDES.get(lang, "Both sides")),
        minute=sig["minute"],
        home=m["home"]["name"], away=m["away"]["name"],
        score_line=score_line(pack, score),
        since=facts.get("since_minute_label", "-"),
        on_name=names.get(str(facts.get("on")), "-"), off_name=names.get(str(facts.get("off")), "-"),
        previous_name=names.get(str(facts.get("previous")), str(facts.get("previous", "-"))),
        value_name=names.get(str(facts.get("value")), str(facts.get("value", "-"))),
        card_name=CARD_NAMES.get(str(facts.get("card")), CARD_NAMES["yellow"])[lang],
        outcome=OUTCOMES.get(str(facts.get("outcome")), {}).get(lang, str(facts.get("outcome", "-"))),
        on_pitch=facts.get("players_on_pitch", "-"),
    )
    return v


def first_sentence(text: str) -> str:
    for sep in (". ", "। "):
        if sep in text:
            return text.split(sep)[0] + sep.strip()
    return text


def data_text(pack: Pack, lang: str = "en") -> tuple[str, str]:
    key = phrase_key(pack)
    table = PHRASES.get(key) or {"en": ("{team}: {minute}", "")}
    head, body = table.get(lang, table["en"])
    v = _vars(pack, lang)
    return head.format_map(v), body.format_map(v)


def key_numbers(pack: Pack) -> list[KeyNumber]:
    facts = pack["signal"]["facts"]
    labels = {
        "xg": ("xG", None), "xg_faced": ("xG faced", None), "shot_speed_kmh": ("Shot speed", "km/h"),
        "speed_kmh": ("Top speed", "km/h"), "distance_km": ("Distance", "km"), "difficulty": ("Pass difficulty", "/100"),
        "distance_m": ("Pass length", "m"), "to_index": ("Momentum", None), "pressing_actions": ("Pressing actions", None),
        "turnovers_won": ("Turnovers won", None), "turnovers_per_min": ("Turnovers/min", None),
        "pass_share_pct": ("Pass share", "%"), "value": ("Career goals", None), "players_on_pitch": ("On pitch", None),
    }
    out = []
    for k, (label, unit) in labels.items():
        val = facts.get(k)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out.append(KeyNumber(label=label, value=f"{val:g}", unit=unit))
    return out[:3]


def _score_claim(pack: Pack) -> AgentClaim:
    m = pack["match"]
    return AgentClaim(kind="score", text=score_line(pack), subject_id=None, stat=None, value=None, event_id=None,
                      player_id=None, home=m["score"][m["home"]["team_id"]], away=m["score"][m["away"]["team_id"]])


# ---------------------------------------------------------------- drafts


def tactical(pack: Pack) -> TacticalDraft:
    head, body = data_text(pack)
    return TacticalDraft(headline=head, explanation=body, why_it_matters=f"Score: {score_line(pack)}.",
                         key_numbers=key_numbers(pack), claims=[_score_claim(pack)])


def narrative(pack: Pack) -> NarrativeDraft:
    head, body = data_text(pack)
    return NarrativeDraft(headline=head, narrative=body, storyline=None, claims=[_score_claim(pack)])


OVERLAY_FOR = {
    "goal": OverlayKind.LOWER_THIRD, "big_chance": OverlayKind.SHOT_CARD, "big_save": OverlayKind.SHOT_CARD,
    "card": OverlayKind.LOWER_THIRD, "substitution": OverlayKind.LOWER_THIRD,
    "momentum_swing": OverlayKind.MOMENTUM_BAR, "pressure_spell": OverlayKind.MOMENTUM_BAR,
    "chaos_spell": OverlayKind.STAT_CARD, "control_spell": OverlayKind.STAT_CARD,
    "milestone": OverlayKind.MILESTONE_BANNER, "speed_badge": OverlayKind.PLAYER_CARD,
    "distance_badge": OverlayKind.PLAYER_CARD, "shot_speed_badge": OverlayKind.PLAYER_CARD,
    "hard_pass": OverlayKind.STAT_CARD, "correction": OverlayKind.CORRECTION_BANNER, "period": OverlayKind.LOWER_THIRD,
}


def overlay_plan(pack: Pack, headline: str | None = None, body: str | None = None) -> OverlayPlan:
    head, sub = data_text(pack)
    sig = pack["signal"]
    title = (headline or head)[:32]
    subtitle = (body or sub)[:60] or None
    return OverlayPlan(kind=OVERLAY_FOR.get(sig["type"], OverlayKind.LOWER_THIRD), title=title, subtitle=subtitle,
                       stats=key_numbers(pack), duration_s=8 if sig["type"] in ("goal", "milestone") else 6,
                       focus_player_id=sig["player_ids"][0] if sig["player_ids"] else None)


def personalised(pack: Pack, languages: list[str] | None = None) -> PersonalisedDraft:
    langs = languages or [x.value for x in Language]
    variants, angles = [], []
    teams = _team_names(pack)
    for lang in langs:
        head, body = data_text(pack, lang)
        for mode in Mode:
            text = body if mode != Mode.CASUAL else first_sentence(body)
            variants.append(Variant(mode=mode, language=Language(lang), headline=head, body=text))
        for tid in teams:
            angles.append(ClubAngle(team_id=tid, language=Language(lang), text=head))
    return PersonalisedDraft(variants=variants, club_angles=angles)


WINDOW_TEXT = {
    "was": {"en": "was", "hi": "पहले", "es": "antes"},
    "no_change": {"en": "no change", "hi": "कोई बदलाव नहीं", "es": "sin cambios"},
    "none": {"en": "No key moments in this window.", "hi": "इस दौरान कोई बड़ा पल नहीं।",
             "es": "Sin momentos clave en este intervalo."},
    "on_top": {"en": "{team} have been on top.", "hi": "{team} का दबदबा रहा।", "es": "{team} ha dominado."},
    "even": {"en": "Neither side has been on top.", "hi": "कोई टीम हावी नहीं रही।", "es": "Ningún equipo ha dominado."},
}


def moment_text(pack: Pack, moment: dict[str, Any], lang: str) -> str:
    """One localised line for a window moment, rendered like a live card."""
    sub = {"match": pack["match"], "players": pack.get("players", []),
           "signal": {"type": moment["type"], "minute": moment["minute"], "team_id": moment["team_id"],
                      "player_ids": moment["player_ids"], "facts": moment["facts"]}}
    head, _ = data_text(sub, lang)
    return f"{moment['minute']} {head}"


def catchup(pack: Pack, lang: str = "en") -> CatchupDraft:
    m = pack["match"]
    teams = _team_names(pack)
    start = pack["window"]["score_at_start"]
    h, a = m["home"]["team_id"], m["away"]["team_id"]
    moments = sorted(pack["moments"][:5], key=lambda mo: mo["elapsed"])
    bullets = [moment_text(pack, mo, lang) for mo in moments] or [WINDOW_TEXT["none"][lang]]
    idx = pack["momentum"]["now"]
    top = teams[h] if idx > 10 else teams[a] if idx < -10 else None
    changed = start != m["score"]
    headline = score_line(pack) + (f" ({WINDOW_TEXT['was'][lang]} {start[h]}-{start[a]})" if changed
                                   else f", {WINDOW_TEXT['no_change'][lang]}")
    first = pack["moments"][0] if pack["moments"] else None
    return CatchupDraft(
        headline=headline, bullets=bullets,
        momentum_summary=WINDOW_TEXT["on_top"][lang].format(team=top) if top else WINDOW_TEXT["even"][lang],
        spotlight_player_id=first["player_ids"][0] if first and first["player_ids"] else None,
        claims=[_score_claim(pack)],
    )


def recap(pack: Pack, lang: str = "en") -> RecapDraft:
    c = catchup(pack, lang)
    goals = [mo for mo in pack["moments"] if mo["type"] == "goal"]
    scorers: dict[str, int] = {}
    for g in goals:
        if g["player_ids"]:
            scorers[g["player_ids"][0]] = scorers.get(g["player_ids"][0], 0) + 1
    potm = max(scorers, key=lambda p: scorers[p]) if scorers else None
    return RecapDraft(title=score_line(pack), summary=" ".join(c.bullets), key_moments=c.bullets,
                      turning_point=c.momentum_summary, player_of_the_match_id=potm, claims=c.claims)


def rewrite(payload: Pack) -> RewriteDraft:
    """Offline rewrite: fall back to the data-only text for the signal."""
    head, body = data_text(payload["context"])
    return RewriteDraft(headline=head, body=body, claims=[_score_claim(payload["context"])])


TASKS = {
    "tactical": lambda p: tactical(p["context"]),
    "narrative": lambda p: narrative(p["context"]),
    "overlay": lambda p: overlay_plan(p["context"], p.get("headline"), p.get("body")),
    "personalise": lambda p: personalised(p["context"], p.get("languages")),
    "catchup": lambda p: catchup(p["context"], p.get("language", "en")),
    "recap": lambda p: recap(p["context"], p.get("language", "en")),
    "rewrite": rewrite,
    "coach": lambda p: coach_template(p["context"]),
}

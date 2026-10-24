"""Coach persona: short ranked tactical briefs for one side (BRD §6.2, now in scope).

Same coordinator-and-specialist pattern as the live insights:

* The coach coordinator (code, no model) scans the stats engine for problems and opportunities over the last
  15 minutes, scores their urgency, rates its confidence from the strength of the evidence and keeps the top three.
  Ranking, confidence and evidence never come from a model.
* The Coach agent words each item as a recommendation with its trade-off, using only the facts the coordinator
  supplied, and returns structured claims.
* The Fact-Checker verifies the text. If it fails, or the agent is unavailable, the data-only wording is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from matchlens.agents.context import TEAM_KEYS, match_header, player_brief
from matchlens.agents.contracts import AgentClaim, FactCheckReport, KeyNumber, _Strict
from matchlens.domain.models import EventType
from matchlens.interpret.engine import MatchEngine

WINDOW_MIN = 15
MAX_ITEMS = 3
Confidence = Literal["high", "medium", "low"]

# (title, evidence, recommendation, trade-off) per issue kind and language. Placeholders come from Issue.vars.
TEXT: dict[str, dict[str, tuple[str, str, str, str]]] = {
    "protect_box": {
        "en": ("Protect the box",
               "{opp} have {opp_shots} shots worth {opp_xg} xG in the last {window} minutes.",
               ("Drop the back line a few metres and keep a holding midfielder in front of the centre-backs "
               "until the pressure eases."),
               "You give up territory and press from deeper, so counter-attacks become your main outlet."),
        "hi": ("बॉक्स को सुरक्षित करें",
               "पिछले {window} मिनट में {opp} ने {opp_shots} शॉट लिए, जिनका मूल्य {opp_xg} xG है।",
               "दबाव कम होने तक डिफेंस लाइन को थोड़ा पीछे रखें और सेंटर-बैक के आगे एक होल्डिंग मिडफ़ील्डर रखें।",
               "आप मैदान का हिस्सा छोड़ते हैं और प्रेस पीछे से शुरू होगा, इसलिए काउंटर-अटैक ही मुख्य रास्ता बचेगा।"),
        "es": ("Proteger el área",
               "{opp} suma {opp_shots} tiros por valor de {opp_xg} xG en los últimos {window} minutos.",
               ("Retrasa unos metros la línea defensiva y mantén un mediocentro delante de los centrales hasta que "
               "baje la presión."),
               "Cedes terreno y presionas más atrás, así que el contraataque pasa a ser tu salida principal."),
    },
    "momentum_against": {
        "en": ("Take the sting out of the game",
               ("{opp} have been on top: momentum is {momentum} in their favour, and against you for {share}% of "
               "the last 10 minutes."),
               "Slow the tempo: keep the ball for longer spells and have the full-backs hold their positions.",
               "Fewer runners in behind, so chances will be scarce until the game settles."),
        "hi": ("मैच की रफ़्तार धीमी करें",
               ("{opp} हावी है: मोमेंटम {momentum} उनके पक्ष में है, और पिछले 10 मिनट के {share}% समय में आपके "
               "ख़िलाफ़ रहा।"),
               "रफ़्तार धीमी करें: गेंद को ज़्यादा देर तक अपने पास रखें और फ़ुल-बैक अपनी जगह पर टिके रहें।",
               "पीछे दौड़ने वाले खिलाड़ी कम होंगे, इसलिए खेल संभलने तक मौके कम बनेंगे।"),
        "es": ("Quitarle ritmo al partido",
               ("{opp} domina: el impulso es de {momentum} a su favor, y en contra tuya durante el {share}% de los "
               "últimos 10 minutos."),
               "Baja el ritmo: conserva el balón en posesiones más largas y que los laterales mantengan su posición.",
               "Menos jugadores atacando el espacio, así que habrá pocas ocasiones hasta que el partido se calme."),
    },
    "blunt_attack": {
        "en": ("Turn possession into chances",
               ("{team} have {possession}% of the passes in the last {window} minutes but only {xg} xG from "
               "{shots} shots."),
               "Commit more runners into the box and get the ball wide earlier for crosses and cut-backs.",
               "More players forward leaves fewer behind the ball if the attack breaks down."),
        "hi": ("कब्ज़े को मौकों में बदलें",
               "पिछले {window} मिनट में {team} के पास {possession}% पास हैं, पर {shots} शॉट से सिर्फ़ {xg} xG बना।",
               "बॉक्स में ज़्यादा खिलाड़ी भेजें और क्रॉस व कट-बैक के लिए गेंद जल्दी किनारे पर ले जाएँ।",
               "आगे ज़्यादा खिलाड़ी होने से हमला टूटने पर पीछे कम खिलाड़ी बचेंगे।"),
        "es": ("Convertir la posesión en ocasiones",
               ("{team} tiene el {possession}% de los pases en los últimos {window} minutos, pero solo {xg} xG en "
               "{shots} tiros."),
               "Mete más llegadores en el área y abre antes a banda para centros y pases atrás.",
               "Con más gente arriba quedan menos jugadores por detrás del balón si se pierde el ataque."),
    },
    "bypass_press": {
        "en": ("Play around the press",
               "{team} lost the ball {turnovers} times in their own half in the last {window} minutes.",
               "Go longer from the back to bypass the press and win the second balls in midfield.",
               "Long passes are completed far less often than short ones, so expect to give the ball away more."),
        "hi": ("प्रेस से बचकर खेलें",
               "पिछले {window} मिनट में {team} ने अपने हाफ़ में {turnovers} बार गेंद गँवाई।",
               "प्रेस से बचने के लिए पीछे से लंबी गेंद खेलें और मिडफ़ील्ड में दूसरी गेंद जीतें।",
               "लंबे पास छोटे पासों से कहीं कम पूरे होते हैं, इसलिए गेंद ज़्यादा गँवाने की उम्मीद रखें।"),
        "es": ("Saltar la presión",
               "{team} perdió el balón {turnovers} veces en su propio campo en los últimos {window} minutos.",
               "Juega más directo desde atrás para saltar la presión y gana las segundas jugadas en el centro.",
               "Los pases largos se completan mucho menos que los cortos, así que perderás más balones."),
    },
    "card_risk": {
        "en": ("Manage the booked player",
               "{player} is on a yellow card and has committed {fouls} fouls.",
               "Substitute {player}, or move them away from the opponent's most direct dribbler.",
               "You lose {player}'s influence, and it uses one of your changes."),
        "hi": ("बुक हुए खिलाड़ी को सँभालें",
               "{player} को पीला कार्ड मिल चुका है और उन्होंने {fouls} फ़ाउल किए हैं।",
               "{player} को बदलें, या उन्हें विरोधी के सबसे सीधे ड्रिबलर से दूर रखें।",
               "{player} का असर खो जाएगा, और एक बदलाव ख़र्च होगा।"),
        "es": ("Gestionar al amonestado",
               "{player} tiene tarjeta amarilla y ha cometido {fouls} faltas.",
               "Sustituye a {player} o aléjalo del regateador más directo del rival.",
               "Pierdes la influencia de {player} y gastas un cambio."),
    },
    "fresh_legs": {
        "en": ("Fresh legs soon",
               "{player} has covered {distance} km, the most in the {team} side.",
               "Plan a change for {player} in the next ten minutes, before the final push.",
               "A change now spends a substitution you may want later."),
        "hi": ("जल्द ताज़ा खिलाड़ी",
               "{player} ने {distance} किमी दौड़ लगाई है, जो {team} में सबसे ज़्यादा है।",
               "आख़िरी दौर से पहले, अगले दस मिनट में {player} की जगह बदलाव की योजना बनाएँ।",
               "अभी बदलाव करने से एक ऐसा सब्स्टिट्यूशन ख़र्च होगा जो बाद में काम आ सकता था।"),
        "es": ("Piernas frescas pronto",
               "{player} ha recorrido {distance} km, el que más en {team}.",
               "Prepara un cambio para {player} en los próximos diez minutos, antes del tramo final.",
               "Un cambio ahora gasta una sustitución que quizá necesites después."),
    },
    "extra_man": {
        "en": ("Use the extra player",
               "{team} have {players} players against {opp_players}.",
               "Stretch the pitch with both full-backs high and move the ball quickly from side to side.",
               "With both full-backs pushed on, there is space behind for counter-attacks."),
        "hi": ("अतिरिक्त खिलाड़ी का फ़ायदा उठाएँ",
               "{team} के {players} खिलाड़ियों के सामने {opp_players} खिलाड़ी हैं।",
               "दोनों फ़ुल-बैक को ऊपर रखकर मैदान चौड़ा करें और गेंद तेज़ी से एक ओर से दूसरी ओर ले जाएँ।",
               "दोनों फ़ुल-बैक आगे होने से पीछे काउंटर-अटैक के लिए जगह बनती है।"),
        "es": ("Aprovechar la superioridad",
               "{team} juega con {players} contra {opp_players}.",
               "Abre el campo con los dos laterales altos y mueve el balón rápido de banda a banda.",
               "Con los dos laterales arriba queda espacio a la espalda para el contraataque."),
    },
    "man_down": {
        "en": ("Compact with ten",
               "{team} have {players} players against {opp_players}.",
               "Stay compact in two banks of four and break quickly through your fastest forward.",
               "You will have less of the ball and depend on a few chances."),
        "hi": ("कम खिलाड़ियों के साथ सिमटकर खेलें",
               "{team} के {players} खिलाड़ियों के सामने {opp_players} खिलाड़ी हैं।",
               "चार-चार की दो पंक्तियों में सिमटे रहें और सबसे तेज़ फ़ॉरवर्ड के ज़रिए तेज़ी से जवाबी हमला करें।",
               "गेंद आपके पास कम रहेगी और आप कुछ ही मौकों पर निर्भर रहेंगे।"),
        "es": ("Bloque compacto con uno menos",
               "{team} juega con {players} contra {opp_players}.",
               "Mantente compacto en dos líneas de cuatro y sal rápido por tu delantero más veloz.",
               "Tendrás menos el balón y dependerás de pocas ocasiones."),
    },
    "chase_game": {
        "en": ("Chase the game",
               "{team} trail {score_line} with {minutes_left} minutes of normal time left.",
               "Go to two strikers, push the full-backs on and get the ball into the box early.",
               "You will be more open at the back, so conceding again becomes more likely."),
        "hi": ("मैच का पीछा करें",
               "{team} {score_line} से पीछे है और सामान्य समय के {minutes_left} मिनट बचे हैं।",
               "दो स्ट्राइकर उतारें, फ़ुल-बैक को आगे भेजें और गेंद जल्दी बॉक्स में डालें।",
               "पीछे जगह ज़्यादा खुलेगी, इसलिए एक और गोल खाने का ख़तरा बढ़ेगा।"),
        "es": ("Ir a por el partido",
               "{team} pierde {score_line} y quedan {minutes_left} minutos de tiempo reglamentario.",
               "Juega con dos delanteros, sube los laterales y mete el balón al área pronto.",
               "Quedarás más expuesto atrás, así que encajar otro gol será más probable."),
    },
    "see_it_out": {
        "en": ("See the game out",
               "{team} lead {score_line} with {minutes_left} minutes of normal time left.",
               "Keep the ball in the opponent's half and use your remaining changes to slow the game.",
               "Sitting deeper hands the initiative to {opp}."),
        "hi": ("मैच को संभालकर ख़त्म करें",
               "{team} {score_line} से आगे है और सामान्य समय के {minutes_left} मिनट बचे हैं।",
               "गेंद को विरोधी के हाफ़ में रखें और बचे हुए बदलावों से खेल की रफ़्तार धीमी करें।",
               "पीछे हटकर खेलने से पहल {opp} के हाथ में चली जाती है।"),
        "es": ("Cerrar el partido",
               "{team} gana {score_line} y quedan {minutes_left} minutos de tiempo reglamentario.",
               "Mantén el balón en campo rival y usa los cambios que te quedan para frenar el partido.",
               "Replegarte le cede la iniciativa a {opp}."),
    },
    "danger_man": {
        "en": ("Stop their danger man",
               "{player} has {shots} shots worth {xg} xG so far.",
               "Have a holding midfielder track {player} and cut off the passes into them between the lines.",
               "That midfielder will join fewer of your own attacks."),
        "hi": ("उनके सबसे ख़तरनाक खिलाड़ी को रोकें",
               "{player} अब तक {shots} शॉट ले चुके हैं, जिनका मूल्य {xg} xG है।",
               "एक होल्डिंग मिडफ़ील्डर {player} पर नज़र रखे और लाइनों के बीच उन तक पहुँचने वाले पास रोके।",
               "वह मिडफ़ील्डर आपके अपने हमलों में कम शामिल होगा।"),
        "es": ("Frenar a su jugador más peligroso",
               "{player} lleva {shots} tiros por valor de {xg} xG.",
               "Que un mediocentro siga a {player} y corte los pases que le llegan entre líneas.",
               "Ese mediocentro participará menos en tus ataques."),
    },
    "steady": {
        "en": ("Keep the shape",
               "No clear problem for {team} in the last {window} minutes.",
               "Stick with the plan and keep the distances between the lines short.",
               "Standing still can let {opp} adapt first."),
        "hi": ("ढाँचा बनाए रखें",
               "पिछले {window} मिनट में {team} के लिए कोई साफ़ समस्या नहीं दिखी।",
               "योजना पर टिके रहें और लाइनों के बीच की दूरी कम रखें।",
               "कुछ न बदलने से {opp} पहले बदलाव कर सकता है।"),
        "es": ("Mantener la estructura",
               "Ningún problema claro para {team} en los últimos {window} minutos.",
               "Sigue con el plan y mantén cortas las distancias entre líneas.",
               "No mover nada puede dejar que {opp} se adapte antes."),
    },
}


@dataclass
class Issue:
    """One candidate item, found and scored by the coordinator."""

    kind: str
    urgency: float  # 0..1, ranks the brief
    confidence: Confidence
    vars: dict[str, Any]
    key_numbers: list[KeyNumber] = field(default_factory=list)
    evidence_event_ids: list[str] = field(default_factory=list)
    player_ids: list[str] = field(default_factory=list)

    def text(self, lang: str) -> tuple[str, str, str, str]:
        table = TEXT[self.kind]
        return tuple(t.format(**self.vars) for t in table.get(lang, table["en"]))  # type: ignore[return-value]


# ------------------------------------------------------------------ coordinator


def _band(value: float, high: float, medium: float) -> Confidence:
    return "high" if value >= high else "medium" if value >= medium else "low"


class _View:
    """The match from one side's bench: window counts plus whole-match stats."""

    def __init__(self, engine: MatchEngine, team_id: str) -> None:
        self.e = engine
        self.team = team_id
        self.home = team_id == engine.meta.home.team_id
        self.opp = engine.meta.away.team_id if self.home else engine.meta.home.team_id
        self.now = engine.c.elapsed
        self.minute = int(self.now // 60)
        self.since = max(0.0, self.now - WINDOW_MIN * 60)
        self.window = self.e.events(since=self.since)
        self.stats = {t: engine.team_stats(t) for t in (self.team, self.opp)}

    def name(self, team_id: str) -> str:
        return self.e.meta.team(team_id).name

    def player(self, pid: str) -> str:
        return self.e.meta.players[pid].name

    def shots(self, team_id: str) -> list:
        return [e for e in self.window if e.type == EventType.SHOT and e.team_id == team_id]

    def own_half(self, x: float) -> bool:
        return x < 52.5 if self.home else x > 52.5

    def base_vars(self) -> dict[str, Any]:
        score = self.e.score()
        h, a = self.e.meta.home, self.e.meta.away
        return {"team": self.name(self.team), "opp": self.name(self.opp), "window": WINDOW_MIN,
                "score_line": f"{h.name} {score[h.team_id]}-{score[a.team_id]} {a.name}"}


def _protect_box(v: _View) -> Issue | None:
    shots = v.shots(v.opp)
    xg = round(sum(float(s.attrs.get("xg", 0)) for s in shots), 2)
    if len(shots) < 4 and xg < 0.6:
        return None
    return Issue("protect_box", min(1.0, xg / 1.2 + len(shots) / 12), _band(max(xg, len(shots) / 6), 1.0, 0.6),
                 {"opp_shots": len(shots), "opp_xg": xg},
                 [KeyNumber(label="Shots against", value=str(len(shots)), unit=f"last {WINDOW_MIN} min"),
                  KeyNumber(label="xG against", value=f"{xg:.2f}", unit=None)],
                 [s.event_id for s in shots][-6:])


def _momentum_against(v: _View) -> Issue | None:
    sign = 1 if v.home else -1
    now = v.e.momentum_now() * sign
    if now > -30:
        return None
    recent = [p.index * sign for p in v.e.momentum_series() if p.elapsed >= v.now - 600]
    share = round(100 * sum(i < -15 for i in recent) / len(recent)) if recent else 0
    conf: Confidence = "high" if share >= 70 and now <= -50 else "medium" if share >= 50 else "low"
    momentum = round(abs(now))
    return Issue("momentum_against", min(1.0, abs(now) / 100 * (0.5 + share / 200)), conf,
                 {"momentum": momentum, "share": share},
                 [KeyNumber(label="Momentum against", value=str(momentum), unit="of 100"),
                  KeyNumber(label="Time on the back foot", value=f"{share}%", unit="last 10 min")])


def _blunt_attack(v: _View) -> Issue | None:
    passes = [e for e in v.window if e.type == EventType.PASS]
    ours = sum(e.team_id == v.team for e in passes)
    if len(passes) < 60:
        return None
    possession = round(100 * ours / len(passes))
    shots = v.shots(v.team)
    xg = round(sum(float(s.attrs.get("xg", 0)) for s in shots), 2)
    if possession < 55 or xg >= 0.3:
        return None
    return Issue("blunt_attack", 0.45 + (possession - 55) / 100, _band(possession, 65, 58),
                 {"possession": possession, "xg": xg, "shots": len(shots)},
                 [KeyNumber(label="Share of passes", value=f"{possession}%", unit=f"last {WINDOW_MIN} min"),
                  KeyNumber(label="xG", value=f"{xg:.2f}", unit=None)],
                 [s.event_id for s in shots][-4:])


def _bypass_press(v: _View) -> Issue | None:
    lost = [e for e in v.window if e.type == EventType.POSSESSION_CHANGE and e.team_id == v.opp and e.start
            and e.attrs.get("reason") in ("tackle", "interception") and v.own_half(e.start.x)]
    if len(lost) < 3:
        return None
    return Issue("bypass_press", min(1.0, 0.35 + len(lost) * 0.08), _band(len(lost), 6, 4),
                 {"turnovers": len(lost)},
                 [KeyNumber(label="Lost in own half", value=str(len(lost)), unit=f"last {WINDOW_MIN} min")],
                 [e.event_id for e in lost][-6:])


def _on_pitch(v: _View, team_id: str) -> list[str]:
    return [p for p in v.e.meta.players if p.startswith(team_id + "-") and v.e.player_stats(p)["on_pitch"]]


def _card_risk(v: _View) -> Issue | None:
    booked = [(p, v.e.player_stats(p)) for p in _on_pitch(v, v.team)]
    booked = [(p, s) for p, s in booked if s["yellow_cards"] and s["fouls"] >= 2]
    if not booked:
        return None
    pid, s = max(booked, key=lambda x: x[1]["fouls"])
    return Issue("card_risk", min(0.8, 0.45 + 0.08 * int(s["fouls"])), _band(int(s["fouls"]), 3, 2),
                 {"player": v.player(pid), "fouls": s["fouls"]},
                 [KeyNumber(label="Fouls", value=str(s["fouls"]), unit=None),
                  KeyNumber(label="Yellow cards", value="1", unit=None)], player_ids=[pid])


def _fresh_legs(v: _View) -> Issue | None:
    if v.minute < 60:
        return None
    runners = [(p, v.e.player_stats(p)) for p in _on_pitch(v, v.team) if v.e.meta.players[p].role != "GK"]
    if not runners:
        return None
    pid, s = max(runners, key=lambda x: x[1]["distance_km"])
    distance = round(float(s["distance_km"]), 1)
    return Issue("fresh_legs", min(0.6, 0.3 + (v.minute - 60) / 100), "low",
                 {"player": v.player(pid), "distance": distance},
                 [KeyNumber(label="Distance", value=f"{distance}", unit="km")], player_ids=[pid])


def _numbers(v: _View) -> Issue | None:
    ours, theirs = len(_on_pitch(v, v.team)), len(_on_pitch(v, v.opp))
    if ours == theirs:
        return None
    kind = "extra_man" if ours > theirs else "man_down"
    return Issue(kind, 0.85, "high", {"players": ours, "opp_players": theirs},
                 [KeyNumber(label="Players", value=f"{ours} v {theirs}", unit=None)])


def _game_state(v: _View) -> Issue | None:
    score = v.e.score()
    diff = score[v.team] - score[v.opp]
    left = max(0, 90 - v.minute)
    if diff < 0 and v.minute >= 65:
        return Issue("chase_game", min(1.0, 0.6 + (v.minute - 65) / 60), _band(v.minute, 80, 70),
                     {"minutes_left": left},
                     [KeyNumber(label="Minutes left", value=str(left), unit=None)])
    if diff > 0 and v.minute >= 75:
        return Issue("see_it_out", min(1.0, 0.5 + (v.minute - 75) / 50), _band(v.minute, 85, 80),
                     {"minutes_left": left},
                     [KeyNumber(label="Minutes left", value=str(left), unit=None)])
    return None


def _danger_man(v: _View) -> Issue | None:
    rivals = [(p, v.e.player_stats(p)) for p in _on_pitch(v, v.opp)]
    rivals = [(p, s) for p, s in rivals if s["shots"] >= 3 or s["xg"] >= 0.5]
    if not rivals:
        return None
    pid, s = max(rivals, key=lambda x: x[1]["xg"])
    xg = round(float(s["xg"]), 2)
    shots = v.e.events(player_id=pid, types={EventType.SHOT})
    return Issue("danger_man", min(0.8, 0.35 + xg / 2), _band(xg, 1.0, 0.5),
                 {"player": v.player(pid), "shots": s["shots"], "xg": xg},
                 [KeyNumber(label="Shots", value=str(s["shots"]), unit=None),
                  KeyNumber(label="xG", value=f"{xg:.2f}", unit=None)],
                 [e.event_id for e in shots if e.player_id == pid][-4:], [pid])


DETECTORS = (_numbers, _game_state, _protect_box, _momentum_against, _bypass_press, _blunt_attack, _card_risk,
             _danger_man, _fresh_legs)
# After the final whistle only the review items still make sense.
LIVE_ONLY = (_game_state, _card_risk, _fresh_legs)


def find_issues(engine: MatchEngine, team_id: str) -> list[Issue]:
    """The coordinator's ranked shortlist for one side."""
    v = _View(engine, team_id)
    over = engine.state()["status"] == "full_time"
    found = [i for d in DETECTORS if not (over and d in LIVE_ONLY) and (i := d(v)) is not None]
    for i in found:
        i.vars = {**v.base_vars(), **i.vars}
    found.sort(key=lambda i: -i.urgency)
    if not found:
        found = [Issue("steady", 0.1, "medium", v.base_vars())]
    return found[:MAX_ITEMS]


def coach_pack(engine: MatchEngine, team_id: str, issues: list[Issue], lang: str) -> dict[str, Any]:
    """Grounded context for the Coach agent and the Fact-Checker."""
    players = list(dict.fromkeys(p for i in issues for p in i.player_ids))
    return {
        "match": match_header(engine),
        "coaching": {"team_id": team_id, "name": engine.meta.team(team_id).name, "window_minutes": WINDOW_MIN},
        "issues": [
            {"issue_id": i.kind, "rank": n, "confidence": i.confidence,
             "facts": {k: v for k, v in i.vars.items() if k not in ("team", "opp", "score_line")},
             "key_numbers": [k.model_dump() for k in i.key_numbers],
             "draft": dict(zip(("title", "evidence", "recommendation", "trade_off"), i.text(lang), strict=True))}
            for n, i in enumerate(issues, 1)
        ],
        "players": [player_brief(engine, p) for p in players],
        "teams": {t: {k: v for k, v in engine.team_stats(t).items() if k in TEAM_KEYS and v is not None}
                  for t in (engine.meta.home.team_id, engine.meta.away.team_id)},
    }


# ------------------------------------------------------------------ contracts


class CoachItemDraft(_Strict):
    issue_id: str = Field(description="issue_id from context, unchanged")
    title: str = Field(description="Max 48 characters, imperative")
    evidence: str = Field(description="One sentence: the numbers from facts that show the problem")
    recommendation: str = Field(description="One or two sentences: what to change on the pitch")
    trade_off: str = Field(description="One sentence: what the change costs")


class CoachDraft(_Strict):
    items: list[CoachItemDraft] = Field(description="One item per issue, in the given rank order")
    claims: list[AgentClaim]


class CoachItem(BaseModel):
    rank: int
    issue_id: str
    title: str
    evidence: str
    recommendation: str
    trade_off: str
    confidence: Confidence
    key_numbers: list[KeyNumber]
    evidence_event_ids: list[str]
    player_ids: list[str]


class CoachBrief(BaseModel):
    team_id: str
    minute: str
    language: str
    items: list[CoachItem]
    fact_check: FactCheckReport
    source: Literal["agent", "data"]
    agents: list[str]


def template_draft(pack: dict[str, Any]) -> CoachDraft:
    """Data-only wording (and the offline client's answer): the coordinator's own drafts."""
    return CoachDraft(items=[CoachItemDraft(issue_id=i["issue_id"], **i["draft"]) for i in pack["issues"]],
                      claims=[])


def assemble(issues: list[Issue], draft: CoachDraft, team_id: str, minute: str, lang: str,
             report: FactCheckReport, source: Literal["agent", "data"], agents: list[str]) -> CoachBrief:
    worded = {d.issue_id: d for d in draft.items}
    items = []
    for n, i in enumerate(issues, 1):
        d = worded.get(i.kind)
        title, evidence, rec, trade = (d.title, d.evidence, d.recommendation, d.trade_off) if d else i.text(lang)
        items.append(CoachItem(rank=n, issue_id=i.kind, title=title, evidence=evidence, recommendation=rec,
                               trade_off=trade, confidence=i.confidence, key_numbers=i.key_numbers,
                               evidence_event_ids=i.evidence_event_ids, player_ids=i.player_ids))
    return CoachBrief(team_id=team_id, minute=minute, language=lang, items=items, fact_check=report, source=source,
                      agents=agents)

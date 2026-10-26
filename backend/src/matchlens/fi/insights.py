"""Live AI insights for the real-footage match: facts from player positions in, persona commentary out.

For each trigger (a sprint, or a rolling 10 s tactical window) the code computes the facts itself from vest frames
already ingested up to the live edge: team shape, width, depth, defensive line, compactness, speeds, sprints and
the nearest opponent. The model only words those facts. Its answer must match a JSON schema, and every number in the
text has to appear in the facts, otherwise it is rejected and the template stays. Each call has a 90 s budget; the
commentary becomes available at trigger time + measured latency, so the viewer never sees it before it existed.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import mean

from dotenv import load_dotenv

from .db import Database
from .ingest import SPRINT_MPS, ingest_commentary, ingest_operator
from .models import Commentary, CommentaryBatch, EventRef, OperatorBatch, OperatorEvent

log = logging.getLogger(__name__)

BUDGET_S = 90.0
HOME, AWAY = "RB Leipzig (white, home)", "Bayern Munich (dark, away)"
GLOSSARY = {
    "width_m": "touchline-to-touchline spread of the team's tracked outfield players",
    "depth_m": "front-to-back spread of the team, along the pitch",
    "centroid_x_m": "where the team's average position sits along the pitch, from the left goal line on screen",
    "centroid_gap_along_pitch_m": "distance between the two teams' average positions along the pitch",
    "more_compact": "team covering the smaller area (width x depth): tighter, harder to play through",
    "players_above_20kmh": "players who ran faster than 20 km/h at least once in the window",
    "nearest_opponent_m": "distance to the closest opposing player at the moment of the sprint",
}
WINDOW_MS = 10_000
PITCH_L, PITCH_W = 105.0, 68.0
PERSONAS = ("diehard", "casual", "tactical", "fantasy")

SYSTEM = """You are the live analyst for a football companion app. Match: RB Leipzig (white shirts, home) v Bayern Munich
(dark shirts, away), Bundesliga. You receive FACTS computed from player tracking (positions estimated from broadcast
video; there is no ball data, no score change and no event labels). FACTS.glossary defines every measure.
Always name the team in full ("RB Leipzig", "Bayern Munich") and make clear which team each number belongs to.
When you call a team compact or stretched, say what that means in this moment (e.g. "Leipzig's outfield players sit
within 18.3 m from touchline to touchline"). Write short, vivid, accurate insight copy for four viewer personas:
- diehard: passionate, from RB Leipzig's side ("we"/"us" = RB Leipzig).
- casual: plain words, no jargon, one idea.
- tactical: precise; shape, lines, distances, compactness; say what the numbers suggest, hedged where unsure.
- fantasy: player-level physical output; note that there are no points changes from tracking alone.
FACTS.why_now says why this moment was picked: lead with that change.
Rules: use ONLY numbers present in FACTS, written exactly as given (same rounding). Never invent passes, shots,
tackles, goals, names or the ball's location. Refer to players only by the names in FACTS.
Length: headline <= 8 words. casual <= 20 words with at most 1 number. diehard and fantasy <= 25 words.
tactical <= 40 words, the 2-3 numbers that matter most, then what they suggest. Do not list every fact.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "copy", "evidence"],
    "properties": {
        "headline": {"type": "string", "description": "<= 60 chars, factual"},
        "copy": {"type": "object", "additionalProperties": False, "required": list(PERSONAS),
                 "properties": {p: {"type": "string"} for p in PERSONAS}},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "FACTS keys the copy relies on"},
    },
}


class InsightWriter:
    """Calls the model off the ingest thread and writes commentary when (and only if) it passes checks.

    Uses the Azure OpenAI settings in backend/.env. ``MATCHLENS_FI_LLM=offline`` turns it off (templates only),
    independently of the legacy pipeline's ``MATCHLENS_LLM``.
    """

    def __init__(self, db: Database, match_id: str, players: list[dict]) -> None:
        load_dotenv()
        self.db, self.match_id = db, match_id
        self.players = {p["id"]: p for p in players}
        self.model = os.getenv("MATCHLENS_MODEL_FAST")
        self.client = None
        if os.getenv("AZURE_OPENAI_ENDPOINT") and self.model and os.getenv("MATCHLENS_FI_LLM", "auto").lower() != "offline":
            from openai import OpenAI
            self.client = OpenAI(base_url=os.environ["AZURE_OPENAI_ENDPOINT"], api_key=os.getenv("AZURE_OPENAI_API_KEY"),
                                 timeout=BUDGET_S, max_retries=0)
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="fi-insight")
        self.stats = {"requested": 0, "written": 0, "rejected": 0, "failed": 0, "latency_ms": []}

    @property
    def enabled(self) -> bool:
        return self.client is not None

    # --- Facts from vest frames ---------------------------------------------------------------------------------

    def _frames(self, from_ms: int, to_ms: int) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT player_id, media_ms, x, y, speed_mps FROM vest_frames WHERE match_id = ? AND on_pitch = 1 "
            "AND media_ms > ? AND media_ms <= ?", (self.match_id, from_ms, to_ms))]

    def _name(self, pid: str) -> str:
        return self.players[pid]["name"]

    def window_facts(self, end_ms: int) -> dict:
        frames = self._frames(end_ms - WINDOW_MS, end_ms)
        latest: dict[str, dict] = {}
        for f in frames:
            if f["media_ms"] >= latest.get(f["player_id"], {}).get("media_ms", -1):
                latest[f["player_id"]] = f
        facts: dict = {"window": f"{(end_ms - WINDOW_MS) // 1000}s–{end_ms // 1000}s", "glossary": GLOSSARY, "teams": {}}
        for side, label in (("home", HOME), ("away", AWAY)):
            pts = [f for pid, f in latest.items() if self.players[pid]["team"] == side]
            all_f = [f for f in frames if self.players[f["player_id"]]["team"] == side]
            if len(pts) < 3:
                continue
            # Pitch metres in a common frame (x towards the right-hand goal of the broadcast frame).
            xs = [(f["x"] if side == "home" else 100 - f["x"]) * PITCH_L / 100 for f in pts]
            ys = [f["y"] * PITCH_W / 100 for f in pts]
            speeds = [f["speed_mps"] for f in all_f]
            fastest = max(all_f, key=lambda f: f["speed_mps"])
            facts["teams"][label] = {
                "players_tracked": len(pts),
                "width_m": round(max(ys) - min(ys), 1),
                "depth_m": round(max(xs) - min(xs), 1),
                "centroid_x_m": round(mean(xs), 1),  # along the pitch, from the left goal line
                "avg_speed_kmh": round(mean(speeds) * 3.6, 1),
                "players_above_20kmh": len({f["player_id"] for f in all_f if f["speed_mps"] * 3.6 >= 20}),
                "fastest_player": self._name(fastest["player_id"]),
                "fastest_kmh": round(fastest["speed_mps"] * 3.6, 1),
            }
        if len(facts["teams"]) == 2:
            w, d = facts["teams"][HOME], facts["teams"][AWAY]
            facts["centroid_gap_along_pitch_m"] = round(abs(w["centroid_x_m"] - d["centroid_x_m"]), 1)
            facts["more_compact"] = HOME if w["width_m"] * w["depth_m"] < d["width_m"] * d["depth_m"] else AWAY
        return facts

    def sprint_facts(self, frame: dict) -> dict:
        pid, t = frame["player_id"], frame["media_ms"]
        p = self.players[pid]
        here = self._frames(t - 1000, t)
        me = next((f for f in here if f["player_id"] == pid), frame)
        others = {f["player_id"]: f for f in here if self.players[f["player_id"]]["team"] != p["team"]}
        nearest = min(others.values(), key=lambda f: math.dist((f["x"], f["y"]), (100 - me["x"], me["y"])), default=None)
        mine = self._frames(t - 5000, t)
        return {
            "player": p["name"], "team": HOME if p["team"] == "home" else AWAY, "glossary": GLOSSARY,
            "speed_kmh": round(frame["speed_mps"] * 3.6, 1), "sprint_threshold_kmh": round(SPRINT_MPS * 3.6, 1),
            "distance_last_5s_m": round(sum(f["speed_mps"] for f in mine if f["player_id"] == pid) * 0.2, 1),
            "nearest_opponent": self._name(nearest["player_id"]) if nearest else None,
            "nearest_opponent_m": round(math.dist(((100 - nearest["x"]) * PITCH_L / 100, nearest["y"] * PITCH_W / 100),
                                                  (me["x"] * PITCH_L / 100, me["y"] * PITCH_W / 100)), 1) if nearest else None,
            "at_second": t // 1000,
        }

    # --- Model call ---------------------------------------------------------------------------------------------

    def submit(self, event_id: str, trigger_ms: int, kind: str, facts: dict, fallback: Commentary | None = None) -> None:
        """Queue a model call; if it fails, is rejected or runs over budget, ``fallback`` (template text) is written."""
        if not self.enabled:
            if fallback:
                ingest_commentary(self.db, self.match_id, CommentaryBatch(items=[fallback]))
            return
        self.stats["requested"] += 1
        self.pool.submit(self._write, event_id, trigger_ms, kind, facts, time.monotonic(), fallback)

    def _fallback(self, fallback: Commentary | None, trigger_ms: int, wall_started: float) -> None:
        if fallback:
            available = trigger_ms + int((time.monotonic() - wall_started) * 1000)
            ingest_commentary(self.db, self.match_id, CommentaryBatch(items=[
                fallback.model_copy(update={"available_ms": max(fallback.available_ms, available)})]))

    def _write(self, event_id: str, trigger_ms: int, kind: str, facts: dict, wall_started: float,
               fallback: Commentary | None) -> None:
        t0 = time.monotonic()
        try:
            r = self.client.responses.create(
                model=self.model, instructions=SYSTEM, reasoning={"effort": "low"},
                input=f"TRIGGER: {kind}\nFACTS:\n{json.dumps(facts, ensure_ascii=False)}",
                text={"format": {"type": "json_schema", "name": "insight", "schema": SCHEMA, "strict": True}},
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            if latency_ms > BUDGET_S * 1000:
                raise TimeoutError(f"{latency_ms} ms over budget")
            out = json.loads(r.output_text)
            problem = unsupported_numbers(out, facts)
            if problem:
                self.stats["rejected"] += 1
                log.warning("insight %s rejected: numbers not in facts %s", event_id, problem)
                self._fallback(fallback, trigger_ms, wall_started)
                return
            # Available when it actually finished, measured on the same clock as the live edge.
            available = trigger_ms + int((time.monotonic() - wall_started) * 1000)
            ingest_commentary(self.db, self.match_id, CommentaryBatch(items=[Commentary(
                event_id=event_id, available_ms=available, headline=out["headline"][:80],
                copy_by_persona={p: out["copy"][p] for p in PERSONAS}, model=f"{self.model} · {latency_ms} ms")]))
            self.stats["written"] += 1
            self.stats["latency_ms"].append(latency_ms)
        except Exception:
            self.stats["failed"] += 1
            log.exception("insight %s failed; template used", event_id)
            self._fallback(fallback, trigger_ms, wall_started)


NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def unsupported_numbers(out: dict, facts: dict) -> list[str]:
    """Numbers in the copy that are not in the facts (small counts like 'two' words are not checked)."""
    allowed: set[str] = set()

    def walk(v) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                allowed.update(NUMBER.findall(k.replace("_", " ")))  # e.g. "last 5s", "above 20kmh"
                walk(x)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            allowed.update({str(v), f"{v:g}", str(round(v)), str(int(v)) if float(v).is_integer() else str(v)})
        elif isinstance(v, str):
            allowed.update(NUMBER.findall(v))
    walk(facts)
    text = " ".join([out["headline"], *out["copy"].values()])
    return [n for n in NUMBER.findall(text) if n not in allowed and n not in {"1", "2", "3"}]


# --- What is worth showing ---------------------------------------------------------------------------------------

CHECK_EVERY_MS = 5_000        # how often team shape is evaluated
SHAPE_COOLDOWN_MS = 8_000     # at most one shape insight per 8 s of play
NOTABLE_SPRINT_KMH = 27.0     # sprints this fast are always worth a card
SHAPE_CHANGE = 0.20           # a team's width or depth moving by 20 %
GAP_CHANGE_M = 5.0            # teams' average positions moving 5 m closer or further apart
BURST_PLAYERS = 3             # this many players above 20 km/h in one window


def shape_reasons(previous: dict | None, facts: dict) -> list[str]:
    """Why this window is worth an insight (empty list: nothing important changed)."""
    teams = facts.get("teams", {})
    if len(teams) < 2:
        return []
    if previous is None:
        return ["first clear read of both teams' shape"]
    reasons = []
    if previous.get("more_compact") != facts.get("more_compact"):
        reasons.append(f"{facts['more_compact'].split(' (')[0]} are now the more compact side")
    for team, now in teams.items():
        before = previous.get("teams", {}).get(team)
        name = team.split(" (")[0]
        if not before:
            continue
        for key, label in (("width_m", "width"), ("depth_m", "depth")):
            if before[key] and abs(now[key] - before[key]) / before[key] >= SHAPE_CHANGE:
                reasons.append(f"{name} {label} changed from {before[key]} m to {now[key]} m")
        if now["players_above_20kmh"] >= BURST_PLAYERS and before["players_above_20kmh"] < BURST_PLAYERS:
            reasons.append(f"{name} have {now['players_above_20kmh']} players running above 20 km/h")
    gap_before, gap_now = previous.get("centroid_gap_along_pitch_m"), facts.get("centroid_gap_along_pitch_m")
    if gap_before is not None and gap_now is not None and abs(gap_now - gap_before) >= GAP_CHANGE_M:
        reasons.append(f"gap between the teams moved from {gap_before} m to {gap_now} m")
    return reasons


def sprint_reason(kmh: float, fastest_so_far: float) -> str | None:
    """Only sprints that stand out get a card; every sprint still counts in the stats."""
    if kmh > fastest_so_far:
        return "fastest sprint of the match so far"
    if kmh >= NOTABLE_SPRINT_KMH:
        return f"sprint above {NOTABLE_SPRINT_KMH:g} km/h"
    return None


def post_window_event(db: Database, match_id: str, epoch: int, end_ms: int) -> str:
    """A ledger event for a tactical window; its text arrives later from the model (fact first, analysis after)."""
    event_id = f"insight-{epoch}-{end_ms // 1000}"
    ingest_operator(db, match_id, OperatorBatch(source="sensor", events=[OperatorEvent(
        media_ms=end_ms, available_ms=end_ms, event=EventRef(id=event_id, type="insight"))]), key=f"{match_id}:{epoch}:{event_id}")
    return event_id

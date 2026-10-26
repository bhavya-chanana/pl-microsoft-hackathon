"""Deterministic demo match, replayed through the same ingest functions real devices use.

It turns the scripted story (``demo_match.json``) into raw sensor data: 1 Hz vest frames for every player,
ball-sensor impacts (passes, duels, shots, line crosses), video-AI observations and operator input.
Nothing here writes the ledger or stats directly; fusion and the stats engine derive them from the feeds.
"""

from __future__ import annotations

import json
import math
from functools import cache
from pathlib import Path
from typing import Any

from .db import Database
from .ingest import DuplicateBatch, ingest_ball, ingest_commentary, ingest_operator, ingest_vest, ingest_vision
from .models import (BallBatch, BallEvent, Commentary, CommentaryBatch, EventRef, OperatorBatch, OperatorEvent, VestBatch,
                     VestFrame, VisionBatch, VisionObservation)

STORY_PATH = Path(__file__).with_name("demo_match.json")
STORY_MATCHES = ("nor-har", "nor-har-r1")  # matches that replay the demo story
VEST_BATCH_MS = 60_000


@cache
def story() -> dict[str, Any]:
    return json.loads(STORY_PATH.read_text())


def fnv(s: str) -> float:
    """Same 32-bit FNV-1a hash as the frontend mock, so numbers line up across both."""
    h = 2166136261
    for ch in s:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return (h % 10_000) / 10_000


def on_pitch(p: dict, t_ms: int) -> bool:
    start = 0 if p["startsOn"] else p.get("onFromMs")
    if start is None or t_ms < start:
        return False
    return t_ms < p.get("offAtMs", math.inf)


# --- Vests ------------------------------------------------------------------------------------------------------

def _vest_frames(p: dict, from_ms: int, to_ms: int) -> list[VestFrame]:
    r = fnv(p["id"])
    gk = p["position"] == "GK"
    base_v = (55 if gk else 105 + r * 22) / 60  # m/s from metres per minute
    peak = (24 + r * 3 if gk else 27 + r * 6) / 3.6
    sprint_p = 0 if gk else (0.18 + r * 0.12) / 60
    phase, phase2 = r * 6.28, fnv(p["id"] + "y") * 6.28
    frames = []
    sprint_left = 0
    for t in range(from_ms, to_ms, 1000):
        s = t // 1000
        active = on_pitch(p, t)
        if sprint_left == 0 and active and fnv(f"{p['id']}s{s}") < sprint_p:
            sprint_left = 4
        v = base_v * (0.55 + 0.9 * (0.5 + 0.5 * math.sin(s / 23 + phase)))
        if sprint_left:
            v, sprint_left = peak, sprint_left - 1
        if p["id"] == "h11" and 62 * 60_000 <= t < 62 * 60_000 + 3000:
            v = 34.6 / 3.6  # the story's top-speed moment
        x = min(100, max(0, p["base"]["x"] + 12 * math.sin(s / 37 + phase)))
        y = min(100, max(0, p["base"]["y"] + 10 * math.sin(s / 53 + phase2)))
        hr = round(min(195, 140 + r * 18 + 25 * v / peak)) if active else None
        frames.append(VestFrame(player_id=p["id"], media_ms=t, x=x, y=y, speed_mps=round(v if active else 0, 3),
                                heart_rate=hr, on_pitch=active))
    return frames


# --- Ball sensor ------------------------------------------------------------------------------------------------

def _teammates(team: str, t: int, exclude: str) -> list[dict]:
    return [q for q in story()["players"] if q["team"] == team and q["id"] != exclude and on_pitch(q, t)]


def _opponents(team: str, t: int) -> list[dict]:
    return [q for q in story()["players"] if q["team"] != team and on_pitch(q, t)]


def _pick(players: list[dict], key: str) -> dict | None:
    return players[int(fnv(key) * len(players))] if players else None


def _possession_events(duration_ms: int) -> list[BallEvent]:
    """Passes, duels, recoveries, dribbles, tackles and interceptions at each player's calibrated rate."""
    out: list[BallEvent] = []
    team_rates = {"dribble": {"home": 0.11, "away": 0.10}, "tackle": {"home": 0.2, "away": 0.15},
                  "interception": {"home": 0.09, "away": 0.11}}
    for p in story()["players"]:
        r = fnv(p["id"])
        gk, pos = p["position"] == "GK", p["position"]
        rates = {
            "pass": 0.35 if gk else (0.75 + r * 0.25 if pos == "MF" else 0.45 + r * 0.25),
            "duel": 0 if gk else 0.08 + r * 0.06,
            "recovery": 0.09 if pos in ("MF", "DF") else 0.04,
        }
        outfield = sum(1 for q in story()["players"] if q["team"] == p["team"] and q["position"] != "GK" and q["startsOn"])
        if not gk:
            for kind, by_team in team_rates.items():
                share = 1.4 if (kind == "dribble" and pos in ("FW", "MF")) or (kind != "dribble" and pos in ("DF", "MF")) else 0.4
                rates[kind] = by_team[p["team"]] * share / outfield
        accuracy = 0.72 if gk else 0.78 + r * 0.14
        acc = {k: 0.0 for k in rates}
        for minute in range(duration_ms // 60_000 + 1):
            for kind, rate in rates.items():
                acc[kind] += rate
                n = int(acc[kind])
                acc[kind] -= n
                for i in range(n):
                    t = minute * 60_000 + int(fnv(f"{p['id']}{kind}{minute}{i}") * 59_000)
                    if t >= duration_ms or not on_pitch(p, t):
                        continue
                    key = f"{p['id']}-{kind}-{minute}-{i}"
                    attrs: dict[str, Any] = {}
                    target = None
                    if kind == "pass":
                        attrs = {"completed": fnv(key + "c") < accuracy,
                                 "progressive": fnv(key + "p") < (0.14 if pos == "MF" else 0.07),
                                 "half": "own" if fnv(key + "h") < (0.36 if p["team"] == "home" else 0.52) else "opposition",
                                 "long": fnv(key + "l") < 0.08, "y": round(fnv(key + "y") * 100, 1)}
                        mate = _pick(_teammates(p["team"], t, p["id"]), key + "t")
                        target = mate["id"] if mate else None
                    elif kind == "duel":
                        attrs = {"won": True, "aerial": fnv(key + "a") < 0.22}
                        opp = _pick(_opponents(p["team"], t), key + "o")
                        target = opp["id"] if opp else None
                    out.append(BallEvent(id=key, kind=kind, media_ms=t, available_ms=t + 1500, team=p["team"],
                                         player_id=p["id"], target_player_id=target, attributes=attrs))
    return out


SHOOTERS = {"home": ["h9", "h7", "h11", "h10", "h8", "h19", "h9", "h11"], "away": ["a9", "a10", "a7", "a17", "a9", "a10"]}
OFF_OUTCOMES = ["off", "blocked", "off", "woodwork", "off", "blocked"]
SHOT_SPEEDS = {"e03": 96, "e08": 98, "e14": 102, "e17": 104}


def _shot_events() -> list[BallEvent]:
    """Missed shots at the calibrated timings, plus every story shot, save and goal (with its strike data)."""
    out: list[BallEvent] = []
    misses = [row for row in story()["calibratedTimeline"] if row[1] == "off"]
    for i, (sec, _, team) in enumerate(misses):
        r, r2 = fnv(f"shot{i}"), fnv(f"shoty{i}")
        x, y = 74 + r * 22, 25 + r2 * 50
        out.append(BallEvent(
            id=f"s{i}", kind="shot", media_ms=sec * 1000, available_ms=sec * 1000 + 5000, team=team,
            player_id=SHOOTERS[team][i % len(SHOOTERS[team])],
            attributes={"x": x, "y": y, "outcome": OFF_OUTCOMES[i % len(OFF_OUTCOMES)], "speed_kmh": round(70 + r2 * 45),
                        "body": "Header" if i % 5 == 0 else "Left foot" if i % 2 else "Right foot",
                        "situation": "Set piece" if i % 4 == 0 else "Open play"}))
    for e in story()["events"]:
        if not e.get("team") or not e.get("at") or e["type"] not in ("shot", "goal", "big_chance", "save"):
            continue
        shooter_team = ("away" if e["team"] == "home" else "home") if e["type"] == "save" else e["team"]
        shooter = e["playerIds"][1] if e["type"] == "save" else e["playerIds"][0]
        outcome = "goal" if e["type"] == "goal" else "saved" if e["type"] == "save" else "off"
        attrs = {"x": e["at"]["x"], "y": e["at"]["y"], "outcome": outcome, "xg": e.get("xg", 0.22),
                 "speed_kmh": SHOT_SPEEDS.get(e["id"], 88),
                 "body": "Header" if e["id"] in ("e12", "e28") else "Right foot" if e["id"] == "e03" else "Left foot",
                 "situation": "Set piece" if e["id"] == "e03" else "Open play"}
        first = e["revisions"][0]["availableMs"]
        ref = None if e["type"] == "goal" else _ref(e)
        out.append(BallEvent(id=f"shot-{e['id']}", kind="shot", media_ms=e["atMs"], available_ms=first, team=shooter_team,
                             player_id=shooter, attributes=attrs, event=ref))
    return out


def _ref(e: dict) -> EventRef:
    attrs = {k: e[k] for k in ("at", "xg", "fantasyPoints") if k in e}
    return EventRef(id=e["id"], type=e["type"], team=e.get("team"), player_ids=e["playerIds"], critical=e["critical"], attributes=attrs)


VISION_KIND = {"offside": "offside", "yellow": "yellow", "red": "red", "sub": "substitution", "foul": "foul", "corner": "corner"}


def _story_feeds() -> tuple[list[BallEvent], list[VisionObservation], list[OperatorEvent], list[OperatorEvent]]:
    """Route each story event to the source that would really produce it."""
    ball, vision, operator, vest = [], [], [], []
    for e in story()["events"]:
        first, *later = e["revisions"]
        ref = _ref(e)
        if e["type"] == "goal":
            ball.append(BallEvent(id=f"line-{e['id']}", kind="goal_line_cross", media_ms=e["atMs"], available_ms=first["availableMs"],
                                  team=e["team"], player_id=e["playerIds"][0], event=ref))
        elif e["type"] == "press_win":
            ball.append(BallEvent(id=f"press-{e['id']}", kind="press_win", media_ms=e["atMs"], available_ms=first["availableMs"],
                                  team=e["team"], player_id=e["playerIds"][0], event=ref))
        elif e["type"] in ("shot", "save", "big_chance"):
            pass  # created by the shot impact in _shot_events
        elif e["source"] == "vest":
            vest.append(OperatorEvent(media_ms=e["atMs"], available_ms=first["availableMs"], event=ref))
        elif e["source"] == "llm_vision" or e["type"] in VISION_KIND:
            vision.append(VisionObservation(
                id=f"v-{e['id']}", kind=VISION_KIND.get(e["type"], "other"), media_from_ms=max(0, e["atMs"] - 5000),
                media_to_ms=e["atMs"] + 3000, available_ms=first["availableMs"], team=e.get("team"), player_ids=e["playerIds"],
                confidence=0.9, description=e["headline"], event=ref))
        else:
            operator.append(OperatorEvent(media_ms=e["atMs"], available_ms=first["availableMs"], status=first["status"],
                                          note=first.get("note"), event=ref))
        for i, rev in enumerate(later):
            kind = "var_overturn" if rev["status"] == "overturned" else "goal_confirmed"
            vision.append(VisionObservation(
                id=f"v-{e['id']}-r{i + 2}", kind=kind, media_from_ms=e["atMs"], media_to_ms=rev["availableMs"],
                available_ms=rev["availableMs"], team=e.get("team"), player_ids=e["playerIds"], confidence=0.95,
                description=rev.get("note", ""), event=ref))
    calibrated_vision = {"foul": "foul", "corner": "corner", "fk": "free_kick", "throw": "throw_in"}
    for i, (sec, kind, team) in enumerate(story()["calibratedTimeline"]):
        if kind in calibrated_vision:
            vision.append(VisionObservation(id=f"cal-{i}", kind=calibrated_vision[kind], media_from_ms=sec * 1000,
                                            media_to_ms=sec * 1000 + 2000, available_ms=sec * 1000 + 8000, team=team,
                                            confidence=0.85, description=f"{kind} ({team})"))
        elif kind == "clearance":
            defender = _pick([q for q in story()["players"] if q["team"] == team and q["position"] == "DF"
                              and on_pitch(q, sec * 1000)], f"clr{i}")
            ball.append(BallEvent(id=f"clr-{i}", kind="clearance", media_ms=sec * 1000, available_ms=sec * 1000 + 1500, team=team,
                                  player_id=defender["id"] if defender else None))
    return ball, vision, operator, vest


def _commentary() -> list[Commentary]:
    """Mock LLM commentary: ready a few seconds after the fact it describes."""
    return [Commentary(event_id=e["id"], available_ms=e["revisions"][0]["availableMs"] + 3000, headline=e["headline"],
                       copy_by_persona=e["copy"]) for e in story()["events"]]


# --- Seeding ----------------------------------------------------------------------------------------------------

def seed_reference(db: Database) -> None:
    s = story()
    for m in s["matches"]:
        db.execute("INSERT OR REPLACE INTO matches VALUES (?, ?)", (m["id"], json.dumps(m)))
        if m["id"] in STORY_MATCHES:
            for p in s["players"]:
                db.execute("INSERT OR REPLACE INTO roster VALUES (?, ?, ?, ?)", (m["id"], p["id"], p["team"], json.dumps(p)))
    db.commit()


def replay(db: Database, match_id: str) -> dict[str, int]:
    """Post the whole match through ingest. Safe to call again: every batch has an idempotency key."""
    s = story()
    duration = s["durationMs"]
    counts = {"vest_frames": 0, "ball_events": 0, "vision": 0, "operator": 0}

    def guarded(fn, *args, key: str) -> int:
        try:
            return fn(db, match_id, *args, key=f"{match_id}:{key}")
        except DuplicateBatch:
            return 0

    for start in range(0, duration + 1000, VEST_BATCH_MS):
        frames = [f for p in s["players"] for f in _vest_frames(p, start, min(start + VEST_BATCH_MS, duration + 1000))]
        counts["vest_frames"] += guarded(ingest_vest, VestBatch(source="mock", frames=frames), key=f"vest:{start}")
    ball, vision, operator, vest = _story_feeds()
    ball += _shot_events() + _possession_events(duration)
    ball.sort(key=lambda e: e.media_ms)
    for i in range(0, len(ball), 2000):
        counts["ball_events"] += guarded(ingest_ball, BallBatch(source="mock", events=ball[i:i + 2000]), key=f"ball:{i}")
    vision.sort(key=lambda o: o.available_ms)
    counts["vision"] = guarded(ingest_vision, VisionBatch(source="mock", observations=vision), key="vision")
    counts["operator"] = guarded(ingest_operator, OperatorBatch(source="operator", events=operator), key="operator")
    counts["operator"] += guarded(ingest_operator, OperatorBatch(source="sensor", events=vest), key="vest-moments")
    guarded(ingest_commentary, CommentaryBatch(items=_commentary()), key="commentary")
    return counts


def ensure_seeded(db: Database) -> None:
    seed_reference(db)
    for match_id in STORY_MATCHES:
        replay(db, match_id)

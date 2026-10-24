"""Real-data reference profile for simulator calibration (FR-008).

Measures the same match metrics on real event data and on simulated matches,
so the simulator can be compared with how football is actually played rather
than with rounded published averages. Only aggregate statistics are kept: no
team, player, competition or match names or ids leave this module, so the
product stays fully fictional (BRD §9.3).

Source: StatsBomb Open Data (https://github.com/statsbomb/open-data), English
top-flight men's season 2015/16, a deterministic sample of matches. Data
provided by StatsBomb. Event data only: there is no tracking, so distance and
sprint metrics keep their published reference ranges (see calibration.py).

Run (downloads about 3 MB per match, nothing raw is stored):
    uv run python -m matchlens.simulator.reference --matches 100
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from matchlens.domain.models import (
    PITCH_LENGTH,
    PITCH_WIDTH,
    EventType,
    Outcome,
    SyntheticMatch,
)

SOURCE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
COMPETITION_ID, SEASON_ID = 2, 27  # English top flight, 2015/16
PROFILE_PATH = Path(__file__).with_name("reference_profile.json")

# StatsBomb pitch: 120 x 80 yards, attacking towards x = 120. Ours: 105 x 68 metres.
SX, SY = PITCH_LENGTH / 120, PITCH_WIDTH / 80
ON_TARGET = {"Goal", "Saved", "Saved to Post"}
BOX_X, BOX_HALF_W = 16.5, 20.16  # penalty area depth and half width, metres

# What each metric means, shared by both measurements.
METRICS: dict[str, str] = {
    "passes": "Passes, both teams, throw-ins excluded",
    "pass_completion_pct": "Completed passes, % (throw-ins excluded)",
    "pass_len_median_m": "Median pass length, metres",
    "pass_short_pct": "Passes under 15 m, %",
    "pass_long_pct": "Passes over 30 m, %",
    "completion_long_pct": "Completion of passes over 30 m, %",
    "pressured_pass_pct": "Passes made under pressure, %",
    "completion_pressured_pct": "Completion under pressure, %",
    "shots": "Shots, both teams",
    "goals": "Goals (own goals excluded)",
    "on_target_pct": "Shots on target, %",
    "blocked_pct": "Shots blocked, %",
    "xg_per_shot": "Mean xG per shot",
    "shot_distance_m": "Mean shot distance to goal, metres",
    "shots_in_box_pct": "Shots from inside the penalty area, %",
    "headers_pct": "Headed shots, %",
    "corners": "Corners",
    "throw_ins": "Throw-ins",
    "goal_kicks": "Goal kicks",
    "fouls": "Fouls",
    "yellow_cards": "Yellow cards (first bookings)",
    "tackles": "Tackles won",
    "interceptions": "Interceptions",
}


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def _shot_geometry(x: float, y: float) -> tuple[float, bool]:
    """Distance to the centre of the goal being attacked and whether inside the box (metres)."""
    dx, dy = PITCH_LENGTH - x, abs(y - PITCH_WIDTH / 2)
    return math.hypot(dx, dy), dx <= BOX_X and dy <= BOX_HALF_W


def _pass_summary(passes: list[tuple[float, bool, bool]]) -> dict[str, float | None]:
    """passes: (length_m, completed, pressured) for every non-throw-in pass."""
    long = [p for p in passes if p[0] > 30]
    pressured = [p for p in passes if p[2]]
    return {
        "passes": len(passes),
        "pass_completion_pct": _pct(sum(p[1] for p in passes), len(passes)),
        "pass_len_median_m": round(statistics.median(p[0] for p in passes), 2) if passes else None,
        "pass_short_pct": _pct(sum(p[0] < 15 for p in passes), len(passes)),
        "pass_long_pct": _pct(len(long), len(passes)),
        "completion_long_pct": _pct(sum(p[1] for p in long), len(long)),
        "pressured_pass_pct": _pct(len(pressured), len(passes)),
        "completion_pressured_pct": _pct(sum(p[1] for p in pressured), len(pressured)),
    }


def _shot_summary(shots: list[tuple[float, float, bool, bool, bool, bool]], goals: int) -> dict[str, float | None]:
    """shots: (xg, distance_m, in_box, header, on_target, blocked)."""
    n = len(shots)
    return {
        "shots": n,
        "goals": goals,
        "on_target_pct": _pct(sum(s[4] for s in shots), n),
        "blocked_pct": _pct(sum(s[5] for s in shots), n),
        "xg_per_shot": round(statistics.mean(s[0] for s in shots), 4) if shots else None,
        "shot_distance_m": round(statistics.mean(s[1] for s in shots), 2) if shots else None,
        "shots_in_box_pct": _pct(sum(s[2] for s in shots), n),
        "headers_pct": _pct(sum(s[3] for s in shots), n),
    }


def measure_statsbomb(events: list[dict[str, Any]]) -> dict[str, float | None]:
    """Metrics for one real match from StatsBomb events."""
    passes, shots = [], []
    goals = corners = throw_ins = goal_kicks = fouls = yellows = tackles = interceptions = 0
    for e in events:
        kind = e["type"]["name"]
        if kind == "Pass":
            p = e["pass"]
            ptype = p.get("type", {}).get("name")
            if ptype == "Throw-in":
                throw_ins += 1
                continue
            corners += ptype == "Corner"
            goal_kicks += ptype == "Goal Kick"
            (x0, y0), (x1, y1) = e["location"], p["end_location"]
            length = math.hypot((x1 - x0) * SX, (y1 - y0) * SY)
            passes.append((length, "outcome" not in p, bool(e.get("under_pressure"))))
        elif kind == "Shot":
            s = e["shot"]
            outcome = s["outcome"]["name"]
            goals += outcome == "Goal"
            dist, in_box = _shot_geometry(e["location"][0] * SX, e["location"][1] * SY)
            shots.append((s.get("statsbomb_xg", 0.0), dist, in_box, s["body_part"]["name"] == "Head",
                          outcome in ON_TARGET, outcome == "Blocked"))
        elif kind == "Foul Committed":
            fouls += 1
            yellows += e.get("foul_committed", {}).get("card", {}).get("name") == "Yellow Card"
        elif kind == "Bad Behaviour":
            yellows += e.get("bad_behaviour", {}).get("card", {}).get("name") == "Yellow Card"
        elif kind == "Duel" and e.get("duel", {}).get("type", {}).get("name") == "Tackle":
            tackles += e["duel"].get("outcome", {}).get("name") in ("Won", "Success", "Success In Play",
                                                                     "Success Out")
        elif kind == "Interception":
            interceptions += e.get("interception", {}).get("outcome", {}).get("name") in (
                "Won", "Success", "Success In Play", "Success Out")
    return {**_pass_summary(passes), **_shot_summary(shots, goals), "corners": corners, "throw_ins": throw_ins,
            "goal_kicks": goal_kicks, "fouls": fouls, "yellow_cards": yellows, "tackles": tackles,
            "interceptions": interceptions}


def measure_simulated(m: SyntheticMatch) -> dict[str, float | None]:
    """The same metrics for one simulated match."""
    passes, shots = [], []
    counts = {"corners": 0, "throw_ins": 0, "goal_kicks": 0, "fouls": 0, "yellow_cards": 0, "tackles": 0,
              "interceptions": 0}
    goals = 0
    for e in m.events:
        attacks_right = e.team_id == m.meta.home.team_id  # the home side attacks x = 105 in both halves
        if e.type == EventType.PASS:
            if e.attrs.get("set_piece") == "throw_in":
                continue
            assert e.start and e.end
            length = math.hypot(e.end.x - e.start.x, e.end.y - e.start.y)
            passes.append((length, e.outcome == Outcome.SUCCESS, bool(e.attrs.get("pressured"))))
        elif e.type == EventType.SHOT:
            assert e.start
            x = e.start.x if attacks_right else PITCH_LENGTH - e.start.x
            dist, in_box = _shot_geometry(x, e.start.y)
            shots.append((float(e.attrs.get("xg", 0.0)), dist, in_box, e.attrs.get("body_part") == "head",
                          e.outcome in (Outcome.GOAL, Outcome.SAVED), e.outcome == Outcome.BLOCKED))
        elif e.type == EventType.GOAL:
            goals += 1
        elif e.type == EventType.THROW_IN:
            counts["throw_ins"] += 1
        elif e.type == EventType.CORNER:
            counts["corners"] += 1
        elif e.type == EventType.GOAL_KICK:
            counts["goal_kicks"] += 1
        elif e.type == EventType.FOUL:
            counts["fouls"] += 1
        elif e.type == EventType.CARD and e.outcome == Outcome.YELLOW:
            counts["yellow_cards"] += 1
        elif e.type == EventType.TACKLE:
            counts["tackles"] += 1
        elif e.type == EventType.INTERCEPTION and not e.attrs.get("clearance"):
            counts["interceptions"] += 1
    return {**_pass_summary(passes), **_shot_summary(shots, goals), **counts}


def summarise(rows: Iterable[dict[str, float | None]]) -> dict[str, dict[str, float]]:
    rows = list(rows)
    out: dict[str, dict[str, float]] = {}
    for key in METRICS:
        values = sorted(v for r in rows if (v := r.get(key)) is not None)
        if not values:
            continue
        q = statistics.quantiles(values, n=10) if len(values) > 1 else [values[0]] * 9
        out[key] = {"mean": round(statistics.mean(values), 3),
                    "sd": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
                    "p10": round(q[0], 3), "p90": round(q[-1], 3)}
    return out


def _get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def build_profile(matches: int = 100, workers: int = 8) -> dict[str, Any]:
    ids = sorted(m["match_id"] for m in _get(f"{SOURCE}/matches/{COMPETITION_ID}/{SEASON_ID}.json"))
    step = max(1, len(ids) // matches)
    sample = ids[::step][:matches]
    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(lambda mid: measure_statsbomb(_get(f"{SOURCE}/events/{mid}.json")), sample))
    return {
        "source": "StatsBomb Open Data (https://github.com/statsbomb/open-data). Data provided by StatsBomb.",
        "scope": "English top-flight men's league, season 2015/16, aggregate statistics only",
        "matches": len(rows),
        "sample": f"every {step}th match by match id",
        "metrics": summarise(rows),
        "definitions": METRICS,
    }


def load_profile() -> dict[str, Any]:
    return json.loads(PROFILE_PATH.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--matches", type=int, default=100)
    args = parser.parse_args()
    profile = build_profile(args.matches)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2) + "\n")
    print(f"wrote {PROFILE_PATH} from {profile['matches']} matches")


if __name__ == "__main__":
    main()

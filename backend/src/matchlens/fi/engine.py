"""As-of reads: the server-side projection behind Watch, Stats and player pages.

Every public function takes the viewer's playhead and only uses data whose ``media_ms`` and
``available_ms`` are at or before it. Stats additionally use the last complete snapshot boundary
(``snapshot_through``), so they may lag the video but never lead it.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any, Literal

from .db import Database
from .ingest import PHYSICAL_SNAPSHOT_MS
from .simulator import fnv

Period = Literal["all", "h1", "h2"]
HALF_MS = 45 * 60_000
# Snapshot cadence (docs/POLLING.md). A match can override it with a "snapshot" block, e.g. a 30 s live clip.
DEFAULT_TIMING = {"fullMs": 30_000, "lagMs": 20_000, "physicalMs": 15_000}
PERSONAS = ("diehard", "casual", "tactical", "fantasy")
DEFAULT_HEADLINE = {"goal": "Goal", "save": "Save", "yellow": "Yellow card", "red": "Red card", "sub": "Substitution",
                    "offside": "Offside", "press_win": "High regain", "sprint": "Top speed", "kickoff": "Kick-off",
                    "half_time": "Half time", "full_time": "Full time", "var": "VAR review", "big_chance": "Big chance",
                    "insight": "Live insight"}


def snapshot_through(t: int, timing: dict = DEFAULT_TIMING) -> int:
    return max(0, t - timing["lagMs"]) // timing["fullMs"] * timing["fullMs"]


def physical_through(t: int, timing: dict = DEFAULT_TIMING) -> int:
    return max(0, t - timing["lagMs"] // 2) // timing["physicalMs"] * timing["physicalMs"]


def bounds(through: int, period: Period) -> tuple[int, int]:
    if period == "h1":
        return 0, min(through, HALF_MS)
    if period == "h2":
        return HALF_MS, max(HALF_MS, through)
    return 0, through


def r1(x: float) -> float:
    return round(x * 10) / 10


def r2(x: float) -> float:
    return round(x * 100) / 100


class Engine:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: dict[tuple, Any] = {}

    def _memo(self, key: tuple, fn):
        full = (self.db.version, *key)
        if full not in self._cache:
            if len(self._cache) > 2000:
                self._cache.clear()
            self._cache[full] = fn()
        return self._cache[full]

    # --- Reference ----------------------------------------------------------------------------------------------

    def matches(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.db.query("SELECT body FROM matches")]

    def match(self, match_id: str) -> dict | None:
        rows = self.db.query("SELECT body FROM matches WHERE id = ?", (match_id,))
        return json.loads(rows[0]["body"]) if rows else None

    def timing(self, match_id: str) -> dict:
        return {**DEFAULT_TIMING, **((self.match(match_id) or {}).get("snapshot") or {})}

    def roster(self, match_id: str) -> list[dict]:
        return self._memo(("roster", match_id), lambda: [
            json.loads(r["body"]) for r in self.db.query("SELECT body FROM roster WHERE match_id = ?", (match_id,))])

    def public_roster(self, match_id: str) -> list[dict]:
        """Roster without substitution times: when someone comes on is itself a future fact."""
        return [{k: v for k, v in p.items() if k not in ("onFromMs", "offAtMs")} for p in self.roster(match_id)]

    # --- Ledger ---------------------------------------------------------------------------------------------------

    def events(self, match_id: str, t: int, known_by: int | None = None) -> list[dict]:
        """Events up to ``t`` on the video. ``known_by`` (default ``t``) is how far knowledge may reach: a live viewer
        behind the edge may see analysis that already exists about moments on screen; a replay uses the playhead."""
        k = t if known_by is None else max(t, known_by)
        return self._memo(("events", match_id, t, k), lambda: self._events(match_id, t, k))

    def _events(self, match_id: str, t: int, known_by: int) -> list[dict]:
        rows = self.db.query("SELECT * FROM event_revisions WHERE match_id = ? AND available_ms <= ? AND media_ms <= ? "
                             "ORDER BY event_id, revision", (match_id, known_by, t))
        commentary = {r["event_id"]: r for r in self.db.query(
            "SELECT * FROM commentary WHERE match_id = ? AND available_ms <= ?", (match_id, known_by))}
        grouped: dict[str, list] = defaultdict(list)
        for r in rows:
            grouped[r["event_id"]].append(r)
        out = []
        for event_id, revs in grouped.items():
            first, last = revs[0], revs[-1]
            body = json.loads(first["body"])
            c = commentary.get(event_id)
            source = {"fusion": "fusion", "llm_vision": "llm_vision", "operator": "operator"}[first["source"]]
            if first["type"] == "sprint":
                source = "vest"
            elif first["type"] == "insight":
                source = "model"
            elif first["type"] == "kickoff":
                source = "ball"
            out.append({
                "id": event_id, "type": first["type"], "atMs": first["media_ms"], "team": first["team"],
                "playerIds": json.loads(first["player_ids"]), "source": source, "critical": body.get("critical", False),
                "status": last["status"], "note": last["note"],
                "revisions": [{"availableMs": r["available_ms"], "status": r["status"], "note": r["note"]} for r in revs],
                "headline": c["headline"] if c else DEFAULT_HEADLINE.get(first["type"], first["type"].title()),
                "copy": json.loads(c["copy"]) if c else None,
                "analysis": "ready" if c else "pending",
                "analysisModel": c["model"] if c else None,
                **{k: body[k] for k in ("at", "xg", "fantasyPoints") if k in body},
            })
        return sorted(out, key=lambda e: e["atMs"])

    def score(self, match_id: str, t: int, known_by: int | None = None) -> dict:
        score = {"home": 0, "away": 0, "provisional": False}
        for e in self.events(match_id, t, known_by):
            if e["type"] == "goal" and e["status"] != "overturned" and e["team"]:
                score[e["team"]] += 1
                score["provisional"] = score["provisional"] or e["status"] == "provisional"
        return score

    # --- Raw-feed helpers (always bounded by available time) --------------------------------------------------------

    def _sensor(self, match_id: str, through: int) -> list[dict]:
        def load():
            rows = self.db.query("SELECT kind, media_ms, team, player_id, target_player_id, body FROM sensor_events "
                                 "WHERE match_id = ? AND available_ms <= ? AND media_ms <= ?", (match_id, through, through))
            return [{**dict(r), "attrs": json.loads(r["body"])} for r in rows]
        return self._memo(("sensor", match_id, through), load)

    def _vision(self, match_id: str, through: int) -> list[dict]:
        return self._memo(("vision", match_id, through), lambda: [dict(r) for r in self.db.query(
            "SELECT kind, media_from_ms, team FROM vision_observations WHERE match_id = ? AND available_ms <= ? AND media_from_ms <= ?",
            (match_id, through, through))])

    def shots(self, match_id: str, through: int, period: Period) -> list[dict]:
        a, b = bounds(through, period)
        overturned = {e["id"] for e in self.events(match_id, through) if e["status"] == "overturned"}
        goal_events = {e["id"]: e for e in self.events(match_id, through) if e["type"] == "goal"}
        out = []
        for r in self.db.query("SELECT * FROM shots WHERE match_id = ? AND available_ms <= ? AND media_ms >= ? AND media_ms <= ? "
                               "ORDER BY media_ms", (match_id, through, a, b)):
            attrs = json.loads(r["body"])
            if r["event_id"] in overturned:
                continue
            x, y = attrs["x"], attrs["y"]
            xg = attrs.get("xg") or r2(max(0.02, 0.45 - math.hypot(100 - x, 50 - y) / 60))  # simple distance model
            outcome = attrs["outcome"]
            if outcome == "goal" and not any(g["atMs"] == r["media_ms"] and g["status"] != "overturned" for g in goal_events.values()):
                continue  # the line cross has not been fused into a goal yet, or was ruled out
            xgot = min(0.99, xg + 0.35) if outcome == "goal" else (0.48 if r["event_id"] else r2(xg + 0.12)) if outcome == "saved" else None
            out.append({"id": r["event_id"] or r["id"].removeprefix("shot-"), "atMs": r["media_ms"], "availableMs": r["available_ms"], "team": r["team"],
                        "playerId": r["player_id"], "x": x, "y": y, "xg": r2(xg), "xgot": xgot, "outcome": outcome,
                        "body": attrs["body"], "situation": attrs["situation"], "speedKmh": attrs["speed_kmh"]})
        return out

    def _physical(self, match_id: str, player_id: str, phys: int) -> dict | None:
        rows = self.db.query("SELECT * FROM physical_snapshots WHERE match_id = ? AND player_id = ? AND through_ms <= ? "
                             "ORDER BY through_ms DESC LIMIT 1", (match_id, player_id, phys))
        return dict(rows[0]) if rows else None

    # --- Player stats ---------------------------------------------------------------------------------------------

    def player_stats_at(self, match_id: str, player_id: str, through: int, phys: int) -> dict[str, dict]:
        return self._memo(("pstats", match_id, player_id, through, phys), lambda: self._player_stats_at(match_id, player_id, through, phys))

    def _player_stats_at(self, match_id: str, pid: str, through: int, phys: int) -> dict[str, dict]:
        p = next(q for q in self.roster(match_id) if q["id"] == pid)
        ph = self._physical(match_id, pid, phys) or {}
        sensor = self._sensor(match_id, through)
        mine = [e for e in sensor if e["player_id"] == pid]
        passes = [e for e in mine if e["kind"] == "pass"]
        completed = sum(1 for e in passes if e["attrs"].get("completed"))
        received = sum(1 for e in sensor if e["kind"] == "pass" and e["target_player_id"] == pid and e["attrs"].get("completed"))
        count = lambda kind: sum(1 for e in mine if e["kind"] == kind)
        shots = self.shots(match_id, through, "all")
        own_shots = [s for s in shots if s["playerId"] == pid]
        events = [e for e in self.events(match_id, through) if e["status"] != "overturned"]
        played = ph.get("minutes", 0.0)
        hr = ph.get("hr_sum", 0) / ph["hr_n"] if ph.get("hr_n") else None
        gk = p["position"] == "GK"
        val = lambda v, source, unit=None: {"value": v, "unit": unit, "source": source}
        return {
            "minutes": val(round(played), "vest", "min"),
            "distance": val(round(ph.get("distance_m", 0)) / 1000, "vest", "km"),
            "topSpeed": val(r1(ph["top_speed_kmh"]) if ph.get("top_speed_kmh") else None, "vest", "km/h"),
            "sprints": val(ph.get("sprints", 0), "vest"),
            "highSpeedRun": val(round(ph.get("hsr_m", 0)), "vest", "m"),
            "avgHeartRate": val(round(hr) if hr else None, "vest", "bpm"),
            "touches": val(len(passes) + received + count("duel") + count("dribble") + len(own_shots), "fusion"),
            "passes": val(len(passes), "fusion"),
            "passAccuracy": val(round(100 * completed / len(passes)) if passes else None, "fusion", "%"),
            "progressivePasses": val(sum(1 for e in passes if e["attrs"].get("progressive")), "fusion"),
            "shots": val(len(own_shots), "fusion"),
            "goals": val(sum(1 for s in own_shots if s["outcome"] == "goal"), "fusion"),
            "assists": val(sum(1 for e in events if e["type"] == "goal" and e["playerIds"][1:2] == [pid]), "fusion"),
            "xg": val(r2(sum(s["xg"] for s in own_shots)), "model"),
            "duelsWon": val(count("duel"), "fusion"),
            "recoveries": val(count("recovery") + count("press_win"), "fusion"),
            "saves": val(sum(1 for s in shots if s["team"] != p["team"] and s["outcome"] == "saved") if gk else None, "fusion"),
            "yellow": val(sum(1 for e in events if e["type"] == "yellow" and e["playerIds"][:1] == [pid]), "llm_vision"),
            "red": val(sum(1 for e in events if e["type"] == "red" and e["playerIds"][:1] == [pid]), "llm_vision"),
        }

    def player_stats(self, match_id: str, player_id: str, playhead: int) -> dict[str, dict]:
        return self.player_stats_at(match_id, player_id, snapshot_through(playhead, self.timing(match_id)), physical_through(playhead, self.timing(match_id)))

    def fantasy_points(self, match_id: str, player_id: str, playhead: int) -> int:
        through = snapshot_through(playhead, self.timing(match_id))
        minutes = self.player_stats(match_id, player_id, playhead)["minutes"]["value"] or 0
        pts = 0 if minutes == 0 else 2 if minutes >= 60 else 1
        for e in self.events(match_id, through):
            if e["status"] != "overturned":
                pts += (e.get("fantasyPoints") or {}).get(player_id, 0)
        return pts

    def heatmap(self, match_id: str, player_id: str, playhead: int) -> list[list[float]]:
        phys = physical_through(playhead, self.timing(match_id))
        rows = self.db.query("SELECT x, y FROM vest_frames WHERE match_id = ? AND player_id = ? AND media_ms <= ? AND on_pitch = 1",
                             (match_id, player_id, phys))
        if not rows:
            return []
        grid = [[0.0] * 6 for _ in range(4)]
        for r in rows:
            grid[min(3, int(r["y"] / 25))][min(5, int(r["x"] / (100 / 6)))] += 1
        peak = max(max(row) for row in grid) or 1
        return [[r2(v / peak) for v in row] for row in grid]

    # --- Team stats ---------------------------------------------------------------------------------------------

    def _team_window(self, match_id: str, side: str, through: int, period: Period, key: str) -> float:
        a, b = bounds(through, period)
        total = 0.0
        for p in self.roster(match_id):
            if p["team"] != side:
                continue
            end = self.player_stats_at(match_id, p["id"], b, b)[key]["value"] or 0
            start = self.player_stats_at(match_id, p["id"], a, a)[key]["value"] or 0 if a else 0
            total += end - start
        return total

    def chips(self, match_id: str, playhead: int) -> dict[str, dict]:
        """Watch-page stat chips (whole match so far)."""
        data = self.sections(match_id, snapshot_through(playhead, self.timing(match_id)), "all")
        row = lambda sec, label: next(r for s in data["sections"] if s["id"] == sec for r in s["rows"] if r["label"] == label)
        pick = lambda sec, label, source, unit=None: {"home": row(sec, label)["home"], "away": row(sec, label)["away"], "unit": unit, "source": source}
        return {
            "possession": {"home": data["possession"][0], "away": data["possession"][1], "unit": "%", "source": "fusion"},
            "xg": pick("xg", "Expected goals (xG)", "model"),
            "shots": pick("shots", "Total shots", "ball"),
            "bigChances": pick("top", "Big chances", "fusion"),
            "passes": pick("passes", "Passes", "fusion"),
            "highRegains": pick("defence", "High regains", "fusion"),
            "ppda": {**data["ppda"], "source": "fusion"},
            "corners": pick("top", "Corners", "llm_vision"),
            "fouls": pick("discipline", "Fouls committed", "llm_vision"),
            "yellow": pick("discipline", "Yellow cards", "llm_vision"),
            "red": pick("discipline", "Red cards", "llm_vision"),
            "distance": pick("physical", "Distance covered", "vest", "km"),
            "sprints": pick("physical", "Number of sprints", "vest"),
            "lineHeight": {**data["lineHeight"], "unit": "m", "source": "vest"},
        }

    def sections(self, match_id: str, through: int, period: Period) -> dict:
        return self._memo(("sections", match_id, through, period), lambda: self._sections(match_id, through, period))

    def _sections(self, match_id: str, through: int, period: Period) -> dict:
        a, b = bounds(through, period)
        mins = max(0.0, (b - a) / 60_000)
        shots = self.shots(match_id, through, period)
        sensor = [e for e in self._sensor(match_id, through) if a <= e["media_ms"] <= b]
        vision = [o for o in self._vision(match_id, through) if a <= o["media_from_ms"] <= b]
        events = [e for e in self.events(match_id, through) if a <= e["atMs"] <= b and e["status"] != "overturned"]
        sides = ("home", "away")
        both = lambda f: {"home": f("home"), "away": f("away")}
        by = lambda side: [s for s in shots if s["team"] == side]
        kind = lambda side, k, cond=lambda e: True: sum(1 for e in sensor if e["team"] == side and e["kind"] == k and cond(e))
        vis = lambda side, k: sum(1 for o in vision if o["team"] == side and o["kind"] == k)
        ev = lambda side, t: sum(1 for e in events if e["team"] == side and e["type"] == t)
        other = lambda side: "away" if side == "home" else "home"

        passes = both(lambda s: kind(s, "pass"))
        acc = both(lambda s: kind(s, "pass", lambda e: e["attrs"].get("completed")))
        possession = round(100 * passes["home"] / (passes["home"] + passes["away"])) if passes["home"] + passes["away"] else 50
        on_target = both(lambda s: sum(1 for x in by(s) if x["outcome"] in ("goal", "saved")))
        xg = both(lambda s: r2(sum(x["xg"] for x in by(s))))
        set_xg = both(lambda s: r2(sum(x["xg"] for x in by(s) if x["situation"] == "Set piece")))
        dist = both(lambda s: r1(self._team_window(match_id, s, through, period, "distance")))
        sprints = both(lambda s: int(self._team_window(match_id, s, through, period, "sprints")))
        hsr = both(lambda s: r1(self._team_window(match_id, s, through, period, "highSpeedRun") / 1000))
        duels = both(lambda s: kind(s, "duel"))
        ground = both(lambda s: kind(s, "duel", lambda e: not e["attrs"].get("aerial")))
        aerial = both(lambda s: kind(s, "duel", lambda e: e["attrs"].get("aerial")))
        players = [p for p in self.roster(match_id)]
        top_speed = both(lambda s: max([self.player_stats_at(match_id, p["id"], b, b)["topSpeed"]["value"] or 0
                                        for p in players if p["team"] == s] or [0]) or None)
        hr = both(lambda s: self._team_hr(match_id, s, b))
        defensive = both(lambda s: kind(s, "tackle") + kind(s, "interception") + vis(s, "foul"))
        ppda = both(lambda s: r1(kind(other(s), "pass", lambda e: e["attrs"].get("half") == "own") / defensive[s]) if defensive[s] and mins >= 5 else None)
        opp_half = lambda s: [e for e in sensor if e["team"] == s and e["kind"] == "pass" and e["attrs"].get("half") == "opposition"]
        zones = {}
        for s in sides:
            ys = [e["attrs"].get("y", 50) for e in opp_half(s)]
            n = len(ys) or 1
            left, right = sum(1 for y in ys if y < 33.3), sum(1 for y in ys if y > 66.6)
            zones[s] = [round(100 * left / n), 100 - round(100 * left / n) - round(100 * right / n), round(100 * right / n)] if ys else [33, 34, 33]

        sections = [
            {"id": "top", "title": "Top stats", "source": "fusion", "rows": [
                {"label": "Expected goals (xG)", **xg, "fmt": "dec"},
                {"label": "Total shots", **both(lambda s: len(by(s)))},
                {"label": "Shots on target", **on_target},
                {"label": "Big chances", **both(lambda s: sum(1 for x in by(s) if x["xg"] >= 0.3))},
                {"label": "Accurate passes", **acc, "homeTotal": passes["home"], "awayTotal": passes["away"], "fmt": "ofTotal"},
                {"label": "Touches in opposition box", **both(lambda s: round(len(opp_half(s)) * 0.12))},
                {"label": "Corners", **both(lambda s: vis(s, "corner"))},
                {"label": "Yellow cards", **both(lambda s: ev(s, "yellow")), "lowerIsBetter": True},
            ]},
            {"id": "physical", "title": "Physical performance", "source": "vest", "rows": [
                {"label": "Distance covered", **dist, "fmt": "km"},
                {"label": "High-speed running (> 19.8 km/h)", **hsr, "fmt": "km"},
                {"label": "Number of sprints", **sprints},
                {"label": "Top speed", **top_speed, "fmt": "kmh"},
                {"label": "Avg heart rate", **hr, "fmt": "bpm", "lowerIsBetter": True},
            ]},
            {"id": "xg", "title": "Expected goals (xG)", "source": "model", "rows": [
                {"label": "Expected goals (xG)", **xg, "fmt": "dec"},
                {"label": "xG open play", **both(lambda s: r2(xg[s] - set_xg[s])), "fmt": "dec"},
                {"label": "xG set play", **set_xg, "fmt": "dec"},
                {"label": "xG on target (xGOT)", **both(lambda s: r2(sum(x["xgot"] or 0 for x in by(s)))), "fmt": "dec"},
            ]},
            {"id": "shots", "title": "Shots", "source": "ball", "rows": [
                {"label": "Total shots", **both(lambda s: len(by(s)))},
                {"label": "Shots on target", **on_target},
                {"label": "Shots off target", **both(lambda s: sum(1 for x in by(s) if x["outcome"] == "off"))},
                {"label": "Blocked shots", **both(lambda s: sum(1 for x in by(s) if x["outcome"] == "blocked"))},
                {"label": "Hit woodwork", **both(lambda s: sum(1 for x in by(s) if x["outcome"] == "woodwork"))},
                {"label": "Shots inside box", **both(lambda s: sum(1 for x in by(s) if x["x"] >= 83))},
                {"label": "Shots outside box", **both(lambda s: sum(1 for x in by(s) if x["x"] < 83))},
                {"label": "Fastest shot", **both(lambda s: max([x["speedKmh"] for x in by(s)] or [0]) or None), "fmt": "kmh"},
            ]},
            {"id": "passes", "title": "Passes", "source": "fusion", "rows": [
                {"label": "Passes", **passes},
                {"label": "Accurate passes", **acc, "homeTotal": passes["home"], "awayTotal": passes["away"], "fmt": "ofTotal"},
                {"label": "Own half", **both(lambda s: kind(s, "pass", lambda e: e["attrs"].get("half") == "own"))},
                {"label": "Opposition half", **both(lambda s: len(opp_half(s)))},
                {"label": "Progressive passes", **both(lambda s: kind(s, "pass", lambda e: e["attrs"].get("progressive")))},
                {"label": "Accurate long balls", **both(lambda s: kind(s, "pass", lambda e: e["attrs"].get("long") and e["attrs"].get("completed")))},
                {"label": "Accurate crosses", **both(lambda s: sum(1 for e in opp_half(s) if e["attrs"].get("completed") and not 15 <= e["attrs"].get("y", 50) <= 85))},
            ]},
            {"id": "duels", "title": "Duels", "source": "fusion", "rows": [
                {"label": "Duels won", **duels},
                {"label": "Ground duels won", **ground, "homeTotal": ground["home"] + ground["away"], "awayTotal": ground["home"] + ground["away"], "fmt": "ofTotal"},
                {"label": "Aerial duels won", **aerial, "homeTotal": aerial["home"] + aerial["away"], "awayTotal": aerial["home"] + aerial["away"], "fmt": "ofTotal"},
                {"label": "Successful dribbles", **both(lambda s: kind(s, "dribble"))},
            ]},
            {"id": "defence", "title": "Defence", "source": "fusion", "rows": [
                {"label": "High regains", **both(lambda s: kind(s, "press_win") + round(kind(s, "recovery") * 0.35))},
                {"label": "Tackles", **both(lambda s: kind(s, "tackle"))},
                {"label": "Interceptions", **both(lambda s: kind(s, "interception"))},
                {"label": "Blocks", **both(lambda s: sum(1 for x in by(other(s)) if x["outcome"] == "blocked"))},
                {"label": "Clearances", **both(lambda s: kind(s, "clearance"))},
                {"label": "Keeper saves", **both(lambda s: sum(1 for x in by(other(s)) if x["outcome"] == "saved"))},
            ]},
            {"id": "discipline", "title": "Discipline", "source": "llm_vision", "rows": [
                {"label": "Fouls committed", **both(lambda s: vis(s, "foul")), "lowerIsBetter": True},
                {"label": "Yellow cards", **both(lambda s: ev(s, "yellow")), "lowerIsBetter": True},
                {"label": "Red cards", **both(lambda s: ev(s, "red")), "lowerIsBetter": True},
                {"label": "Free kicks won", **both(lambda s: vis(s, "free_kick"))},
                {"label": "Offsides", **both(lambda s: ev(s, "offside")), "lowerIsBetter": True},
            ]},
        ]
        return {"possession": [possession, 100 - possession], "sections": sections, "zones": zones, "ppda": ppda,
                "lineHeight": both(lambda s: self._line_height(match_id, s, b) if mins >= 5 else None)}

    def _team_hr(self, match_id: str, side: str, at: int) -> int | None:
        total = n = 0
        for p in self.roster(match_id):
            if p["team"] == side:
                ph = self._physical(match_id, p["id"], at // PHYSICAL_SNAPSHOT_MS * PHYSICAL_SNAPSHOT_MS)
                if ph and ph["hr_n"]:
                    total += ph["hr_sum"]
                    n += ph["hr_n"]
        return round(total / n) if n else None

    def _line_height(self, match_id: str, side: str, at: int) -> float | None:
        """Mean distance of the back line from its own goal over the last 5 minutes (vest positions)."""
        defenders = [p["id"] for p in self.roster(match_id) if p["team"] == side and p["position"] == "DF"]
        if not defenders:
            return None
        marks = ",".join("?" * len(defenders))
        row = self.db.query(f"SELECT AVG(x) AS x FROM vest_frames WHERE match_id = ? AND on_pitch = 1 AND media_ms > ? AND media_ms <= ? "
                            f"AND player_id IN ({marks})", (match_id, at - 300_000, at, *defenders))
        return round(row[0]["x"] * 1.05) if row and row[0]["x"] is not None else None

    # --- Player table -------------------------------------------------------------------------------------------

    DIFF_KEYS = {"minutes", "distance", "sprints", "highSpeedRun", "touches", "passes", "progressivePasses", "duelsWon",
                 "recoveries", "yellow", "red"}

    def player_rows(self, match_id: str, through: int, period: Period, playhead: int) -> list[dict]:
        a, b = bounds(through, period)
        shots = self.shots(match_id, through, period)
        events = [e for e in self.events(match_id, through) if a <= e["atMs"] <= b and e["status"] != "overturned"]
        rows = []
        for p in self.roster(match_id):
            end = self.player_stats_at(match_id, p["id"], b, b)
            if not p["startsOn"] and not end["minutes"]["value"]:
                continue
            start = self.player_stats_at(match_id, p["id"], a, a) if a else None
            values: dict[str, float | None] = {}
            for k, v in end.items():
                s = (start[k]["value"] or 0) if start else 0
                values[k] = None if v["value"] is None else r2(v["value"] - s) if k in self.DIFF_KEYS else v["value"]
            mine = [s for s in shots if s["playerId"] == p["id"]]
            values.update(
                shots=len(mine), onTarget=sum(1 for s in mine if s["outcome"] in ("goal", "saved")),
                goals=sum(1 for s in mine if s["outcome"] == "goal"), xg=r2(sum(s["xg"] for s in mine)),
                fastestShot=max([s["speedKmh"] for s in mine]) if mine else None,
                assists=sum(1 for e in events if e["type"] == "goal" and e["playerIds"][1:2] == [p["id"]]),
                saves=sum(1 for s in shots if s["team"] != p["team"] and s["outcome"] == "saved") if p["position"] == "GK" else None,
                fantasy=self.fantasy_points(match_id, p["id"], playhead),
            )
            mins = values.get("minutes") or 0
            rating = None if mins < 10 else min(10.0, max(3.0, 6.2 + values["goals"] + values["assists"] * 0.7 + values["xg"] * 0.6
                     + (values.get("recoveries") or 0) * 0.04 + (values.get("progressivePasses") or 0) * 0.05
                     + (values.get("saves") or 0) * 0.3 - (values.get("yellow") or 0) * 0.4 - (values.get("red") or 0) * 2
                     + (fnv(p["id"]) - 0.5) * 0.6))
            rows.append({"id": p["id"], "name": p["name"], "shirt": p["shirt"], "team": p["team"], "position": p["position"],
                         "rating": None if rating is None else r1(rating), "values": values})
        return rows

    # --- Match pulse ------------------------------------------------------------------------------------------------

    def pulse(self, match_id: str, playhead: int) -> list[dict]:
        """Per-minute field tilt from opposition-half passes, bumped by chances: −1 away … +1 home."""
        minute_now = playhead // 60_000
        sensor = self._sensor(match_id, minute_now * 60_000)
        tilt: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for e in sensor:
            if e["kind"] == "pass" and e["attrs"].get("half") == "opposition":
                tilt[e["media_ms"] // 60_000][0 if e["team"] == "home" else 1] += 1
        bumps: dict[int, float] = defaultdict(float)
        for s in self.shots(match_id, minute_now * 60_000, "all"):
            bumps[s["atMs"] // 60_000] += (1 if s["team"] == "home" else -1) * (0.5 if s["outcome"] == "goal" else 0.2)
        out = []
        for m in range(minute_now + 1):
            h, a = tilt[m]
            v = (h - a) / (h + a + 4) * 2 + bumps[m]
            out.append({"minute": m, "value": max(-1.0, min(1.0, r2(v)))})
        return out

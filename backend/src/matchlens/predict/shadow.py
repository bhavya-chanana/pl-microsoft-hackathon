"""Shadow predictions for real matches from the event feed (FotMob): issued live, scored after full time.

The event feed is 20-30 s behind and has no positions, so it cannot drive the
next-10-seconds model; it can still be predicted honestly at the minute level.
Every minute of a live match, for each team, this issues:

    shot_1m   P(the team shoots in the next minute)
    shot_5m   P(the team shoots in the next 5 minutes)
    goal_5m   P(the team scores in the next 5 minutes)

from a discrete-time hazard model: one L2 logistic regression per head over
the match state at the end of minute m (minutes are 0-based floors: 0 is the
first minute). Features, all from the team's point of view:

    minute, late (m >= 80), score difference, trailing, pre-match strength
    (expected score: bookmaker odds in training, ClubElo live), home, shots
    and xG in the last 10 minutes for and against, shot rates so far, red
    cards difference.

It is fitted on Understat shot timelines with football-data closing odds
from the curated league warehouse (``leaguedata``). Own goals are left out of
both training and scoring, since neither side shot them.

Each prediction is written to the next10 store (``store/next10.py``,
``predictions``) when it is issued, with ``t_issued``, before its outcome
exists. After full time ``score`` labels every prediction from FotMob's final
shot map and compares the model with two baselines: the training base rate,
and persistence (the team's own rate so far in this match). Brier score and
log loss, per head.

    python -m matchlens.predict.shadow fit                       # (re)fit from the curated warehouse
    python -m matchlens.predict.shadow fixtures --date 2026-10-28
    python -m matchlens.predict.shadow watch --date 2026-10-28   # runs until every match is scored
    python -m matchlens.predict.shadow score --match fotmob:6275371

Needs ``MATCHLENS_FOTMOB=1`` for anything that calls FotMob.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import logging
import math
import re
import time
import unicodedata
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[3]
LOCAL = BACKEND_DIR / "data" / "local"
MODEL_PATH = LOCAL / "next10" / "shadow_model.json"
SHADOW_DIR = LOCAL / "next10" / "shadow"
ELO_DIR = LOCAL / "next10" / "clubelo"
DAYS_DIR = LOCAL / "fotmob" / "days"
LEAGUEDATA = LOCAL / "leaguedata"
# A warehouse built for this model alone (raw linked from LEAGUEDATA), so rebuilding it never replaces the
# tables the app's playbooks and knowledge base read.
WAREHOUSE = LOCAL / "next10" / "warehouse"

FEATURES = ("minute", "late", "score_diff", "trailing", "strength", "home", "shots10_for", "shots10_against",
            "xg10_for", "xg10_against", "shot_rate_for", "shot_rate_against", "reds_diff")
HEADS = {"shot_1m": ("shot", 1), "shot_5m": ("shot", 5), "goal_5m": ("goal", 5)}
MODEL_FAMILY = "shadow-hazard-v1"
POLL_S = 60.0  # the FotMob adapter allows 30 s; once a minute is enough for minute-level predictions
HFA_ELO = 60.0  # home advantage in Elo points

# FotMob team ids of the 12 clubs (men's first teams).
CLUBS: dict[int, str] = {
    8650: "Liverpool", 8456: "Manchester City", 10204: "Brighton & Hove Albion", 10252: "Aston Villa",
    9825: "Arsenal", 8634: "Barcelona", 8633: "Real Madrid", 9906: "Atlético Madrid", 9823: "Bayern München",
    178475: "RB Leipzig", 9847: "Paris Saint-Germain", 9773: "FC Porto",
}
# FotMob name -> ClubElo name where they differ after normalisation.
ELO_ALIASES = {"manchestercity": "mancity", "brightonhovealbion": "brighton", "afcbournemouth": "bournemouth",
               "bayernmunchen": "bayern", "fcporto": "porto", "parissaintgermain": "parissg",
               "atleticomadrid": "atletico", "rbleipzig": "rbleipzig", "manchesterunited": "manunited",
               "tottenhamhotspur": "tottenham", "wolverhamptonwanderers": "wolves", "newcastleunited": "newcastle",
               "nottinghamforest": "forest", "westhamunited": "westham", "academicoviseu": "academicoviseu"}


# ------------------------------------------------------------------ match state and features


@dataclass
class Shot:
    team: str  # "home" | "away"
    minute: int  # 0-based floor minute
    xg: float
    goal: bool


@dataclass
class MatchState:
    """What the event feed has said up to now; the same shape for training and live."""

    shots: list[Shot] = field(default_factory=list)
    reds: list[tuple[str, int]] = field(default_factory=list)  # (team, minute)
    strength_home: float | None = None  # expected score of the home side, 0..1

    def score(self, side: str, upto: int) -> int:
        return sum(1 for s in self.shots if s.team == side and s.goal and s.minute <= upto)


def other(side: str) -> str:
    return "away" if side == "home" else "home"


def features(state: MatchState, side: str, m: int) -> dict[str, float]:
    """The match as seen at the end of minute ``m``, from ``side``'s point of view."""
    opp = other(side)
    seen = [s for s in state.shots if s.minute <= m]
    recent = [s for s in seen if s.minute > m - 10]
    diff = state.score(side, m) - state.score(opp, m)
    strength = 0.5 if state.strength_home is None else (
        state.strength_home if side == "home" else 1.0 - state.strength_home)
    reds = sum(1 for t, mm in state.reds if mm <= m and t == side) - sum(
        1 for t, mm in state.reds if mm <= m and t == opp)
    return {
        "minute": m / 90.0,
        "late": float(m >= 80),
        "score_diff": float(max(-3, min(3, diff))),
        "trailing": float(diff < 0),
        "strength": strength - 0.5,
        "home": float(side == "home"),
        "shots10_for": float(sum(1 for s in recent if s.team == side)),
        "shots10_against": float(sum(1 for s in recent if s.team == opp)),
        "xg10_for": sum(s.xg for s in recent if s.team == side),
        "xg10_against": sum(s.xg for s in recent if s.team == opp),
        "shot_rate_for": sum(1 for s in seen if s.team == side) / (m + 1),
        "shot_rate_against": sum(1 for s in seen if s.team == opp) / (m + 1),
        "reds_diff": float(max(-2, min(2, reds))),
    }


def label(state: MatchState, side: str, m: int, head: str) -> int:
    kind, horizon = HEADS[head]
    return int(any(s.team == side and m < s.minute <= m + horizon and (kind == "shot" or s.goal)
                   for s in state.shots))


def persistence(state: MatchState, side: str, m: int, head: str) -> float:
    """The team's own rate so far, extended over the horizon (one of the two baselines)."""
    kind, horizon = HEADS[head]
    seen = [s for s in state.shots if s.team == side and s.minute <= m]
    rate = (len(seen) if kind == "shot" else sum(s.xg for s in seen)) / (m + 1)
    return clip(1.0 - math.exp(-rate * horizon))


def clip(p: float, eps: float = 1e-4) -> float:
    return min(1 - eps, max(eps, p))


# ------------------------------------------------------------------ model


@dataclass
class HazardModel:
    mean: np.ndarray
    std: np.ndarray
    coef: dict[str, np.ndarray]  # head -> [bias, w...]
    base_rate: dict[str, float]
    meta: dict[str, Any]

    @property
    def version(self) -> str:
        return self.meta["version"]

    def predict(self, feats: dict[str, float]) -> dict[str, float]:
        x = (np.array([feats[f] for f in FEATURES]) - self.mean) / self.std
        return {h: clip(float(1 / (1 + np.exp(-(w[0] + x @ w[1:]))))) for h, w in self.coef.items()}

    def save(self, path: Path = MODEL_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"features": FEATURES, "mean": self.mean.tolist(), "std": self.std.tolist(),
                                    "coef": {h: w.tolist() for h, w in self.coef.items()},
                                    "base_rate": self.base_rate, "meta": self.meta}, indent=2))

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> HazardModel:
        d = json.loads(path.read_text())
        if tuple(d["features"]) != FEATURES:
            raise ValueError(f"{path}: fitted on other features; re-run fit")
        return cls(np.array(d["mean"]), np.array(d["std"]), {h: np.array(w) for h, w in d["coef"].items()},
                   d["base_rate"], d["meta"])


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 30) -> np.ndarray:
    """L2-regularised logistic regression by Newton's method; returns [bias, w...]. The bias is not penalised."""
    xb = np.hstack([np.ones((len(x), 1)), x])
    w = np.zeros(xb.shape[1])
    w[0] = math.log(max(y.mean(), 1e-6) / max(1 - y.mean(), 1e-6))
    reg = np.full(xb.shape[1], l2)
    reg[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(xb @ w)))
        grad = xb.T @ (p - y) + reg * w
        hess = (xb * (p * (1 - p))[:, None]).T @ xb + np.diag(reg)
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def panel(states: Iterable[tuple[str, MatchState, int]]) -> tuple[np.ndarray, dict[str, np.ndarray], list[str]]:
    """Rows of (match, side, minute) for every minute whose 5-minute window ends before the match does."""
    rows, labels, keys = [], {h: [] for h in HEADS}, []
    for key, state, end in states:
        for side in ("home", "away"):
            for m in range(0, end - 5):
                f = features(state, side, m)
                rows.append([f[k] for k in FEATURES])
                for h in HEADS:
                    labels[h].append(label(state, side, m, h))
                keys.append(key)
    return np.array(rows, dtype=float), {h: np.array(v, dtype=float) for h, v in labels.items()}, keys


def held_out(key: str) -> bool:
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 5 == 0


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(p: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def fit(states: Sequence[tuple[str, MatchState, int]], source: str) -> tuple[HazardModel, dict[str, Any]]:
    """Fit on 80% of matches (by hash), report on the other 20%, then refit on everything."""
    x, ys, keys = panel(states)
    if not len(x):
        raise ValueError("no training rows")
    test = np.array([held_out(k) for k in keys])
    report: dict[str, Any] = {"matches": len(states), "rows": int(len(x)), "test_rows": int(test.sum()), "heads": {}}

    def standardise(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mean, std = rows.mean(axis=0), rows.std(axis=0)
        return mean, np.where(std > 1e-9, std, 1.0)

    mean, std = standardise(x[~test])
    for h, y in ys.items():
        w = fit_logistic((x[~test] - mean) / std, y[~test])
        p = 1 / (1 + np.exp(-(w[0] + ((x[test] - mean) / std) @ w[1:])))
        base = np.full(test.sum(), y[~test].mean())
        report["heads"][h] = {"base_rate": float(y[~test].mean()), "auc": auc(p, y[test]),
                              "brier": brier(p, y[test]), "brier_base": brier(base, y[test]),
                              "logloss": logloss(p, y[test]), "logloss_base": logloss(base, y[test])}
    mean, std = standardise(x)
    coef = {h: fit_logistic((x - mean) / std, y) for h, y in ys.items()}
    digest = hashlib.sha256(json.dumps({h: w.round(6).tolist() for h, w in coef.items()}).encode()).hexdigest()
    meta = {"version": f"{MODEL_FAMILY}+{digest[:8]}", "fitted_at": datetime.now(UTC).isoformat(),
            "source": source, "matches": len(states), "rows": int(len(x)), "holdout": report["heads"]}
    return HazardModel(mean, std, coef, {h: float(y.mean()) for h, y in ys.items()}, meta), report


def warehouse_states(root: Path = LEAGUEDATA) -> list[tuple[str, MatchState, int]]:
    """Training matches from the latest curated warehouse: every match with shots and closing odds."""
    import duckdb  # leaguedata dependency group

    version = (root / "curated" / "LATEST").read_text().strip()
    tables = root / "curated" / f"v{version}" / "tables"
    con = duckdb.connect()
    matches = con.execute(
        f"SELECT match_key, home_id, away_id, p_home, p_draw FROM '{tables}/matches.parquet' "
        "WHERE has_shots AND has_odds AND p_home IS NOT NULL").fetchall()
    shots: dict[str, list[tuple]] = {}
    for row in con.execute(f"SELECT match_key, team_id, minute, xg, result FROM '{tables}/shots.parquet'").fetchall():
        shots.setdefault(row[0], []).append(row[1:])
    reds: dict[str, list[tuple]] = {}
    if (tables / "match_events.parquet").exists():  # cards come from Transfermarkt; without it reds_diff is constant
        for row in con.execute(f"SELECT match_key, team_id, minute FROM '{tables}/match_events.parquet' "
                               "WHERE kind IN ('red', 'second_yellow')").fetchall():
            reds.setdefault(row[0], []).append(row[1:])
    out = []
    for key, home, away, p_home, p_draw in matches:
        if key not in shots:
            continue
        side = {home: "home", away: "away"}
        state = MatchState(
            shots=[Shot(side[t], int(mi), float(xg or 0.0), r == "goal") for t, mi, xg, r in shots[key] if t in side],
            reds=[(side[t], int(mi)) for t, mi in reds.get(key, []) if t in side and mi is not None],
            strength_home=float(p_home) + 0.5 * float(p_draw or 0.0))
        end = max([93] + [s.minute + 1 for s in state.shots])
        out.append((key, state, end))
    return out


# ------------------------------------------------------------------ FotMob live state


def fotmob_minute(minute: Any, added: Any) -> int:
    """FotMob shows 1-based minutes with added time apart (45+2 is min 45, added 2); ours are 0-based floors."""
    return max(0, int(minute or 0) - 1 + int(added or 0))


def live_minute(details: dict[str, Any]) -> int | None:
    status = details.get("header", {}).get("status", {})
    if status.get("finished"):
        return None
    short = (status.get("liveTime") or {}).get("short") or ""
    m = re.match(r"\s*(\d+)(?:\s*\+\s*(\d+))?", short)
    if m:
        return fotmob_minute(m.group(1), m.group(2))
    return None  # half-time, not started: nothing new to predict


def fotmob_state(details: dict[str, Any], strength_home: float | None) -> MatchState:
    teams = details["header"]["teams"]
    side = {teams[0]["id"]: "home", teams[1]["id"]: "away"}
    shots = [Shot(side[s["teamId"]], fotmob_minute(s.get("min"), s.get("minAdded")),
                  float(s.get("expectedGoals") or 0.0), s.get("eventType") == "Goal")
             for s in ((details.get("content") or {}).get("shotmap") or {}).get("shots") or []
             if s.get("teamId") in side and not s.get("isOwnGoal")]
    events = ((details.get("content") or {}).get("matchFacts") or {}).get("events") or {}
    reds = [("home" if e.get("isHome") else "away", fotmob_minute(e.get("time"), e.get("overloadTime")))
            for e in events.get("events") or [] if e.get("type") == "Card" and e.get("card") in ("Red", "YellowRed")]
    return MatchState(shots=sorted(shots, key=lambda s: s.minute), reds=reds, strength_home=strength_home)


# ------------------------------------------------------------------ pre-match strength (ClubElo)


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z]", "", s)
    return ELO_ALIASES.get(s, s)


def clubelo(day: date, directory: Path = ELO_DIR, back_days: int = 7) -> tuple[dict[str, float], str] | None:
    """Elo of every club on ``day`` (or the latest snapshot in the week before), cached locally."""
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(back_days + 1):
        d = day - timedelta(days=i)
        path = directory / f"{d.isoformat()}.csv"
        if not (path.exists() and path.stat().st_size > 100):
            try:
                req = urllib.request.Request(f"http://api.clubelo.com/{d.isoformat()}",
                                             headers={"User-Agent": "matchlens-research/0.1"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    path.write_bytes(resp.read())
                time.sleep(1.0)
            except OSError as exc:
                log.warning("ClubElo %s unavailable: %s", d, exc)
                continue
        rows = list(csv.DictReader(io.StringIO(path.read_text())))
        if rows and "Elo" in rows[0]:
            return {norm(r["Club"]): float(r["Elo"]) for r in rows if r.get("Elo")}, d.isoformat()
    return None


def expected_home(elo: dict[str, float] | None, home: str, away: str) -> tuple[float | None, str]:
    if not elo:
        return None, "none"
    h, a = elo.get(norm(home)), elo.get(norm(away))
    if h is None or a is None:
        # A club ClubElo does not rate is lower-league: place it below the weakest rated club in the snapshot.
        floor = min(elo.values()) - 50
        h, a = h if h is not None else floor, a if a is not None else floor
        source = "clubelo+floor"
    else:
        source = "clubelo"
    return 1 / (1 + 10 ** (-(h - a + HFA_ELO) / 400)), source


# ------------------------------------------------------------------ fixtures, documents, watch, score


@dataclass(frozen=True)
class Fixture:
    fotmob_id: int
    home: str
    away: str
    home_id: int
    away_id: int
    competition: str
    kickoff_utc: str

    @property
    def match_id(self) -> str:
        return f"fotmob:{self.fotmob_id}"


def day_listing(day: date, directory: Path = DAYS_DIR, refresh: bool = False) -> dict[str, Any]:
    path = directory / f"{day.strftime('%Y%m%d')}.json"
    if refresh or not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(f"https://www.fotmob.com/api/data/matches?date={day.strftime('%Y%m%d')}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            path.write_bytes(resp.read())
    return json.loads(path.read_text())


def fixtures(listing: dict[str, Any], clubs: dict[int, str] = CLUBS) -> list[Fixture]:
    """Matches on the day involving one of the clubs, by FotMob team id (so namesakes and women's teams stay out)."""
    out = []
    for league in listing.get("leagues") or []:
        for m in league.get("matches") or []:
            h, a = m["home"], m["away"]
            if h["id"] in clubs or a["id"] in clubs:
                out.append(Fixture(int(m["id"]), h.get("longName") or h["name"], a.get("longName") or a["name"],
                                   int(h["id"]), int(a["id"]), league.get("name", ""), m["status"]["utcTime"]))
    return sorted(out, key=lambda f: f.kickoff_utc)


def match_doc(fx: Fixture, details: dict[str, Any] | None, strength: dict[str, Any]) -> dict[str, Any]:
    status = (details or {}).get("header", {}).get("status", {})
    teams = (details or {}).get("header", {}).get("teams") or [{}, {}]
    state = "finished" if status.get("finished") else "live" if status.get("started") else "upcoming"
    return {"id": fx.match_id, "match_id": fx.match_id, "source": "fotmob", "competition": fx.competition,
            "kickoff_utc": fx.kickoff_utc, "status": state,
            "home": {"name": fx.home, "fotmob_id": fx.home_id}, "away": {"name": fx.away, "fotmob_id": fx.away_id},
            "score": {"home": teams[0].get("score"), "away": teams[1].get("score")},
            "strength": strength, "updated_at": datetime.now(UTC).isoformat()}


def prediction_docs(fx: Fixture, state: MatchState, m: int, model: HazardModel) -> list[dict[str, Any]]:
    issued = datetime.now(UTC).isoformat()
    docs = []
    for side, team in (("home", fx.home), ("away", fx.away)):
        f = features(state, side, m)
        docs.append({"id": f"{fx.match_id}:shadow:{side}:{m}", "match_id": fx.match_id, "kind": "shadow_event",
                     "team_side": side, "team": team, "minute": m, "t_issued": issued,
                     "probs": model.predict(f), "model_version": model.version, "features": f})
    return docs


class Sink:
    """Writes to the next10 store and mirrors every document to a local JSON-lines file."""

    def __init__(self, store: Any | None, directory: Path = SHADOW_DIR) -> None:
        self.store = store
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    async def write(self, container: str, docs: list[dict[str, Any]]) -> None:
        if not docs:
            return
        with (self.directory / f"{container}.jsonl").open("a") as fh:
            for d in docs:
                fh.write(json.dumps(d) + "\n")
        if self.store is not None:
            try:
                await self.store.upsert(container, docs)
            except Exception as exc:  # noqa: BLE001 - the local mirror keeps the record; never stop the watch
                log.warning("store write to %s failed: %s", container, exc)


def score_match(details: dict[str, Any], predictions: Sequence[dict[str, Any]], model: HazardModel,
                match_id: str) -> dict[str, Any]:
    """Label every prediction from the final shot map and compare it with the base rate and persistence."""
    state = fotmob_state(details, None)
    heads: dict[str, Any] = {}
    for h in HEADS:
        rows = [(p["probs"][h], label(state, p["team_side"], p["minute"], h),
                 persistence(state, p["team_side"], p["minute"], h)) for p in predictions
                if p.get("kind") == "shadow_event" and h in p.get("probs", {})]
        if not rows:
            continue
        p, y, pers = (np.array(c, dtype=float) for c in zip(*rows))
        base = np.full(len(y), model.base_rate[h])
        heads[h] = {"n": int(len(y)), "positives": int(y.sum()), "base_rate": model.base_rate[h],
                    "observed_rate": float(y.mean()), "brier": brier(p, y), "logloss": logloss(p, y),
                    "brier_baseline": brier(base, y), "logloss_baseline": logloss(base, y),
                    "brier_persistence": brier(pers, y), "logloss_persistence": logloss(pers, y),
                    "auc": auc(p, y)}
    return {"id": f"score:{match_id}", "match_id": match_id, "kind": "score", "scored_at": datetime.now(UTC).isoformat(),
            "model_version": model.version, "heads": heads}


async def watch_fixture(fx: Fixture, model: HazardModel, sink: Sink, elo: dict[str, float] | None,
                        poll_s: float = POLL_S) -> dict[str, Any] | None:
    from matchlens.local import fotmob

    strength_home, source = expected_home(elo, fx.home, fx.away)
    strength = {"home_expected": strength_home, "source": source}
    kickoff = datetime.fromisoformat(fx.kickoff_utc.replace("Z", "+00:00"))
    wait = (kickoff - datetime.now(UTC)).total_seconds() - 120
    if wait > 0:
        log.info("%s v %s: waiting %.0f min for kick-off", fx.home, fx.away, wait / 60)
        await asyncio.sleep(wait)
    issued: list[dict[str, Any]] = []
    last_m: int | None = None
    while True:
        try:
            details = await asyncio.to_thread(fotmob.fetch_details, fx.fotmob_id)
        except Exception as exc:  # noqa: BLE001 - a failed poll must not end the watch
            log.warning("%s: poll failed: %s", fx.match_id, exc)
            await asyncio.sleep(poll_s)
            continue
        await sink.write("matches", [match_doc(fx, details, strength)])
        status = details.get("header", {}).get("status", {})
        if status.get("finished") or status.get("cancelled"):
            break
        m = live_minute(details)
        if m is not None and m != last_m and m < 120:
            docs = prediction_docs(fx, fotmob_state(details, strength_home), m, model)
            await sink.write("predictions", docs)
            issued.extend(docs)
            last_m = m
            log.info("%s %s' %s", fx.match_id, m + 1,
                     " | ".join(f"{d['team']}: shot1m {d['probs']['shot_1m']:.2f} goal5m {d['probs']['goal_5m']:.2f}"
                                for d in docs))
        await asyncio.sleep(poll_s)
    if not issued:
        return None
    result = score_match(details, issued, model, fx.match_id)
    await sink.write("predictions", [result])
    log.info("%s scored: %s", fx.match_id, json.dumps(result["heads"]))
    return result


# ------------------------------------------------------------------ CLI


def load_or_fit() -> HazardModel:
    if MODEL_PATH.exists():
        return HazardModel.load()
    model, report = fit(warehouse_states(), source="leaguedata curated (understat shots, football-data odds)")
    model.save()
    log.info("fitted %s: %s", model.version, json.dumps(report))
    return model


async def open_sink(no_store: bool) -> tuple[Sink, Any]:
    if no_store:
        return Sink(None), None
    from matchlens.store.next10 import Next10Settings, Next10Store

    store = await Next10Store(Next10Settings.from_env()).open()
    return Sink(store), store


def _require_fotmob() -> None:
    from matchlens.local import fotmob

    if not fotmob.enabled():
        raise SystemExit("set MATCHLENS_FOTMOB=1 to call FotMob")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="python -m matchlens.predict.shadow")
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit", help="fit the hazard model from the curated warehouse")
    f.add_argument("--warehouse", type=Path, default=WAREHOUSE if (WAREHOUSE / "curated" / "LATEST").exists()
                   else LEAGUEDATA, help="leaguedata folder with a curated/LATEST")
    for name in ("fixtures", "watch"):
        p = sub.add_parser(name)
        p.add_argument("--date", type=date.fromisoformat, default=datetime.now(UTC).date())
        p.add_argument("--refresh", action="store_true", help="re-download the day listing")
        if name == "watch":
            p.add_argument("--only", type=int, nargs="*", help="FotMob match ids to watch (default: all)")
            p.add_argument("--poll", type=float, default=POLL_S)
            p.add_argument("--no-store", action="store_true", help="write the local mirror only")
    s = sub.add_parser("score", help="score a finished match from its stored predictions")
    s.add_argument("--match", required=True, help="fotmob:<id>")
    s.add_argument("--no-store", action="store_true")
    args = parser.parse_args()

    if args.cmd == "fit":
        model, report = fit(warehouse_states(args.warehouse),
                            source=f"leaguedata curated at {args.warehouse} (understat shots, football-data odds)")
        model.save()
        print(json.dumps({"version": model.version, **report}, indent=2))
        return
    _require_fotmob()
    if args.cmd == "fixtures":
        for fx in fixtures(day_listing(args.date, refresh=args.refresh)):
            print(f"{fx.fotmob_id}  {fx.kickoff_utc}  {fx.competition}: {fx.home} v {fx.away}")
        return
    if args.cmd == "watch":
        asyncio.run(_watch(args))
        return
    asyncio.run(_score(args))


async def _watch(args: argparse.Namespace) -> None:
    model = load_or_fit()
    todo = [fx for fx in fixtures(day_listing(args.date, refresh=args.refresh))
            if not args.only or fx.fotmob_id in args.only]
    elo = clubelo(args.date)
    log.info("watching %d matches with %s; strength from %s", len(todo), model.version,
             f"ClubElo {elo[1]}" if elo else "nothing (ClubElo unavailable: neutral strength)")
    sink, store = await open_sink(args.no_store)
    try:
        results = await asyncio.gather(*(watch_fixture(fx, model, sink, elo[0] if elo else None, args.poll)
                                          for fx in todo))
    finally:
        if store is not None:
            await store.close()
    print(json.dumps([r for r in results if r], indent=2))


async def _score(args: argparse.Namespace) -> None:
    from matchlens.local import fotmob

    model = load_or_fit()
    fotmob_id = int(args.match.split(":")[-1])
    details = fotmob.load_details(fotmob_id) or await asyncio.to_thread(fotmob.fetch_details, fotmob_id)
    if not details.get("header", {}).get("status", {}).get("finished"):
        details = await asyncio.to_thread(fotmob.fetch_details, fotmob_id)
    mirror = SHADOW_DIR / "predictions.jsonl"
    preds = [d for d in (json.loads(x) for x in mirror.read_text().splitlines() if x.strip())
             if d.get("match_id") == args.match and d.get("kind") == "shadow_event"] if mirror.exists() else []
    # One prediction per (side, minute): the first issued.
    first: dict[str, dict[str, Any]] = {}
    for d in preds:
        first.setdefault(d["id"], d)
    result = score_match(details, list(first.values()), model, args.match)
    sink, store = await open_sink(args.no_store)
    try:
        await sink.write("predictions", [result])
    finally:
        if store is not None:
            await store.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

"""Next-10-seconds inference over tracking frames: frame docs in, ``kind: "next10"`` prediction docs out.

    uv run python -m matchlens.next10.predict clip:liv-bha-2627      # a stored match, 1 Hz over its frames
    uv run python -m matchlens.next10.predict test:lb --dry-run

The model is ``tacticsml.infer_next10`` (numpy; about 23 ms a frame). Its input is a ``TFrame`` in absolute metres
(0..105 x 0..68) with up to four earlier frames one second apart. Frame docs are centre-origin and already turned so
home attacks +x (``next10/chain.py``, second half flipped), so ``attacking_right = {home: True, away: False}``.

What the video gives the model, and what it does not:

* people and the ball as seen: only seen players are in the frame (a team has up to 11 slots; the rest are padded and
  the model knows). ``observed_ids`` is all of them. At most 11 per team are kept (the most confident);
* ``ball``: the detected ball, else the last one seen in the previous 3 s, else the centroid of the players (flagged
  ``features.ball_observed = false``);
* ``possession_team_id``: the team of the player nearest the ball (the carrier when within ``CARRIER_M``). A ball that
  has not been seen for 3 s keeps the last answer;
* goalkeepers: the deepest player of a team when inside its own penalty area (``is_gk``);
* velocities come from the history frames (the model derives them), so the first seconds of a match have none.

A frame is skipped (counted, not written) when it is not calibrated, has fewer than ``MIN_PLAYERS`` players, or has
fewer than ``MIN_PER_TEAM`` of either team (the model was trained with two teams in view).

Prediction doc (``predictions`` container, partition ``match_id``)::

    id "n10:<match_id>:<t_ms>", match_id, kind "next10", t_s (video seconds), match_clock_s (or null), minute,
    t_issued, model_version, possession_team_id, probs, prob_std, poss10_by_team, ball_zone10, receivers,
    receiver_lost, action_next, trajectories, embedding (64 floats), features {...}

``t_s`` is video time, the same axis as the frames; ``match_clock_s`` is the broadcast match clock (null until
the clock map exists, and in the half-time break).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
from collections import deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from matchlens.next10.hub import Hub
from matchlens.next10.hub import hub as default_hub
from matchlens.store.next10 import Next10Settings, Next10Store

log = logging.getLogger("matchlens.next10.predict")

PITCH_L, PITCH_W = 105.0, 68.0
HISTORY_S = 4  # earlier frames, one second apart
MIN_PLAYERS = 6
MIN_PER_TEAM = 5  # the model was trained on two teams in view; one team (a failed kit split) is out of its range
MAX_PER_TEAM = 11
CARRIER_M = 3.0
BALL_HOLD_S = 3.0
BALL_MARGIN_M = 1.0  # a "ball" further outside the lines than this is not the ball (frames stored before the chain dropped it)
GK_DEPTH_M = 16.5
BATCH = 16
POINT_DP, PROB_DP = 2, 4

Frames = Sequence[dict[str, Any]]


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _abs(x: float, y: float) -> tuple[float, float]:
    return x + PITCH_L / 2, y + PITCH_W / 2


def _ball_entity(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The detected ball of a frame, unless it lies off the pitch: then there is no ball (the caller falls back to
    the last good one, or to the players)."""
    e = next((e for e in doc["entities"] if e["team"] == "ball"), None)
    if e is None or abs(e["x"]) > PITCH_L / 2 + BALL_MARGIN_M or abs(e["y"]) > PITCH_W / 2 + BALL_MARGIN_M:
        return None
    return e


class Possession:
    """Who has the ball: the team of the nearest player, kept through a short blind spell. The team only changes
    when the other one has been nearest for ``CONFIRM`` updates in a row (once a second): a duel at the touchline
    otherwise flips the possession, and the whole prediction with it, from one second to the next."""

    CONFIRM = 2

    def __init__(self) -> None:
        self.team = "home"
        self.started = False
        self._rival: str | None = None
        self._votes = 0
        self.ball: tuple[float, float] | None = None
        self.seen_s = -math.inf

    def _vote(self, nearest: str) -> None:
        if not self.started:
            self.team, self.started = nearest, True
        elif nearest == self.team:
            self._rival, self._votes = None, 0
        else:
            self._votes = self._votes + 1 if nearest == self._rival else 1
            self._rival = nearest
            if self._votes >= self.CONFIRM:
                self.team, self._rival, self._votes = nearest, None, 0

    def ball_for(self, doc: dict[str, Any], players: list[dict[str, Any]]) -> tuple[tuple[float, float], bool]:
        """(x, y) in centred metres and whether it is a detection."""
        t = float(doc["t_s"])
        seen = _ball_entity(doc)
        if seen is not None:
            self.ball, self.seen_s = (seen["x"], seen["y"]), t
            return self.ball, True
        if self.ball is not None and t - self.seen_s <= BALL_HOLD_S:
            return self.ball, False
        xs = [p["x"] for p in players] or [0.0]
        ys = [p["y"] for p in players] or [0.0]
        return (sum(xs) / len(xs), sum(ys) / len(ys)), False

    def update(self, doc: dict[str, Any], players: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None,
                                                                                     tuple[float, float], bool]:
        ball, seen = self.ball_for(doc, players)
        if not players:
            return self.team, None, ball, seen
        near = min(players, key=lambda p: math.hypot(p["x"] - ball[0], p["y"] - ball[1]))
        d = math.hypot(near["x"] - ball[0], near["y"] - ball[1])
        if seen or float(doc["t_s"]) - self.seen_s <= BALL_HOLD_S:
            self._vote(near["team"])
        return self.team, (near if d <= CARRIER_M and seen else None), ball, seen


def _players(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The players of a frame doc, at most 11 a team (most confident first), home and away only."""
    keep: list[dict[str, Any]] = []
    for side in ("home", "away"):
        team = sorted((e for e in doc["entities"] if e["team"] == side), key=lambda e: -(e.get("conf") or 0.0))
        keep += team[:MAX_PER_TEAM]
    return keep


def _goalkeepers(players: list[dict[str, Any]]) -> set[str]:
    """Home defends x < 0 (it attacks +x), away x > 0: the deepest player inside the box is the keeper."""
    out: set[str] = set()
    home = [p for p in players if p["team"] == "home"]
    away = [p for p in players if p["team"] == "away"]
    if home and (g := min(home, key=lambda p: p["x"]))["x"] + PITCH_L / 2 <= GK_DEPTH_M:
        out.add(g["id"])
    if away and (g := max(away, key=lambda p: p["x"]))["x"] + PITCH_L / 2 >= PITCH_L - GK_DEPTH_M:
        out.add(g["id"])
    return out


def tframe(docs: Frames, possession: Possession) -> tuple[Any, dict[str, Any]] | None:
    """A ``TFrame`` from frame docs, oldest first and the last one the moment to predict (earlier ones are history,
    about a second apart). None when the last frame cannot be used. Also returns what was assumed."""
    from matchlens.tacticsml.contract import TBall, TFrame, TPlayer

    cur = docs[-1]
    if not cur.get("calibrated"):
        return None
    now = _players(cur)
    if len(now) < MIN_PLAYERS or min(sum(p["team"] == side for p in now) for side in ("home", "away")) < MIN_PER_TEAM:
        return None
    team, carrier, ball, ball_seen = possession.update(cur, now)
    gks = _goalkeepers(now)
    t0 = float(cur["t_s"])

    def build(doc: dict[str, Any], players: list[dict[str, Any]], b: tuple[float, float], history: list[Any]) -> Any:
        ax, ay = _abs(*b)
        return TFrame(
            players=[TPlayer(player_id=p["id"], team_id=p["team"], x=_abs(p["x"], p["y"])[0],
                             y=_abs(p["x"], p["y"])[1], is_gk=p["id"] in gks) for p in players],
            ball=TBall(x=ax, y=ay), carrier_id=carrier["id"] if carrier is not None and doc is cur else None,
            possession_team_id=team, attacking_right={"home": True, "away": False},
            elapsed=float(doc["t_s"]) - t0 + 10_000.0, history=history)

    history: list[Any] = []
    for doc in docs[:-1]:
        if not doc.get("calibrated"):
            continue
        players = _players(doc)
        if len(players) < MIN_PLAYERS:
            continue
        seen_h = _ball_entity(doc)
        ball_h = (seen_h["x"], seen_h["y"]) if seen_h is not None else ball
        history.append(build(doc, players, ball_h, []))
    frame = build(cur, now, ball, history[-HISTORY_S:])
    info = {"n_players": len(now), "ball_observed": ball_seen, "history": len(history[-HISTORY_S:]),
            "calib_status": cur.get("calib_status"), "carrier": None if carrier is None else carrier["id"]}
    return frame, info


def _r(values: Any, dp: int) -> Any:
    if isinstance(values, float):
        return round(values, dp)
    if isinstance(values, dict):
        return {k: _r(v, dp) for k, v in values.items()}
    if isinstance(values, (list, tuple)):
        return [_r(v, dp) for v in values]
    return values


def _both_teams(by_team: dict[str, float], possession: str) -> dict[str, float]:
    """Possession over the next 10 s for home and away (the model only names a team it can see in the frame)."""
    other = "away" if possession == "home" else "home"
    return {possession: by_team[possession], other: by_team.get(other, 1.0 - by_team[possession])}


def prediction_doc(match_id: str, frame_doc: dict[str, Any], out: dict[str, Any], info: dict[str, Any],
                   *, t_issued: str | None = None) -> dict[str, Any]:
    """The stored form of one ``infer_next10`` answer."""
    t_s = float(frame_doc["t_s"])
    clock = frame_doc.get("match_clock_s")
    zone = out["ball_zone10"]
    traj = out.get("trajectories")
    doc: dict[str, Any] = {
        "id": f"n10:{match_id}:{round(t_s * 1000)}", "match_id": match_id, "kind": "next10",
        "t_s": round(t_s, 2), "match_clock_s": clock,
        "minute": int((clock if clock is not None else t_s) // 60),
        "t_issued": t_issued or now_utc(), "model_version": out["model_version"],
        "possession_team_id": out["possession_team_id"],
        "probs": _r(out["probs"], PROB_DP), "prob_std": _r(out["prob_std"], PROB_DP),
        "poss10_by_team": _r(_both_teams(out["poss10_by_team"], out["possession_team_id"]), PROB_DP),
        "ball_zone10": {"cols": zone["cols"], "rows": zone["rows"], "x0": zone["x0"], "y0": zone["y0"],
                        "cell_w": zone["cell_w"], "cell_h": zone["cell_h"], "p": _r(zone["p"], 5),
                        "expected_xy": _r(zone["expected_xy"], POINT_DP)},
        "receivers": [{"player_id": r["player_id"], "p": round(r["p"], PROB_DP), "std": round(r["std"], PROB_DP)}
                      for r in out["receivers"]],
        "receiver_lost": round(out["receiver_lost"], PROB_DP), "action_next": _r(out["action_next"], PROB_DP),
        "features": {**info, "team_known": out.get("team_known"), "flipped": bool(frame_doc.get("flipped")),
                     "shot10_parts": _r(out["shot10_parts"], PROB_DP)},
    }
    if traj is not None:
        doc["trajectories"] = {"dt_s": traj["dt_s"], "horizon_s": traj["horizon_s"], "entities": [
            {"entity_id": e["entity_id"], "modes": [{"p": round(m["p"], PROB_DP), "path": _r(m["path"], POINT_DP)}
                                                      for m in e["modes"]]} for e in traj["entities"]]}
    if "embedding" in out:
        doc["embedding"] = _r(out["embedding"], 4)
    return doc


def _model():
    from matchlens.tacticsml.infer_next10 import _default_model

    return _default_model()


class Predictor:
    """Frame docs in time order -> prediction docs at 1 Hz. Holds the last seconds of frames and the possession."""

    def __init__(self, match_id: str, *, home: str | None = None, away: str | None = None, model: Any = None,
                 fps: float = 5.0, every_s: float = 1.0, trajectories: bool = True) -> None:
        self.match_id, self.home, self.away = match_id, home, away
        self.model = model or _model()
        if self.model is None:
            raise RuntimeError("no Next10 weights installed (MATCHLENS_NEXT10_WEIGHTS)")
        self.fps, self.every_s, self.trajectories = fps, every_s, trajectories
        self.possession = Possession()
        self.skipped = 0
        self._recent: deque[dict[str, Any]] = deque(maxlen=int((HISTORY_S + 1) * fps) + 2)
        self._next_s = 0.0

    def _names(self, team: str) -> tuple[str | None, str | None]:
        mine, theirs = (self.home, self.away) if team == "home" else (self.away, self.home)
        return mine, theirs

    def _window(self, t_s: float) -> list[dict[str, Any]]:
        """Frames at t_s and about 1, 2, 3, 4 s before it (oldest first)."""
        by_t = {round(d["t_s"] * self.fps): d for d in self._recent}
        out = []
        for k in range(HISTORY_S, -1, -1):
            d = by_t.get(round((t_s - k) * self.fps))
            if d is not None:
                out.append(d)
        return out

    def feed(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        """Add a frame doc; returns the predictions that became due (zero or one, for a 1 Hz grid)."""
        self._recent.append(doc)
        t = float(doc["t_s"])
        if t + 1e-6 < self._next_s:
            return []
        self._next_s = (math.floor(t / self.every_s + 1e-6) + 1) * self.every_s
        return self._predict([self._window(t)])

    def run(self, docs: Frames) -> list[dict[str, Any]]:
        """Predictions for a whole stored match (frames in time order)."""
        out: list[dict[str, Any]] = []
        windows = []
        for doc in docs:
            self._recent.append(doc)
            t = float(doc["t_s"])
            if t + 1e-6 < self._next_s:
                continue
            self._next_s = (math.floor(t / self.every_s + 1e-6) + 1) * self.every_s
            windows.append(self._window(t))
            if len(windows) >= BATCH:
                out += self._predict(windows)
                windows = []
        return out + (self._predict(windows) if windows else [])

    def _predict(self, windows: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        built, docs = [], []
        for w in windows:
            got = tframe(w, self.possession) if w else None
            if got is None:
                self.skipped += 1
                continue
            built.append(got)
            docs.append(w[-1])
        if not built:
            return []
        frames = [b[0] for b in built]
        names = [self._names(f.possession_team_id) for f in frames]
        outs = self.model.predict_batch(frames, names if self.home or self.away else None,
                                        [None] * len(frames), 3, self.trajectories, True)
        stamp = now_utc()
        return [prediction_doc(self.match_id, d, o, info, t_issued=stamp)
                for d, o, (_, info) in zip(docs, outs, built, strict=True)]


class LivePredictor:
    """For ``video:live``: feed each frame doc, publish and store what is due. Inference runs off the event loop."""

    def __init__(self, predictor: Predictor, sink: Any) -> None:
        self.predictor, self.sink = predictor, sink
        self.made = 0

    async def feed(self, doc: dict[str, Any]) -> None:
        for out in await asyncio.to_thread(self.predictor.feed, doc):
            self.sink.add(out, "next10")
            self.made += 1


# ---------------------------------------------------------------------------------------------- stored matches

async def frames_of(store: Next10Store, match_id: str, page_s: float = 600.0) -> list[dict[str, Any]]:
    top = await store.query("frames", "SELECT VALUE MAX(c.t_s) FROM c WHERE c.match_id = @m",
                            [{"name": "@m", "value": match_id}], partition=match_id)
    end = float(top[0]) if top and top[0] is not None else -1.0
    rows: list[dict[str, Any]] = []
    t = 0.0
    while t <= end:
        rows += await store.query(
            "frames", "SELECT * FROM c WHERE c.match_id = @m AND c.t_s >= @a AND c.t_s < @b ORDER BY c.t_s",
            [{"name": "@m", "value": match_id}, {"name": "@a", "value": t}, {"name": "@b", "value": t + page_s}],
            partition=match_id)
        t += page_s
    return rows


async def predict_match(store: Next10Store, match_id: str, *, model: Any = None, dry_run: bool = False,
                        hub: Hub | None = None, trajectories: bool = True) -> dict[str, Any]:
    """Predictions at 1 Hz over a stored match's frames. Returns counts and the shot10 summary."""
    match = await store.read("matches", match_id, match_id) or {}
    name = lambda side: (match.get(side) or {}).get("name")  # noqa: E731
    frames = await frames_of(store, match_id)
    predictor = Predictor(match_id, home=name("home"), away=name("away"), model=model, trajectories=trajectories)
    docs = await asyncio.to_thread(predictor.run, frames)
    if docs and not dry_run:
        await store.upsert("predictions", docs)
    if hub is not None:
        for d in docs:
            hub.publish(match_id, "next10", d)
    shot = [d["probs"]["shot10"] for d in docs]
    return {"match_id": match_id, "frames": len(frames), "predictions": len(docs), "skipped": predictor.skipped,
            "model_version": docs[0]["model_version"] if docs else None, "written": 0 if dry_run else len(docs),
            "shot10_mean": round(sum(shot) / len(shot), 4) if shot else None,
            "shot10_max": round(max(shot), 4) if shot else None}


async def _main(args: argparse.Namespace) -> None:
    async with await Next10Store(Next10Settings.from_env()).open() as store:
        print(json.dumps(await predict_match(store, args.match_id, dry_run=args.dry_run)))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("match_id")
    p.add_argument("--dry-run", action="store_true", help="compute but do not write")
    logging.basicConfig(level=logging.INFO)
    for noisy in ("azure.core.pipeline.policies.http_logging_policy", "azure.cosmos", "azure.identity"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()

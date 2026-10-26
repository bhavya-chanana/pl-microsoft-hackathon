"""Fit the expected-threat (xT) grid from StatsBomb open event data.

Karun Singh's method: on a 16 x 12 grid, ``xT(z) = s(z) * g(z) + m(z) * sum_z' T(z -> z') xT(z')`` where
``s`` is the share of shots among a zone's actions, ``g`` the goal rate of those shots, ``m = 1 - s`` and
``T`` the transition matrix of successful passes and carries (failed moves leak value: a turnover is worth
nothing to the attacker). Value iteration runs to convergence. The grid is symmetrised about the pitch
midline since open data has no left/right preference worth keeping.

Run (downloads about 3 MB per match once, only a compact extract is kept in the local cache):
    uv run python -m matchlens.tacticsml.train.xt_fit --matches 300
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

SOURCE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
# (competition_id, season_id, label): men's top flights, 2015/16
COMPETITIONS = [(2, 27, "Premier League 2015/16"), (11, 27, "La Liga 2015/16")]
COLS, ROWS = 16, 12
SB_X, SB_Y = 120.0, 80.0
OUT_PATH = Path(__file__).resolve().parent.parent / "xt_grid.json"
CACHE = Path(__file__).resolve().parents[4] / "data" / "local" / "tacticsml" / "raw" / "statsbomb"
SET_PIECE_PASSES = {"Corner", "Free Kick", "Throw-in", "Kick Off", "Goal Kick"}


def _get(url: str) -> Any:
    last: Exception | None = None
    for _ in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - retry transient network errors
            last = exc
    raise RuntimeError(f"cannot fetch {url}") from last


def _cell(x: float, y: float) -> tuple[int, int]:
    return min(int(x / SB_X * COLS), COLS - 1), min(int(y / SB_Y * ROWS), ROWS - 1)


def extract(events: list[dict[str, Any]]) -> dict[str, list[list[float]]]:
    """Compact per-match actions: moves [x0, y0, x1, y1, completed] and shots [x, y, goal] (StatsBomb yards)."""
    moves: list[list[float]] = []
    shots: list[list[float]] = []
    for e in events:
        loc = e.get("location")
        kind = (e.get("type") or {}).get("name")
        if not loc or e.get("period") == 5:
            continue
        if kind == "Pass":
            p = e.get("pass", {})
            if (p.get("type") or {}).get("name") in SET_PIECE_PASSES or "end_location" not in p:
                continue
            moves.append([loc[0], loc[1], p["end_location"][0], p["end_location"][1],
                          0.0 if "outcome" in p else 1.0])
        elif kind == "Carry":
            end = e.get("carry", {}).get("end_location")
            if end:
                moves.append([loc[0], loc[1], end[0], end[1], 1.0])
        elif kind == "Shot":
            s = e.get("shot", {})
            if (s.get("type") or {}).get("name") in ("Penalty",):
                continue
            shots.append([loc[0], loc[1], 1.0 if (s.get("outcome") or {}).get("name") == "Goal" else 0.0])
    return {"moves": moves, "shots": shots}


def _load_match(match_id: int, cache: Path) -> dict[str, list[list[float]]]:
    path = cache / f"{match_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    data = extract(_get(f"{SOURCE}/events/{match_id}.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")))
    return data


def fit_grid(matches: list[dict[str, list[list[float]]]], tol: float = 1e-9, max_iter: int = 500
             ) -> tuple[np.ndarray, dict[str, Any]]:
    n = ROWS * COLS
    move_n = np.zeros(n)
    trans = np.zeros((n, n))
    shot_n = np.zeros(n)
    goal_n = np.zeros(n)

    def idx(x: float, y: float) -> int:
        c, r = _cell(x, y)
        return r * COLS + c

    for m in matches:
        for x0, y0, x1, y1, ok in m["moves"]:
            a = idx(x0, y0)
            move_n[a] += 1
            if ok:
                trans[a, idx(x1, y1)] += 1
        for x, y, goal in m["shots"]:
            a = idx(x, y)
            shot_n[a] += 1
            goal_n[a] += goal
    total = move_n + shot_n
    safe = np.maximum(total, 1)
    s = shot_n / safe
    g = goal_n / np.maximum(shot_n, 1)
    t = trans / np.maximum(move_n, 1)[:, None]
    xt = np.zeros(n)
    iterations = 0
    for iterations in range(1, max_iter + 1):
        new = s * g + (1 - s) * (t @ xt)
        delta = float(np.abs(new - xt).max())
        xt = new
        if delta < tol:
            break
    grid = xt.reshape(ROWS, COLS)
    grid = (grid + grid[::-1]) / 2  # symmetrise about the midline
    info = {"iterations": iterations, "final_delta": delta, "moves": int(move_n.sum()),
            "shots": int(shot_n.sum()), "goals": int(goal_n.sum())}
    return grid, info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matches", type=int, default=300, help="matches per competition")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--competitions", type=int, default=len(COMPETITIONS),
                    help="how many of the listed competitions to use, in order (1 = Premier League only)")
    args = ap.parse_args()
    sampled: list[tuple[str, int]] = []
    for comp, season, label in COMPETITIONS[: args.competitions]:
        ids = sorted(m["match_id"] for m in _get(f"{SOURCE}/matches/{comp}/{season}.json"))
        step = max(1, len(ids) // args.matches)
        sampled += [(label, i) for i in ids[::step][: args.matches]]
    with ThreadPoolExecutor(args.workers) as pool:
        data = list(pool.map(lambda lm: _load_match(lm[1], CACHE), sampled))
    grid, info = fit_grid(data)
    labels = sorted({lab for lab, _ in sampled})
    out = {
        "cols": COLS, "rows": ROWS,
        "note": "rows are y (touchline to touchline), columns are x towards the attacked goal",
        "values": np.round(grid, 5).tolist(),
        "provenance": {
            "method": "Karun Singh xT: value iteration over shot/goal rates and successful move transitions",
            "source": "StatsBomb Open Data (https://github.com/statsbomb/open-data), event data",
            "competitions": labels,
            "matches_used": len(sampled),
            **info,
        },
    }
    rows = ",\n  ".join(json.dumps(r) for r in out.pop("values"))
    text = json.dumps(out, indent=1)
    OUT_PATH.write_text(text.replace('"provenance"', f'"values": [\n  {rows}\n ],\n "provenance"') + "\n")
    print(f"wrote {OUT_PATH}: {len(sampled)} matches, {info}")


if __name__ == "__main__":
    main()

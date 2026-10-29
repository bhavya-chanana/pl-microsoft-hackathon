"""Key moments of a match, for Play it out: goals conceded and chances, written to the ``moments`` container.

    uv run python -m matchlens.next10.moments <match_id> [--threshold 0.25]

Two sources, chosen by the match document:

* FotMob matches (``source`` "fotmob" or "shadow"): the shot map in the score doc (``score:<match_id>``,
  ``outcomes``). Every goal is a ``goal_conceded`` moment for the side that let it in, and every shot with xG at or
  above ``XG_MIN`` (0.15) is a ``chance`` for the side that had it (a goal is only its ``goal_conceded``). ``source`` is "fotmob", ``minute`` is FotMob's
  1-based minute, ``t_s`` and ``match_clock_s`` the start of it in seconds (penalty-shootout kicks are left out), and
  there is no embedding (no tracking for these matches).
* Video-only matches (``source`` "video"): no shot map, so the moments are local maxima of the model's own ``shot10``
  (the chance, for the team in possession, that a shot comes within 10 s), as ``chance`` with ``source`` "model"
  and a label that says so. ``t_s`` is video time, ``match_clock_s`` the broadcast clock when it is known, and the
  embedding is Next10's critic embedding of that instant (64 floats, ``model_version`` stored beside it).

The threshold ``SHOT10_PEAK`` = 0.25. The shot10 head is calibrated (expected calibration error 0.003 on held-out
matches, base rate about 0.04 a second, AUC about 0.87), so a value of 0.25 means about one in four such instants
is followed by a shot within 10 s: six times the base rate, and rare enough to be a moment rather than
a texture (the label reports the value). A peak is the highest value within ``PEAK_WINDOW_S`` either side and
peaks are at least ``MIN_GAP_S`` = 30 s apart (a shot horizon is 10 s and an attack builds for about as long
before it, so closer peaks are the same move seen twice). On the first 10 minutes of the Liverpool v Brighton clip
(1-0 up, one team pressing), the model's shot10 runs hot on video input (mean 0.10, 2.4 times the training base rate,
12% of seconds above 0.25): at 15 s apart that gave 9 moments, at 30 s about 6. The retrain on video-derived
inputs should bring it down; until then these are "the model thinks a shot was coming", which the label says.

``minute`` is 1-based like FotMob's (minute m is match clock (m-1):00 to m:00), model peaks and FotMob moments alike.

A video match whose document has a ``fotmob_id`` (and a ``--clockmap``) gets the real goals and chances too
(``video_fotmob_moments``): ``goal_conceded`` for every goal and ``chance`` for shots with xG >= 0.15 inside the clip's
clock window, ``source`` "fotmob", placed through the clock map (``placed``, ``t_s_error_s`` say how exactly).

Ids are deterministic, so running again overwrites rather than duplicates. Nothing is deleted: if a changed
threshold or new predictions drop a moment, the old document stays until someone removes it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
from pathlib import Path
from typing import Any

from matchlens.store.next10 import Next10Settings, Next10Store

log = logging.getLogger("matchlens.next10.moments")

XG_MIN = 0.15
SHOT10_PEAK = 0.25
PEAK_WINDOW_S = 5.0
MIN_GAP_S = 30.0
EMBED_DIM = 64


def _other(side: str) -> str:
    return "away" if side == "home" else "home"


def _name(match: dict[str, Any], side: str) -> str | None:
    return (match.get(side) or {}).get("name") if isinstance(match.get(side), dict) else match.get(side)


def shootout_start(details: dict[str, Any] | None) -> int | None:
    """The score doc's (0-based) minute of the first penalty-shootout kick in a FotMob match, if it had one."""
    shots = ((details or {}).get("content") or {}).get("shotmap", {}).get("shots") or []
    mins = [int(s["min"]) - 1 for s in shots if s.get("period") == "PenaltyShootout" and s.get("min") is not None]
    return min(mins) if mins else None


def fotmob_moments(match_id: str, score: dict[str, Any], match: dict[str, Any], *,
                   shootout_from: int | None = None) -> list[dict[str, Any]]:
    """Goals conceded and shots with xG >= ``XG_MIN`` from a score doc's ``outcomes``.

    The score doc counts minutes from 0 (``t_s`` is the start of that minute); FotMob and the page show 1-based ones,
    so ``minute`` here is the score doc's plus one (a goal in FotMob's 30th minute is minute 30, ``t_s`` 1740). Kicks of
    a penalty shootout (``shootout_from`` and later) are not moments of the match: they are neither goals conceded
    nor chances."""
    out: list[dict[str, Any]] = []
    for i, o in enumerate(sorted(score.get("outcomes", []), key=lambda o: (o.get("t_s") or o["minute"] * 60))):
        if shootout_from is not None and int(o["minute"]) >= shootout_from:
            continue
        side, minute = o["team_side"], int(o["minute"]) + 1
        t_s = float(o["t_s"]) if o.get("t_s") is not None else (minute - 1) * 60.0
        xg = o.get("xg")
        base = {"match_id": match_id, "minute": minute, "t_s": t_s, "match_clock_s": t_s, "source": "fotmob"}
        if o.get("type") == "goal":  # one moment per goal: the goal conceded (scorer and xG in it), not also a chance
            conceded = _other(side)
            out.append({**base, "id": f"mom:{match_id}:goal:{i}", "kind": "goal_conceded", "team_side": conceded,
                        "team": _name(match, conceded), "xg": xg, "scored_by": side,
                        "label": f"Goal conceded, {minute}'" + (f" (xG {xg:.2f})" if xg is not None else "")})
        elif xg is not None and xg >= XG_MIN:
            out.append({**base, "id": f"mom:{match_id}:chance:{i}", "kind": "chance", "team_side": side,
                        "team": _name(match, side), "xg": xg,
                        "label": f"{'Goal' if o.get('type') == 'goal' else 'Chance'}, {minute}' (xG {xg:.2f})"})
    return out


def _side_of(team_id: Any, details: dict[str, Any]) -> str | None:
    general = details.get("general") or {}
    if team_id == (general.get("homeTeam") or {}).get("id"):
        return "home"
    return "away" if team_id == (general.get("awayTeam") or {}).get("id") else None


def video_fotmob_moments(match_id: str, details: dict[str, Any], match: dict[str, Any], clock_map: Any, *,
                         pins: dict[int, float] | None = None, window: tuple[float, float] | None = None,
                         predictions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The recorded goals and chances (FotMob) that fall inside a clip, placed on the video through its clock map.

    ``details`` is FotMob's cached ``matchDetails``. A goal conceded is every goal event (an own goal counts for the
    side that benefits; the team that let it in is the other one). A chance is a shot with xG >= ``XG_MIN`` (a goal
    with that xG is both). FotMob gives a minute, not a second: minute ``m`` is the broadcast clock from ``(m-1):00`` to
    ``m:00``, so a moment is put in the middle of it (``t_s_error_s`` 30) unless ``pins`` has the video second
    someone read off the footage for that minute (``t_s_error_s`` 1, ``placed`` "observed"). ``window`` is the stretch
    of match clock the clip covers: a minute that does not overlap it is left out. ``t_s`` is video seconds."""
    pins = pins or {}
    lo, hi = window if window else (-math.inf, math.inf)
    content = details.get("content") or {}
    items: list[dict[str, Any]] = []
    for e in ((content.get("matchFacts") or {}).get("events") or {}).get("events") or []:
        if e.get("type") != "Goal" or e.get("isPenaltyShootoutEvent") or e.get("time") is None:
            continue
        shot = e.get("shotmapEvent") or {}
        items.append({"kind": "goal", "minute": int(e["time"]), "scorer_side": "home" if e.get("isHome") else "away",
                      "xg": shot.get("expectedGoals"), "own_goal": bool(e.get("ownGoal")), "player": e.get("nameStr"),
                      "ref": e.get("eventId") or len(items)})
    for sh in (content.get("shotmap") or {}).get("shots") or []:
        side = _side_of(sh.get("teamId"), details)
        xg = sh.get("expectedGoals")
        if side is None or sh.get("eventType") == "Goal" or sh.get("isOwnGoal") or xg is None or xg < XG_MIN:
            continue
        items.append({"kind": "shot", "minute": int(sh.get("min") or 0), "scorer_side": side, "xg": xg, "own_goal": False,
                      "player": sh.get("fullName") or sh.get("playerName"), "ref": sh.get("id")})
    out: list[dict[str, Any]] = []
    preds = sorted((p for p in predictions or [] if p.get("kind") == "next10"), key=lambda p: p["t_s"])
    for it in items:
        m = it["minute"]
        start_s, end_s = (m - 1) * 60.0, m * 60.0
        if end_s < lo or start_s > hi:
            continue
        pinned = pins.get(m)
        if pinned is not None:
            t_s, err, placed = float(pinned), 1.0, "observed"
        else:
            t_s, err, placed = float(clock_map.to_video(start_s + 30.0)), 30.0, "clock_map"
        if not math.isfinite(t_s):
            continue
        clock = float(clock_map.to_match(t_s))
        side = it["scorer_side"]
        base = {"match_id": match_id, "minute": m, "t_s": round(t_s, 2),
                "match_clock_s": round(clock, 2) if math.isfinite(clock) else None, "source": "fotmob",
                "placed": placed, "t_s_error_s": err, "xg": it["xg"], "player": it["player"]}
        near = min(preds, key=lambda p: abs(p["t_s"] - t_s), default=None)
        emb = near.get("embedding") if near is not None and abs(near["t_s"] - t_s) <= 2.0 else None
        if isinstance(emb, list) and len(emb) == EMBED_DIM and all(math.isfinite(x) for x in emb):
            base["embedding"], base["model_version"] = emb, near.get("model_version")
        xg = it["xg"]
        tag = f" (xG {xg:.2f})" if xg else ""
        if it["kind"] == "goal":
            conceded = _other(side)
            out.append({**base, "id": f"mom:{match_id}:fm:goal:{it['ref']}", "kind": "goal_conceded",
                        "team_side": conceded, "team": _name(match, conceded), "scored_by": side,
                        "own_goal": it["own_goal"],
                        "label": f"Goal conceded, {m}'" + (" (own goal)" if it["own_goal"] else
                                                          f" ({it['player']}, xG {xg:.2f})" if xg and it["player"] else tag)})
        elif xg is not None and xg >= XG_MIN and not it["own_goal"]:
            out.append({**base, "id": f"mom:{match_id}:fm:chance:{it['ref']}", "kind": "chance", "team_side": side,
                        "team": _name(match, side),
                        "label": f"{'Goal' if it['kind'] == 'goal' else 'Chance'}, {m}'{tag}"})
    return sorted(out, key=lambda d: (d["t_s"], d["id"]))


OUTCOME_TEXT = {"Goal": "goal", "AttemptSaved": "saved", "Miss": "off target", "Post": "hit the post", "Blocked": "blocked"}


def refine_model_moments(model: list[dict[str, Any]], details: dict[str, Any], goals: list[dict[str, Any]]
                         ) -> list[dict[str, Any]]:
    """Model peaks of a video match whose real shots are known (FotMob): the peak next to a goal moment is dropped (the
    goal covers it); a peak in a FotMob minute that has a shot says which ("a shot followed in this minute: ..."), and
    "no recorded shot" stays only on peaks in minutes with none. Shoot-out kicks are not shots."""
    shots_by_minute: dict[int, list[str]] = {}
    for sh in ((details.get("content") or {}).get("shotmap") or {}).get("shots") or []:
        if sh.get("period") == "PenaltyShootout" or sh.get("min") is None:
            continue
        who = sh.get("fullName") or sh.get("playerName") or "a player"
        what = "own goal" if sh.get("isOwnGoal") else OUTCOME_TEXT.get(sh.get("eventType"), str(sh.get("eventType")).lower())
        shots_by_minute.setdefault(int(sh["min"]), []).append(f"{who}, {what}")
    out = []
    for d in model:
        near = [g for g in goals if g["kind"] == "goal_conceded"
                and abs(g["t_s"] - d["t_s"]) <= max(10.0, g.get("t_s_error_s") or 0.0)]
        if near:
            continue
        clock = d.get("match_clock_s")
        m = int(clock // 60) + 1 if clock is not None else None
        shots = shots_by_minute.get(m, []) if m is not None else []
        v = d.get("p_shot10")
        if shots:
            d = {**d, "fotmob_minute": m, "label": f"Model shot-chance peak: {v:.0%} chance of a shot within 10 s; "
                                                    f"a shot followed in this minute: {'; '.join(shots[:3])}"}
        out.append(d)
    return out


def peaks(series: list[tuple[float, float]], *, threshold: float = SHOT10_PEAK, window_s: float = PEAK_WINDOW_S,
          min_gap_s: float = MIN_GAP_S) -> list[int]:
    """Indices of the local maxima of (t, value) at or above ``threshold``, at least ``min_gap_s`` apart (the
    higher one wins when two are closer)."""
    cand = []
    for i, (t, v) in enumerate(series):
        if v < threshold:
            continue
        near = [u for s, u in series if abs(s - t) <= window_s]
        if v >= max(near):
            cand.append(i)
    cand.sort(key=lambda i: -series[i][1])
    kept: list[int] = []
    for i in cand:
        if all(abs(series[i][0] - series[j][0]) >= min_gap_s for j in kept):
            kept.append(i)
    return sorted(kept)


def model_moments(match_id: str, predictions: list[dict[str, Any]], match: dict[str, Any], *,
                  threshold: float = SHOT10_PEAK) -> list[dict[str, Any]]:
    """Chances found by the model: peaks of ``shot10`` over a match's ``next10`` predictions."""
    rows = sorted((p for p in predictions if p.get("kind") == "next10" and "shot10" in p.get("probs", {})),
                  key=lambda p: p["t_s"])
    series = [(float(p["t_s"]), float(p["probs"]["shot10"])) for p in rows]
    out = []
    for i in peaks(series, threshold=threshold):
        p = rows[i]
        side = p.get("possession_team_id") or "home"
        v = series[i][1]
        clock = p.get("match_clock_s")
        doc = {"id": f"mom:{match_id}:model:{round(series[i][0] * 1000)}", "match_id": match_id, "kind": "chance",
               "team_side": side, "team": _name(match, side), "minute": int((clock if clock is not None else p["t_s"]) // 60) + 1,
               "t_s": round(series[i][0], 2), "match_clock_s": clock, "xg": None, "source": "model",
               "p_shot10": round(v, 4), "model_version": p.get("model_version"),
               "label": f"Model shot-chance peak: {v:.0%} chance of a shot within 10 s (no recorded shot)"}
        emb = p.get("embedding")
        if isinstance(emb, list) and len(emb) == EMBED_DIM and all(math.isfinite(x) for x in emb):
            doc["embedding"] = emb
        out.append(doc)
    return out


async def write_moments(store: Next10Store, match_id: str, *, threshold: float = SHOT10_PEAK, clock_map: Any = None,
                        pins: dict[int, float] | None = None) -> dict[str, Any]:
    """Build and store the moments of a match. Returns what was written."""
    match = await store.read("matches", match_id, match_id) or {}
    if match.get("source") == "video":
        preds = await store.query("predictions", "SELECT * FROM c WHERE c.match_id = @m AND c.kind = 'next10'",
                                  [{"name": "@m", "value": match_id}], partition=match_id)
        docs = model_moments(match_id, preds, match, threshold=threshold)
        origin = {"source": "model", "predictions": len(preds), "threshold": threshold}
        if match.get("fotmob_id") and clock_map is not None:  # the real goals and chances of the match, from FotMob's file
            from matchlens.local import fotmob

            details = fotmob.load_details(int(match["fotmob_id"]))
            window = (match["clock_from_s"], match["clock_to_s"]) if "clock_from_s" in match else None
            if details:
                real = video_fotmob_moments(match_id, details, match, clock_map, pins=pins, window=window, predictions=preds)
                docs = refine_model_moments(docs, details, real) + real
                origin.update(fotmob_moments=len(real), fotmob_id=match["fotmob_id"])
    else:
        score = await store.read("predictions", match_id, f"score:{match_id}")
        from matchlens.local import fotmob

        fid = str(match_id).split(":")[-1]
        details = fotmob.load_details(int(fid)) if fid.isdigit() else None
        docs = fotmob_moments(match_id, score, match, shootout_from=shootout_start(details)) if score else []
        origin = {"source": "fotmob", "score_doc": score is not None, "xg_min": XG_MIN}
    if docs:
        await store.upsert("moments", docs)
    return {"match_id": match_id, "moments": len(docs), "with_embedding": sum("embedding" in d for d in docs),
            "by_kind": {k: sum(d["kind"] == k for d in docs) for k in sorted({d["kind"] for d in docs})}, **origin}


async def _main(args: argparse.Namespace) -> None:
    clock_map = None
    if args.clockmap:
        from matchlens.next10.clock import load_clock

        clock_map = load_clock(args.clockmap)
    pins = {int(k): float(v) for k, v in (a.split("=") for a in args.at)}
    async with await Next10Store(Next10Settings.from_env()).open() as store:
        print(json.dumps(await write_moments(store, args.match_id, threshold=args.threshold, clock_map=clock_map, pins=pins)))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("match_id")
    p.add_argument("--threshold", type=float, default=SHOT10_PEAK, help="shot10 level of a model peak (video matches)")
    p.add_argument("--clockmap", type=Path, help="clockmap.json of the video job: puts a video match's FotMob goals and "
                   "chances (the match doc's fotmob_id) on the video")
    p.add_argument("--at", action="append", default=[], metavar="MINUTE=VIDEO_S",
                   help="the video second of a FotMob minute, read off the footage (repeatable), e.g. --at 17=93.4")
    logging.basicConfig(level=logging.INFO)
    for noisy in ("azure.core.pipeline.policies.http_logging_policy", "azure.cosmos", "azure.identity"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()

"""Domain gap between the training tracking sources and video-derived tracks: ``python -m matchlens.tacticsml.train.domain_gap``.

Inputs: NDJSON frame docs written by the live pipeline (``{t_s, entities:[{id, team, x, y, observed, conf}], calibrated}``,
centre-origin metres, 5 Hz) and the raw tracking on disk (SkillCorner broadcast tracking with its per-player
``is_detected`` flag and camera-footprint polygon; SportEC and Metrica as clean full-pitch references).

Measured, all at 5 Hz so that the spacing is the same everywhere:
  * how many players are seen per frame, and how often the ball is seen;
  * speed and acceleration distributions per track and the share of physically implausible values;
  * jitter: the median/p90 of ``|x_{t+1} - 2 x_t + x_{t-1}|`` (metres) of a track, which is ``a dt^2`` for a smooth
    track (0.04 m per m/s^2 at 5 Hz) and larger when the positions are noisy;
  * track length (consecutive samples) as a fragmentation measure;
  * for SkillCorner, the footprint of the camera on the pitch (polygon area, from ``image_corners_projection``).
Then ``fit_view_mask`` turns the SkillCorner measurements into ``ViewMaskConfig`` values and ``prediction_gap``
compares the Next10 outputs on video windows with those on open-play test samples. Every number in the report comes
from this run.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from matchlens.tacticsml import features as F
from matchlens.tacticsml.train import schema

DT = 0.2  # seconds between samples


# ------------------------------------------------------------------ track statistics

def _series_stats(series: Iterable[tuple[np.ndarray, np.ndarray]]) -> dict[str, Any]:
    """``series``: (t (n,), xy (n,2)) per track; only runs with spacing DT +- 0.05 s count."""
    speeds, accs, jit, runs = [], [], [], []
    for t, xy in series:
        if len(t) < 2:
            continue
        ok = np.isfinite(xy).all(1)
        gap = np.diff(t)
        step = ok[1:] & ok[:-1] & (np.abs(gap - DT) < 0.05)
        v = np.where(step[:, None], np.diff(xy, axis=0) / np.maximum(gap, 1e-6)[:, None], np.nan)
        speeds.append(np.linalg.norm(v, axis=1)[step])
        both = step[1:] & step[:-1]
        acc = np.linalg.norm(np.diff(v, axis=0), axis=1) / DT
        accs.append(acc[both])
        jit.append(np.linalg.norm(xy[2:] - 2 * xy[1:-1] + xy[:-2], axis=1)[both])
        run = 0
        for s in step:  # consecutive-sample run lengths
            if s:
                run += 1
            elif run:
                runs.append(run + 1)
                run = 0
        if run:
            runs.append(run + 1)
    cat = lambda xs: np.concatenate(xs) if xs else np.zeros(0)  # noqa: E731
    sp, ac, jt = cat(speeds), cat(accs), cat(jit)

    def pct(a: np.ndarray, q=(50, 90, 99)) -> dict[str, float | None]:
        return {f"p{k}": (float(np.percentile(a, k)) if len(a) else None) for k in q}
    return {"n_steps": int(len(sp)), "speed": {**pct(sp), "max": float(sp.max()) if len(sp) else None,
                                               "share_over_12": float((sp > 12).mean()) if len(sp) else None},
            "accel": {**pct(ac), "share_over_10": float((ac > 10).mean()) if len(ac) else None},
            "jitter_m": {**pct(jt)}, "run_length": pct(np.array(runs, float), (50, 90)) if runs else None,
            "n_tracks": len(speeds)}


# ------------------------------------------------------------------ video frames

def load_frames(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in open(path) if line.strip()]
    return sorted(rows, key=lambda r: r["t_s"])


def video_stats(rows: list[dict]) -> dict[str, Any]:
    cal = [r for r in rows if r.get("calibrated")]
    people = [[e for e in r["entities"] if e["team"] in ("home", "away", "unk")] for r in cal]
    n = np.array([len(p) for p in people])
    ball = np.array([any(e["team"] == "ball" for e in r["entities"]) for r in cal])
    tracks: dict[str, list[tuple[float, float, float]]] = {}
    for r in cal:
        for e in r["entities"]:
            if e["team"] in ("home", "away", "unk"):
                tracks.setdefault(e["id"], []).append((r["t_s"], e["x"], e["y"]))
    series = [(np.array([a[0] for a in v]), np.array([[a[1], a[2]] for a in v])) for v in tracks.values()]
    # a calibrated frame may be missing between two samples: _series_stats ignores steps that are not 0.2 s
    st = _series_stats(series)
    return {"frames": len(rows), "calibrated_frames": len(cal), "players_per_frame": _pcts(n), "ball_share": float(ball.mean()) if len(ball) else None,
            "tracks": st,
            "teams": {k: int(sum(1 for p in people for e in p if e["team"] == k)) for k in ("home", "away", "unk")},
            "referees": int(sum(1 for r in cal for e in r["entities"] if e["team"] == "ref"))}


def _pcts(a: np.ndarray) -> dict[str, float | None]:
    return {"mean": float(a.mean()) if len(a) else None, **{f"p{k}": (float(np.percentile(a, k)) if len(a) else None)
                                                           for k in (10, 50, 90)}, "min": float(a.min()) if len(a) else None,
            "max": float(a.max()) if len(a) else None}


# ------------------------------------------------------------------ SkillCorner (broadcast tracking with visibility)

def skillcorner_match(path: Path, step: int = 2) -> dict[str, Any]:
    """Per-frame detected counts, ball detection, footprint area and player series at 5 Hz (every ``step``-th frame)."""
    counts, ball_det, area, cx, cy = [], [], [], [], []
    n_in, n_det_in, n_det_out = [], [], []
    det: dict[int, list[tuple[float, float, float]]] = {}
    ext: dict[int, list[tuple[float, float, float]]] = {}
    ball_xy, foot = [], []
    with open(path) as fh:
        for i, line in enumerate(fh):
            if i % step:
                continue
            d = json.loads(line)
            if d.get("period") is None or not d.get("timestamp"):
                continue
            t = _ts(d["timestamp"]) + (0 if d["period"] == 1 else 100000.0)
            pl = d["player_data"]
            c = d["image_corners_projection"]
            xs = [c.get(k) for k in ("x_top_left", "x_bottom_left", "x_bottom_right", "x_top_right")]
            ys = [c.get(k) for k in ("y_top_left", "y_bottom_left", "y_bottom_right", "y_top_right")]
            if None not in xs and None not in ys:  # frames without a camera footprint are not broadcast frames
                counts.append(sum(1 for q in pl if q.get("is_detected")))
                b = d["ball_data"]
                ball_det.append(bool(b.get("is_detected")))
                x, y = np.array(xs, float), np.array(ys, float)
                area.append(0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))
                if b.get("x") is not None:
                    foot.append(np.r_[x - b["x"], y - b["y"], b["x"], b["y"]])
                pts = np.array([[q["x"], q["y"]] for q in pl if q.get("x") is not None], float)
                dflag = np.array([bool(q.get("is_detected")) for q in pl if q.get("x") is not None], dtype=bool)
                inside = in_quad(pts, np.stack([x, y], 1)) if len(pts) else np.zeros(0, bool)
                n_in.append(int(inside.sum()))
                n_det_in.append(int((inside & dflag).sum()))
                n_det_out.append(int((~inside & dflag).sum()))
            for p in pl:
                (det if p.get("is_detected") else ext).setdefault(p["player_id"], []).append((t, p["x"], p["y"]))
    return {"counts": np.array(counts), "ball_detected": np.array(ball_det), "area": np.array(area), "footprints": np.array(foot),
            "detected": det, "extrapolated": ext, "in_poly": np.array(n_in), "det_in_poly": np.array(n_det_in),
            "det_out_poly": np.array(n_det_out)}


def in_quad(pts: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Points ``(n, 2)`` inside the convex-ish quadrilateral ``quad (4, 2)`` (all cross products of one sign)."""
    pts = np.asarray(pts, float).reshape(-1, 2)
    a, b = quad, np.roll(quad, -1, axis=0)
    cr = (b[:, 0] - a[:, 0])[None] * (pts[:, 1:2] - a[:, 1][None]) - (b[:, 1] - a[:, 1])[None] * (pts[:, 0:1] - a[:, 0][None])
    return (cr >= 0).all(1) | (cr <= 0).all(1)


def _ts(s: str) -> float:
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def _as_series(d: dict[int, list]) -> list[tuple[np.ndarray, np.ndarray]]:
    out = []
    for v in d.values():
        a = np.array(v, float)
        out.append((a[:, 0], a[:, 1:3]))
    return out


def skillcorner_stats(match_ids: Sequence[str], root: Path | None = None, limit: int | None = None) -> dict[str, Any]:
    from matchlens.tacticsml.train.sources import skillcorner as S

    root = root or S.RAW
    counts, ball, area, foots = [], [], [], []
    n_in, n_det_in, n_det_out = [], [], []
    det_series, ext_series = [], []
    used = []
    for mid in list(match_ids)[:limit]:
        p = root / mid / f"{mid}_tracking_extrapolated.jsonl"
        if not p.exists():
            continue
        m = skillcorner_match(p)
        used.append(mid)
        counts.append(m["counts"])
        ball.append(m["ball_detected"])
        area.append(m["area"])
        foots.append(m["footprints"])
        n_in.append(m["in_poly"])
        n_det_in.append(m["det_in_poly"])
        n_det_out.append(m["det_out_poly"])
        det_series += _as_series(m["detected"])
        ext_series += _as_series(m["extrapolated"])
    counts, ball, area = np.concatenate(counts), np.concatenate(ball), np.concatenate(area)
    n_in, n_det_in, n_det_out = np.concatenate(n_in), np.concatenate(n_det_in), np.concatenate(n_det_out)
    return {"players_in_footprint": _pcts(n_in), "detected_in_footprint": _pcts(n_det_in),
            "detected_outside_footprint": _pcts(n_det_out), "p_detect_in_footprint": float(n_det_in.sum() / max(n_in.sum(), 1)),
            "_in_poly": n_in, "_det_in_poly": n_det_in, **{"matches": used, "players_detected_per_frame": _pcts(counts), "ball_detected_share": float(ball.mean()),
            "footprint_area_m2": _pcts(area), "footprint_share_of_pitch": float(np.mean(area) / (105 * 68)),
            "detected_tracks": _series_stats(det_series), "extrapolated_tracks": _series_stats(ext_series),
            "_footprints": np.concatenate(foots) if foots else np.zeros((0, 10)), "_counts": counts, "_ball": ball}}


def table_stats(table, period: int = 1, seconds: float = 900.0) -> dict[str, Any]:
    """Stats of a full-pitch research tracking source (SportEC / Metrica) from the first ``seconds`` of a period."""
    per = table.periods[period]
    sel = per.times < per.times[0] + seconds
    t, xy = per.times[sel], per.xy[sel]
    return _series_stats([(t, xy[:, j]) for j in range(xy.shape[1])])


# ------------------------------------------------------------------ view-mask fit

def fit_view_mask(sc: dict[str, Any]) -> dict[str, Any]:
    """What the augmentation should reproduce: SkillCorner's seen-player count distribution, ball detection and the
    footprint area (m^2) and its extent relative to the ball."""
    counts = sc["_counts"]
    foot = sc["_footprints"]
    out: dict[str, Any] = {"seen_players_quantiles": {f"p{q}": float(np.percentile(counts, q)) for q in (5, 25, 50, 75, 95)},
                           "ball_detected": sc["ball_detected_share"]}
    if len(foot):
        rel = foot[:, :8].reshape(-1, 2, 4)  # x offsets, y offsets of 4 corners from the ball
        out["footprint_offsets_m"] = {"width_p50": float(np.percentile(rel[:, 0].max(1) - rel[:, 0].min(1), 50)),
                                      "height_p50": float(np.percentile(rel[:, 1].max(1) - rel[:, 1].min(1), 50)),
                                      "ball_inside_share": float(np.mean([_inside(r) for r in rel[:2000]]))}
    return out


def _inside(rel: np.ndarray) -> bool:
    """Is the ball (origin) inside the quadrilateral whose corners are ``rel`` (2, 4) (x row, y row)?"""
    pts = rel.T
    s = 0
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        s += np.sign(a[0] * b[1] - a[1] * b[0])
    return abs(s) == 4


# ------------------------------------------------------------------ footprint bank and augmentation fit

def write_bank(sc: dict[str, Any], path: Path, max_rows: int = 40000, seed: int = 0) -> int:
    """Ball-relative footprint polygons of SkillCorner broadcast frames, for ``augment.footprint_visibility``."""
    foot = sc["_footprints"]
    rel, ball = foot[:, :8].astype(np.float32), foot[:, 8:10].astype(np.float32)
    if len(rel) > max_rows:
        pick = np.random.default_rng(seed).choice(len(rel), max_rows, replace=False)
        rel, ball = rel[pick], ball[pick]
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, rel=rel, ball=ball, p_detect_in_footprint=np.float32(sc["p_detect_in_footprint"]),
                        ball_detected=np.float32(sc["ball_detected_share"]))
    return len(rel)


def seen_counts(ds, idx: np.ndarray, cfg, seed: int = 0, batch: int = 512) -> np.ndarray:
    """Number of seen players (of 22) per sample after the augmentation ``cfg`` with p_apply = 1."""
    from dataclasses import replace

    from matchlens.tacticsml.train.augment import view_mask_batch

    rng = np.random.default_rng(seed)
    out = []
    for s in range(0, len(idx), batch):
        b = ds.batch(idx[s:s + batch])
        nb, masked = view_mask_batch(b, rng, replace(cfg, p_apply=1.0))
        out.append(((nb["node_mask"] > 0) & ~masked)[:, :F.BALL].sum(1))
    return np.concatenate(out)


def count_table(counts: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    return {k: {"mean": float(v.mean()), **{f"p{q}": float(np.percentile(v, q)) for q in (10, 25, 50, 75, 90)}}
            for k, v in counts.items()}


def fit_footprint_scale(target: np.ndarray, test, idx: np.ndarray, grid=(1.0, 1.1, 1.2, 1.25, 1.3, 1.35, 1.4, 1.5)
                        ) -> dict[str, Any]:
    """Polygon scale about the ball that makes the seen-player counts of the replay match ``target`` (squared
    difference of the 10/25/50/75/90th percentiles and the mean)."""
    from dataclasses import replace

    from matchlens.tacticsml.train.augment import video_config

    qs = (10, 25, 50, 75, 90)
    want = np.r_[np.percentile(target, qs), target.mean()]
    loss = {}
    for sc in grid:
        c = seen_counts(test, idx, replace(video_config(), footprint_scale=sc))
        loss[sc] = float(((np.r_[np.percentile(c, qs), c.mean()] - want) ** 2).sum())
    best = min(loss, key=loss.get)
    return {"best": best, "loss": {str(k): v for k, v in loss.items()}}


def augmentation_fit(sc: dict[str, Any], video: np.ndarray | None, n: int = 4000, seed: int = 0) -> dict[str, Any]:
    """Seen-player counts of the round-1 cone and of the footprint replay on open-play test samples, next to the
    SkillCorner (footprint-frame) and video measurements they should reproduce."""
    from matchlens.tacticsml.train.augment import ViewMaskConfig, video_config
    from matchlens.tacticsml.train.data10 import load_next10

    data = load_next10(("sportec", "skillcorner"))
    test = data["test"]
    idx = np.flatnonzero(test.a["open_play"])
    idx = np.sort(np.random.default_rng(seed).choice(idx, min(n, len(idx)), replace=False))
    counts = {"skillcorner_detected_in_footprint": sc["_det_in_poly"] + 0.0,
              "round1_cone": seen_counts(test, idx, ViewMaskConfig(), seed),
              "footprint_replay": seen_counts(test, idx, video_config(), seed)}
    if video is not None:
        counts["video_test_lb"] = video
    return {"counts": count_table(counts), "scale_fit": fit_footprint_scale(sc["_det_in_poly"], test, idx)}


# ------------------------------------------------------------------ predictions on video frames

def prediction_stats(docs: list[dict]) -> dict[str, Any]:
    """Distributions of the model's answers over prediction docs (``kind: next10``)."""
    if not docs:
        return {"n": 0}
    shot = np.array([d["probs"]["shot10"] for d in docs])
    poss = np.array([d["probs"]["poss10"] for d in docs])
    zp = np.array([d["ball_zone10"]["p"] for d in docs], float)
    ent = -(zp * np.log(np.clip(zp, 1e-9, 1))).sum(1)
    rec = np.array([d["receivers"][0]["p"] if d["receivers"] else np.nan for d in docs])
    std = np.array([d["prob_std"]["shot10"] for d in docs])

    def q(a):
        return {"mean": float(np.nanmean(a)), **{f"p{k}": float(np.nanpercentile(a, k)) for k in (10, 50, 90)}}
    return {"n": len(docs), "shot10": q(shot), "poss10": q(poss), "zone_entropy_nats": q(ent), "zone_top_cell_p": q(zp.max(1)),
            "receiver_top1_p": q(rec), "shot10_member_sd": q(std),
            "n_players": q(np.array([d["features"]["n_players"] for d in docs], float))}


def video_predictions(path: Path, match_id: str = "test:lb") -> list[dict]:
    from matchlens.next10.predict import Predictor

    pr = Predictor(match_id)
    return pr.run(load_frames(path))


def compare_predictions(a: list[dict], b: list[dict]) -> dict[str, Any]:
    """Same video seconds under two calibrations: how far the answers move."""
    bb = {round(d["t_s"], 1): d for d in b}
    pairs = [(d, bb[round(d["t_s"], 1)]) for d in a if round(d["t_s"], 1) in bb]
    if not pairs:
        return {"n": 0}
    f = lambda g: np.array([g(x, y) for x, y in pairs], float)  # noqa: E731
    return {"n": len(pairs),
            "shot10_abs_diff": _pcts(np.abs(f(lambda x, y: x["probs"]["shot10"] - y["probs"]["shot10"]))),
            "poss10_abs_diff": _pcts(np.abs(f(lambda x, y: x["probs"]["poss10"] - y["probs"]["poss10"]))),
            "zone_expected_xy_shift_m": _pcts(f(lambda x, y: np.hypot(x["ball_zone10"]["expected_xy"][0] - y["ball_zone10"]["expected_xy"][0],
                                                                        x["ball_zone10"]["expected_xy"][1] - y["ball_zone10"]["expected_xy"][1]))),
            "same_possession_team": float(f(lambda x, y: x["possession_team_id"] == y["possession_team_id"]).mean())}


def reference_predictions(n: int = 3000, seed: int = 0, view: str = "clean", ckpt_dir: Path | None = None) -> dict[str, Any]:
    """Distribution of the same quantities on open-play test samples (torch ensemble), full tracking or masked like video."""
    from matchlens.tacticsml.train import evaluate10 as E10
    from matchlens.tacticsml.train import fit as _fit
    from matchlens.tacticsml.train.augment import video_config
    from matchlens.tacticsml.train.data10 import load_next10
    from matchlens.tacticsml.train.next10 import load_critic

    files = sorted(Path(ckpt_dir or schema.NEXT10 / "ckpt").glob("next10_seed*.pt"))
    dev = _fit.pick_device("auto")
    models = [load_critic(f, dev) for f in files]
    data = load_next10(("sportec", "skillcorner"), table=models[0][1])
    test = data["test"]
    idx = np.flatnonzero(test.a["open_play"])
    idx = np.sort(np.random.default_rng(seed).choice(idx, min(n, len(idx)), replace=False))
    if view == "clean":
        outs = E10.predict_members([m for m, _ in models], test, idx, dev)
    else:
        outs = E10.predict_members([m for m, _ in models], test, idx, dev, masked=True, view_cfg=video_config())
    pr = E10.ensemble(outs, "blend")
    zp = pr["zone"]
    ent = -(zp * np.log(np.clip(zp, 1e-9, 1))).sum(1)

    def q(a):
        return {"mean": float(np.nanmean(a)), **{f"p{k}": float(np.nanpercentile(a, k)) for k in (10, 50, 90)}}
    return {"n": int(len(idx)), "shot10": q(pr["shot"]), "poss10": q(pr["poss10"]), "zone_entropy_nats": q(ent),
            "zone_top_cell_p": q(zp.max(1))}


def main(argv: list[str] | None = None) -> int:
    from matchlens.tacticsml.train.sources import skillcorner as S

    p = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train.domain_gap", description=__doc__)
    p.add_argument("--frames", nargs="*", default=[], help="NDJSON frame files (label=path)")
    p.add_argument("--out", default=str(schema.NEXT10 / "domain_gap.json"))
    p.add_argument("--skillcorner-matches", type=int, default=None)
    p.add_argument("--bank", action="store_true", help="write the footprint bank (footprints.npz) from SkillCorner")
    p.add_argument("--predictions", action="store_true", help="run the deployed Next10 model on the frame files")
    p.add_argument("--ckpt-dir", default=None, help="critic checkpoints for the reference predictions (default next10/ckpt)")
    p.add_argument("--augmentation", action="store_true", help="seen-player counts of the augmentations on test samples")
    a = p.parse_args(argv)
    t0 = time.time()
    out: dict[str, Any] = {"video": {}}
    video_counts = None
    for spec in a.frames:
        label, path = spec.split("=", 1)
        rows = load_frames(Path(path))
        out["video"][label] = video_stats(rows)
        if video_counts is None:
            video_counts = np.array([sum(1 for e in r["entities"] if e["team"] in ("home", "away", "unk")) for r in rows
                                     if r.get("calibrated")])
    sc = skillcorner_stats(S.MATCH_IDS, limit=a.skillcorner_matches)
    out["view_mask_fit"] = fit_view_mask(sc)
    out["skillcorner"] = {k: v for k, v in sc.items() if not k.startswith("_")}
    from matchlens.tacticsml.train.sources import sportec
    out["sportec"] = table_stats(sportec.load("J03WPY")[0])
    out["metrica"] = table_stats(metrica_table("1"))
    if a.bank:
        out["bank_rows"] = write_bank(sc, schema.NEXT10 / "footprints.npz")
    if a.augmentation:
        out["seen_counts"] = augmentation_fit(sc, video_counts)
    if a.predictions:
        out["predictions"] = {"reference_clean": reference_predictions(view="clean", ckpt_dir=a.ckpt_dir),
                              "reference_video_masked": reference_predictions(view="video", ckpt_dir=a.ckpt_dir)}
        by_label: dict[str, list[dict]] = {}
        for spec in a.frames:
            label, path = spec.split("=", 1)
            by_label[label] = video_predictions(Path(path), "clip:liv-bha-2627" if "clip" in label else "test:lb")
            out["predictions"][label] = prediction_stats(by_label[label])
        if "test_lb_perframe" in by_label and "test_lb_bundle" in by_label:
            out["bundle_vs_perframe"] = compare_predictions(by_label["test_lb_perframe"], by_label["test_lb_bundle"])
    out["seconds"] = round(time.time() - t0)
    Path(a.out).write_text(json.dumps(out, indent=1, default=float))
    print(f"wrote {a.out}")
    return 0


def metrica_table(game: str):
    from kloppy import metrica

    from matchlens.tacticsml.train.sources import metrica as M
    from matchlens.tacticsml.train.sources._common import table_from_kloppy

    home, away, _ = M.fetch(game)
    ds = metrica.load_tracking_csv(home_data=str(home), away_data=str(away), sample_rate=M.SAMPLE_RATE)
    return table_from_kloppy(ds, lambda fr: fr.timestamp.total_seconds())


if __name__ == "__main__":
    raise SystemExit(main())

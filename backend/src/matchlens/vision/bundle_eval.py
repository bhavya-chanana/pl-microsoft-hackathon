"""Whole-match calibration against the per-frame chain, on one broadcast clip.

    uv run --group vision python -m matchlens.vision.bundle_eval LiverpoolBrighton [--length 105] [--stride 5]

Needs ``data/local/broadcast/<clip>/calibration.json`` (``python -m matchlens.vision.broadcast``), its
``sdr.mov`` or the clip itself, and optionally ``labels.json``. Steps:

1. Painted-line pixels of every main-camera frame (960 px wide), cached in ``paint.npz``. The file is decoded
   from the start in every worker: seeking is not frame-exact on these clips.
2. 10% of each frame's paint is held out. Round 0 matches the other 90% to the markings under the per-frame
   homographies and solves the match; round 1 matches again under round 0's cameras (reaching frames the per-frame
   chain lost) and solves again. Labelled frames use their left-out calibrations, so no label reaches the bundle.
3. Scores: calibrated share; held-out line error of the bundle against the existing homographies and against a
   fresh 8-parameter refit of each frame on the same 90%; the hand labels; jitter of the projected pitch corners.

Writes ``bundle_report.json`` next to the clip's other outputs.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from . import bundle as B
from .footage import DATA

SIZE = (960, 544)
N_PAINT = 1500
HELD_OUT = 0.10
FRAME_STEP = 500


# --- paint ---------------------------------------------------------------------------------------------------


def _extract(args: tuple[str, int, int, list[int]]) -> dict[int, np.ndarray]:
    import cv2

    from .camera import FrameLines

    source, lo, hi, mains = args
    wanted = set(mains)
    cv2.setNumThreads(1)
    cap = cv2.VideoCapture(source)
    for _ in range(lo):
        cap.grab()
    out = {}
    for i in range(lo, hi):
        ok, frame = cap.read()
        if not ok:
            break
        if i in wanted:
            small = cv2.resize(frame, SIZE, interpolation=cv2.INTER_AREA)
            paint = FrameLines(small).paint
            if len(paint) > N_PAINT:
                paint = paint[np.linspace(0, len(paint) - 1, N_PAINT).astype(int)]
            out[i] = paint.astype(np.int16)
    return out


def paint_for(work: Path, source: Path, main: list[int], total: int, workers: int = 8) -> dict[int, np.ndarray]:
    cache = work / "paint.npz"
    if cache.exists():
        z = np.load(cache)
        off = np.r_[0, np.cumsum(z["lens"])]
        return {int(f): z["paint"][off[i]:off[i + 1]].astype(np.float64) for i, f in enumerate(z["frames"])}
    jobs = [(str(source), a, min(a + FRAME_STEP, total), [m for m in main if a <= m < a + FRAME_STEP])
            for a in range(0, total, FRAME_STEP)]
    found: dict[int, np.ndarray] = {}
    with ProcessPoolExecutor(workers) as pool:
        for part in pool.map(_extract, jobs):
            found.update(part)
    keys = sorted(found)
    np.savez_compressed(cache, frames=np.array(keys), lens=np.array([len(found[k]) for k in keys]),
                        paint=np.concatenate([found[k] for k in keys]))
    return {k: found[k].astype(np.float64) for k in keys}


# --- helpers -------------------------------------------------------------------------------------------------


def proj_h(h: np.ndarray):
    def f(xy: np.ndarray) -> np.ndarray:
        p = np.c_[xy, np.ones(len(xy))] @ h.T
        with np.errstate(all="ignore"):
            out = p[:, :2] / p[:, 2:]
        out[p[:, 2] <= 1e-9] = np.nan
        return out
    return f


def line_error(project, paint: np.ndarray, model: B.PitchModel, tol: float):
    """Paint matched to markings under ``project``: per matched point the pixel and metre distance across the line."""
    img, pit, tan = B.associate(paint, project, model, tol, SIZE)
    if not len(img):
        return np.zeros(0), np.zeros(0)
    u = project(pit + 0.5 * tan) - project(pit - 0.5 * tan)
    u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-9)
    e = project(pit) - img
    px = np.abs(u[:, 0] * e[:, 1] - u[:, 1] * e[:, 0])
    normal = np.c_[-tan[:, 1], tan[:, 0]]
    d = project(pit + 0.5 * normal) - project(pit - 0.5 * normal)  # pixels per metre across the line
    across = np.abs(d[:, 0] * -u[:, 1] + d[:, 1] * u[:, 0])
    m = px / np.maximum(across, 1e-6)
    ok = np.isfinite(px) & np.isfinite(m)
    return px[ok], m[ok]


def refit_homography(paint: np.ndarray, h0: np.ndarray, model: B.PitchModel, iters: int = 2):
    """The per-frame approach's model on the same points: 8 homography parameters, point-to-line residuals."""
    from scipy.optimize import least_squares

    h = h0 / h0[2, 2]
    norm = np.array([[2 / SIZE[0], 0, -1], [0, 2 / SIZE[1], -1], [0, 0, 1.0]])
    for it in range(iters):
        img, pit, tan = B.associate(paint, proj_h(h), model, 8.0 * (1 if it == 0 else 0.6), SIZE)
        if len(img) < 40:
            return None
        base = norm @ h

        def hom(d: np.ndarray, base: np.ndarray = base) -> np.ndarray:
            step = np.eye(3) + np.array([[d[0], d[1], d[2]], [d[3], d[4], d[5]], [d[6], d[7], 0.0]])
            return np.linalg.solve(norm, step @ base)

        def res(d: np.ndarray, hom=hom, img=img, pit=pit, tan=tan) -> np.ndarray:
            pr = proj_h(hom(d))
            u = pr(pit + 0.5 * tan) - pr(pit - 0.5 * tan)
            u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-9)
            e = pr(pit) - img
            r = u[:, 0] * e[:, 1] - u[:, 1] * e[:, 0]
            return np.where(np.isfinite(r), r, 1e3)

        sol = least_squares(res, np.zeros(8), loss="soft_l1", f_scale=2.0, x_scale=0.01, max_nfev=60)
        h = hom(sol.x)
        h /= h[2, 2]
    return h


def _quant(a: np.ndarray, q: float):
    return float(np.percentile(a, q)) if a.size else None


def _agg(rs: list, ms: list, total: int) -> dict:
    r = np.concatenate(rs) if rs else np.zeros(0)
    m = np.concatenate(ms) if ms else np.zeros(0)
    return {"points": int(r.size), "coverage": r.size / max(total, 1), "px_median": _quant(r, 50),
            "px_p90": _quant(r, 90), "m_median": _quant(m, 50), "m_p90": _quant(m, 90)}


# --- the run -------------------------------------------------------------------------------------------------


def solve_rounds(paint_train: dict[int, np.ndarray], h960: dict[int, np.ndarray], cuts: np.ndarray, fps: float,
                 length: float, options: B.SolveOptions):
    model = B.PitchModel(length, 68.0)
    prior0 = B.CameraPrior(pitch_length=length, pitch_width=68.0)

    def shot(f: int) -> int:
        return int(np.searchsorted(cuts, f, side="right"))

    def empty(f: int) -> B.FrameObs:
        return B.FrameObs(frame=f, t=f / fps, image_pts=np.zeros((0, 2)), pitch_pts=np.zeros((0, 2)), size=SIZE,
                          shot=shot(f))

    obs0 = [B.observations_from_paint(paint_train[f], h960[f], size=SIZE, frame=f, t=f / fps, shot=shot(f),
                                      model=model, tol_px=8.0) if f in h960 else empty(f) for f in sorted(paint_train)]
    r0 = B.solve_match(obs0, prior0, options)
    cams = {c.frame: c for c in r0.frames if c.usable}
    keys = np.array(sorted(cams))

    def camera_near(f: int):
        if f in cams:
            return cams[f]
        if not len(keys):
            return None
        j = int(np.searchsorted(keys, f))
        cand = [int(keys[k]) for k in (j - 1, j) if 0 <= k < len(keys)]
        best = min(cand, key=lambda k: abs(k - f))
        return cams[best] if abs(best - f) <= 28 and shot(best) == shot(f) else None

    obs1 = []
    for f in sorted(paint_train):
        c = camera_near(f)
        obs1.append(empty(f) if c is None else B.observations_from_paint(
            paint_train[f], c.pitch_to_image, size=SIZE, frame=f, t=f / fps, shot=shot(f), model=model, tol_px=5.0,
            init_h=c.homography))
    r1 = B.solve_match(obs1, r0.prior, options)
    return r0, r1


def evaluate(clip: str, length: float = 105.0, stride: int = 5, workers: int = 8, out: str = "bundle_report",
             log=print) -> dict:
    work = DATA / clip
    cal = json.loads((work / "calibration.json").read_text())
    source = work / cal["source"] if (work / cal["source"]).exists() else DATA / "clips" / cal["clip"]
    half = np.diag([SIZE[0] / cal["size"][0]] * 2 + [1.0])
    fps = cal["fps"]
    cuts = np.array(cal["cuts"])
    h960 = {f["frame"]: half @ np.array(f["homography"]) for f in cal["frames"] if "homography" in f}
    for k, v in cal["left_out"].items():  # an anchor frame's own label was in its calibration: use the left-out one
        h960[int(k)] = half @ np.array(v["homography"])
    main = [f["frame"] for f in cal["frames"] if f["main"]]
    log(f"{len(main)} main-camera frames; extracting paint ...")
    paint = paint_for(work, source, main, len(cal["frames"]), workers)

    rng = np.random.default_rng(0)
    train, held = {}, {}
    for f in sorted(paint):
        mask = rng.random(len(paint[f])) < HELD_OUT
        held[f], train[f] = paint[f][mask], paint[f][~mask]
    t0 = time.time()
    log("solving (two rounds) ...")
    r0, r1 = solve_rounds(train, h960, cuts, fps, length, B.SolveOptions(max_joint_frames=800))
    log(f"solved in {time.time() - t0:.0f} s")
    cams = {c.frame: c for c in r1.frames}
    model = B.PitchModel(length, 68.0)
    model105 = B.PitchModel(105.0, 68.0)

    def shot(f: int) -> int:
        return int(np.searchsorted(cuts, f, side="right"))

    report: dict = {"clip": clip, "pitch_length_m": length, "frame_width_px": SIZE[0],
                    "camera": r1.prior.to_dict(), "summary": r1.summary(),
                    "method": "round 0 + round 1 of solve_match on 90% of paint; 10% held out"}
    # (a) share
    nm = len(main)
    base = [f for f in main if f in h960]
    fitted = sum(1 for f in main if f in cams and cams[f].status == "fitted")
    interp = sum(1 for f in main if f in cams and cams[f].status == "interpolated")
    report["share"] = {"main_frames": nm, "per_frame_calibrated": len(base), "per_frame_share": len(base) / nm,
                       "bundle_fitted": fitted, "bundle_interpolated": interp, "bundle_fitted_share": fitted / nm,
                       "bundle_fitted_plus_interpolated_share": (fitted + interp) / nm}
    # (b) held-out
    groups = {k: ([], [], [0]) for k in ("bundle", "per_frame_existing", "per_frame_refit_8dof")}
    used = 0
    for f in [f for f in sorted(held) if f % stride == 0]:
        if f not in h960 or f not in cams or cams[f].status != "fitted" or len(held[f]) < 20:
            continue
        used += 1
        tot = len(held[f])
        for name, proj, mdl in (("bundle", cams[f].pitch_to_image, model), ("per_frame_existing", proj_h(h960[f]), model105)):
            r, m = line_error(proj, held[f], mdl, 6.0)
            groups[name][0].append(r), groups[name][1].append(m)
            groups[name][2][0] += tot
        h2 = refit_homography(train[f], h960[f], model105)
        if h2 is not None:
            r, m = line_error(proj_h(h2), held[f], model105, 6.0)
            groups["per_frame_refit_8dof"][0].append(r), groups["per_frame_refit_8dof"][1].append(m)
            groups["per_frame_refit_8dof"][2][0] += tot
    report["held_out"] = {"frames_compared": used, "tolerance_px": 6.0,
                          **{k: _agg(v[0], v[1], v[2][0]) for k, v in groups.items()}}
    # (b2) hand labels
    labels_path = work / "labels.json"
    rows, acc = [], {"bundle": ([], []), "per_frame": ([], [])}
    if labels_path.exists():
        lm_new, lm_105 = model.landmarks(), model105.landmarks()
        for k, v in json.loads(labels_path.read_text())["frames"].items():
            f = int(k)
            if v["view"] != "main" or not v["points"] or f not in cams:
                continue
            names = [n for n in v["points"] if n in lm_new]
            px = np.array([v["points"][n] for n in names]) * SIZE[0] / cal["size"][0]
            row = {"frame": f, "points": len(names)}
            c = cams[f]
            if c.usable:
                xy = np.array([lm_new[n] for n in names])
                m = np.linalg.norm(c.image_to_pitch(px) - xy, axis=1)
                p = np.linalg.norm(c.pitch_to_image(xy) - px, axis=1)
                row["bundle"] = {"status": c.status, "m_median": float(np.median(m)), "px_median": float(np.median(p))}
                acc["bundle"][0].append(m), acc["bundle"][1].append(p)
            if f in h960:
                xy = np.array([lm_105[n] for n in names])
                inv = np.linalg.inv(h960[f])
                q = np.c_[px, np.ones(len(px))] @ inv.T
                m = np.linalg.norm(q[:, :2] / q[:, 2:] - xy, axis=1)
                p = np.linalg.norm(proj_h(h960[f])(xy) - px, axis=1)
                row["per_frame"] = {"m_median": float(np.median(m)), "px_median": float(np.median(p))}
                acc["per_frame"][0].append(m), acc["per_frame"][1].append(p)
            rows.append(row)

        def lab(ms: list, ps: list) -> dict:
            m = np.concatenate(ms) if ms else np.zeros(0)
            p = np.concatenate(ps) if ps else np.zeros(0)
            return {"points": int(m.size), "m_median": _quant(m, 50), "m_p90": _quant(m, 90),
                    "px_median": _quant(p, 50), "px_p90": _quant(p, 90)}
        report["labels"] = {"bundle": lab(*acc["bundle"]), "per_frame_left_out": lab(*acc["per_frame"]), "frames": rows}
    # (c) jitter
    corners = np.array([[0, 0], [length, 0], [length, 68], [0, 68.0]])
    corners105 = np.array([[0, 0], [105, 0], [105, 68], [0, 68.0]])
    centre = np.array([[SIZE[0] / 2, SIZE[1] / 2]])

    def camera(kind: str, f: int):
        c = cams.get(f)
        if c is None or c.status != "fitted" or (kind == "per_frame" and f not in h960):
            return None
        if kind == "bundle":
            return c.pitch_to_image, corners, lambda: c.image_to_pitch(centre)[0]
        inv = np.linalg.inv(h960[f])
        return proj_h(h960[f]), corners105, lambda: (lambda p: p[:2] / p[2])(inv @ np.r_[centre[0], 1.0])

    def jitter(kind: str) -> dict:
        px, mm = [], []
        for f in sorted(held):
            if f - 1 not in held or f + 1 not in held or shot(f - 1) != shot(f + 1):
                continue
            trio = [camera(kind, g) for g in (f - 1, f, f + 1)]
            if any(t is None for t in trio):
                continue
            pa, pb, pc = (t[0](t[1]) for t in trio)
            d2 = np.linalg.norm(pa - 2 * pb + pc, axis=1)
            ok = np.isfinite(d2) & (np.abs(pb) < 3 * np.array(SIZE)).all(axis=1)
            px += list(d2[ok])
            mm.append(float(np.linalg.norm(trio[0][2]() - 2 * trio[1][2]() + trio[2][2]())))
        a, b = np.array(px), np.array(mm)
        return {"corner_px_median": _quant(a, 50), "corner_px_p90": _quant(a, 90), "samples": int(a.size),
                "centre_footprint_m_median": _quant(b, 50), "centre_footprint_m_p90": _quant(b, 90)}
    report["jitter_second_difference"] = {"bundle": jitter("bundle"), "per_frame": jitter("per_frame")}
    (work / f"{out}.json").write_text(json.dumps(report, indent=1, default=lambda o: None if o is None else str(o)))
    (work / f"{out}_calibration.json").write_text(json.dumps(r1.to_dict()))
    log(f"-> {work / (out + '.json')}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clip", help="clip name, as the folder under data/local/broadcast/")
    parser.add_argument("--length", type=float, default=105.0, help="pitch length in metres for the markings model")
    parser.add_argument("--stride", type=int, default=5, help="score every n-th frame held out")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", default="bundle_report", help="output name stem under the clip folder")
    args = parser.parse_args()
    report = evaluate(args.clip, args.length, args.stride, args.workers, args.out)
    print(json.dumps({k: report[k] for k in ("share", "held_out", "labels", "jitter_second_difference")
                      if k in report}, indent=1))


if __name__ == "__main__":
    main()

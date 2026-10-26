"""Calibrate a broadcast clip frame by frame, and measure it against hand labels.

    uv run --group vision python -m matchlens.vision.broadcast [path/to/clip.mp4] --overlay

(with no clip, the newest video in ``data/local/broadcast/clips/``)

1. Sorts the frames into main camera and other views (``views.py``).
2. Calibrates the main-camera frames (``camera.py``). Every labelled main-camera frame with four or
   more landmarks is a starting point: it sets the calibration when the run reaches it, and is a
   keyframe from the outset, so a cut to its part of the pitch can be found before the run gets
   there. The run goes from the first of them to the end, then from the start up to it.
3. Scores both steps against ``labels.json`` (made with ``python -m matchlens.vision.label``):
   the view filter on every labelled frame, and the calibration on every labelled main-camera
   frame, each with its own label left out. A starting frame is scored by a run of its own without
   it, in parallel with the main run; any other labelled frame by the main run, which never used
   its label. Calibration error is where a labelled pixel lands on the pitch against where that
   landmark really is (metres), and where the landmark is drawn against where it was clicked
   (pixels). For scale, the same is shown for a camera that never moved from the most-labelled frame.

An HDR clip is read from its standard-colour copy (``footage.py``), and pixels are that copy's.

Writes ``data/local/broadcast/<clip>/calibration.json`` (each frame's view and its homography from
pitch metres to clip pixels), and with ``--overlay`` an ``overlay.mp4`` with the pitch drawn on
every calibrated frame plus a still of each labelled one under ``check/`` (drawn with its label
left out, as scored).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .camera import Calibration, CameraTracker, plausible, project, scaled, to_pitch
from .footage import CLIPS, default_clip, prepare, workdir
from .markings import landmarks, polylines
from .views import Views, classify

WORK_WIDTH = 960
MIN_ANCHOR_POINTS = 4


@dataclass(frozen=True)
class Labelled:
    frame: int
    main: bool
    names: list[str]
    pixels: np.ndarray  # (N, 2) clip pixels
    pitch: np.ndarray  # (N, 2) metres


def read_labels(path: Path) -> list[Labelled]:
    if not path.exists():
        return []
    known = landmarks()
    out = []
    for frame, entry in json.loads(path.read_text())["frames"].items():
        names = [n for n in entry.get("points", {}) if n in known]
        out.append(Labelled(
            frame=int(frame), main=entry.get("view") == "main", names=names,
            pixels=np.array([entry["points"][n] for n in names], np.float64).reshape(-1, 2),
            pitch=np.array([known[n] for n in names], np.float64).reshape(-1, 2),
        ))
    return sorted(out, key=lambda lab: lab.frame)


def frames(clip: Path, width: int) -> Iterator[np.ndarray]:
    capture = cv2.VideoCapture(str(clip))
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                return
            h, w = frame.shape[:2]
            yield frame if w == width else cv2.resize(frame, (width, round(h * width / w)),
                                                      interpolation=cv2.INTER_AREA)
    finally:
        capture.release()


def clip_info(clip: Path) -> tuple[tuple[int, int], float]:
    capture = cv2.VideoCapture(str(clip))
    try:
        size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        return size, float(capture.get(cv2.CAP_PROP_FPS)) or 25.0
    finally:
        capture.release()


def keep(stream: Iterable[np.ndarray], wanted: set[int], into: dict[int, np.ndarray]) -> Iterator[np.ndarray]:
    """Pass frames through, keeping the ``wanted`` ones in ``into``."""
    for i, frame in enumerate(stream):
        if i in wanted:
            into[i] = frame
        yield frame


def calibrate(clip: Path, views: Views, anchors: dict[int, np.ndarray], images: dict[int, np.ndarray],
              factor: float, until: int | None = None) -> list[Calibration | None]:
    """Each frame's calibration, in clip pixels. ``anchors`` are clip-pixel homographies by frame and
    ``images`` those frames at the working size. With ``until``, stops once that frame is done."""
    work = {f: scaled(h, factor) for f, h in anchors.items()}
    start = min(work, default=0)
    cut = set(views.cuts)
    out: list[Calibration | None] = [None] * len(views.main)
    tracker = CameraTracker()
    for f, h in work.items():
        tracker.seed(images[f], h)
    # From the first anchor to the end, then from the start up to it (the keyframes carry over).
    for lo, hi in ((start, len(out)), (0, start)):
        tracker.reset()
        for i, frame in enumerate(frames(clip, WORK_WIDTH)):
            if i >= hi:
                break
            if i < lo:
                continue
            cal = tracker.step(frame, main=bool(views.main[i]), cut=i in cut, anchor=work.get(i))
            if cal is not None:
                out[i] = Calibration(scaled(cal.homography, 1 / factor), cal.score, cal.visible, cal.how)
            if i == until:
                return out
    return out


def _left_out(clip: Path, views: Views, anchors: dict[int, np.ndarray], images: dict[int, np.ndarray],
              factor: float, frame: int, filtered: bool) -> tuple[int, Calibration | None]:
    """Calibrate up to ``frame`` without its label, and with the view filter's own call on it."""
    cv2.setNumThreads(2)
    main = views.main.copy()
    main[frame] = filtered
    rest = {f: h for f, h in anchors.items() if f != frame}
    if not rest:
        return frame, None
    return frame, calibrate(clip, Views(main, views.cuts, []), rest, images, factor, until=frame)[frame]


def errors(h: np.ndarray, lab: Labelled) -> tuple[np.ndarray, np.ndarray]:
    """Per landmark: metres on the pitch, and pixels in the frame."""
    metres = np.linalg.norm(to_pitch(h, lab.pixels) - lab.pitch, axis=1)
    drawn, _ = project(h, lab.pitch)
    return metres, np.linalg.norm(drawn - lab.pixels, axis=1)


def summary(values: list[np.ndarray]) -> str:
    v = np.concatenate(values) if values else np.array([])
    if v.size == 0:
        return "-"
    return f"median {np.median(v):.2f}, p90 {np.percentile(v, 90):.2f}, max {v.max():.2f} ({v.size} points)"


def report(views: Views, results: list[Calibration | None], labels: list[Labelled],
           scored: dict[int, Calibration | None], filtered: np.ndarray, best: int | None,
           best_h: np.ndarray | None) -> None:
    """``scored`` holds each labelled frame's calibration with its label left out; ``filtered`` is the
    view filter's call on every frame, before the labelled ones were made main."""
    n = len(results)
    main = int(views.main.sum())
    done = sum(r is not None for r in results)
    hows: dict[str, int] = {}
    for r in results:
        if r is not None:
            hows[r.how] = hows.get(r.how, 0) + 1
    print(f"\n{n} frames, {len(views.shots())} shots; main camera {main} ({main / max(n, 1):.0%})")
    print(f"calibrated {done} of {main} main-camera frames ({done / max(main, 1):.0%}): "
          + ", ".join(f"{k} {v}" for k, v in sorted(hows.items())))
    if not labels:
        print("no labels: run python -m matchlens.vision.label to measure")
        return

    seen = [lab for lab in labels if lab.frame < n]
    tp = sum(filtered[lab.frame] and lab.main for lab in seen)
    fp = sum(filtered[lab.frame] and not lab.main for lab in seen)
    fn = sum(not filtered[lab.frame] and lab.main for lab in seen)
    tn = len(seen) - tp - fp - fn
    print(f"\nview filter on {len(seen)} labelled frames: {tp} main kept, {tn} other dropped, "
          f"{fn} main dropped, {fp} other kept")
    if tp + fn:
        print(f"  main camera: recall {tp / (tp + fn):.0%}" + (f", precision {tp / (tp + fp):.0%}" if tp + fp else ""))

    held = [lab for lab in seen if lab.main and len(lab.names)]
    metres, pixels, base_m, base_px, missing = [], [], [], [], []
    for lab in held:
        cal = scored.get(lab.frame)
        if cal is None:
            missing.append(lab.frame)
        else:
            m, p = errors(cal.homography, lab)
            metres.append(m)
            pixels.append(p)
            print(f"  {lab.frame}: {cal.how}, score {cal.score:.2f}, {np.median(m):.2f} m, {np.median(p):.1f} px")
        if best_h is not None and lab.frame != best:
            m, p = errors(best_h, lab)
            base_m.append(m)
            base_px.append(p)
    dropped = [f for f in missing if not filtered[f]]
    print(f"\ncalibration on {len(held)} labelled main-camera frames, each with its own label left out:")
    print(f"  calibrated {len(held) - len(missing)} of {len(held)}" + (f"; missed {missing}" if missing else "")
          + (f" ({dropped} dropped by the view filter)" if dropped else ""))
    print(f"  metres: {summary(metres)}")
    print(f"  pixels: {summary(pixels)}")
    print(f"  camera held at frame {best}, metres: {summary(base_m)}")
    print(f"  camera held at frame {best}, pixels: {summary(base_px)}")


def draw(frame: np.ndarray, cal: Calibration | None, main: bool, index: int) -> np.ndarray:
    out = frame.copy()
    if cal is not None:
        for line in polylines(0.25):
            pts, front = project(cal.homography, line)
            if front.all():
                cv2.polylines(out, [pts.round().astype(np.int32)], False, (255, 0, 255), 2, cv2.LINE_AA)
        text = f"#{index} {cal.how} {cal.score:.2f}"
    else:
        out = (out * 0.5).astype(np.uint8)
        text = f"#{index} {'main, not calibrated' if main else 'other view'}"
    cv2.putText(out, text, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(out, text, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def overlay(clip: Path, out: Path, views: Views, results: list[Calibration | None], labels: list[Labelled],
            scored: dict[int, Calibration | None], size: tuple[int, int], fps: float) -> None:
    check = out / "check"
    check.mkdir(parents=True, exist_ok=True)
    labelled = {lab.frame: lab for lab in labels}
    writer = cv2.VideoWriter(str(out / "overlay.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    try:
        for i, frame in enumerate(frames(clip, size[0])):
            if i >= len(results):
                break
            image = draw(frame, results[i], bool(views.main[i]), i)
            writer.write(image)
            if i in labelled:
                image = draw(frame, scored.get(i, results[i]), bool(views.main[i]), i)
                for x, y in labelled[i].pixels:
                    cv2.circle(image, (round(x), round(y)), 6, (0, 255, 255), 2)
                cv2.imwrite(str(check / f"{i}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    finally:
        writer.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clip", type=Path, nargs="?", help=f"default: the newest video in {CLIPS}")
    parser.add_argument("--overlay", action="store_true", help="also write overlay.mp4 and check/ stills")
    args = parser.parse_args()
    clip = (args.clip or default_clip()).resolve()
    out = workdir(clip)
    out.mkdir(parents=True, exist_ok=True)
    source = prepare(clip)
    size, fps = clip_info(source)
    if size[0] <= 0:
        raise SystemExit(f"cannot read {source}")
    factor = WORK_WIDTH / size[0]

    labels = read_labels(out / "labels.json")
    # Starting points: four or more landmarks that give a believable camera (not all on one line).
    anchors: dict[int, np.ndarray] = {}
    for lab in labels:
        if lab.main and len(lab.names) >= MIN_ANCHOR_POINTS:
            h, _ = cv2.findHomography(lab.pitch, lab.pixels, 0)
            if h is not None and plausible(h, (size[1], size[0])):
                anchors[lab.frame] = h
    best = max((lab for lab in labels if lab.frame in anchors), key=lambda lab: (len(lab.names), -lab.frame),
               default=None)

    print(f"{clip.name}: {size[0]}x{size[1]} at {fps:g} fps; sorting views ...")
    images: dict[int, np.ndarray] = {}
    views = classify(keep(frames(source, WORK_WIDTH), set(anchors), images))
    filtered = views.main.copy()
    anchors = {f: h for f, h in anchors.items() if f in images}
    for f in anchors:
        views.main[f] = True  # a person said so

    results: list[Calibration | None] = [None] * len(views.main)
    scored: dict[int, Calibration | None] = {}
    if anchors:
        print(f"starting points: frames {sorted(anchors)}; calibrating, and again without each of them ...")
        workers = max(1, min(len(anchors), (os.cpu_count() or 2) // 2))
        with ProcessPoolExecutor(workers) as pool:
            pending = [pool.submit(_left_out, source, views, anchors, images, factor, f, bool(filtered[f]))
                       for f in sorted(anchors)]
            results = calibrate(source, views, anchors, images, factor)
            scored = dict(job.result() for job in pending)
    else:
        print("no labelled main-camera frame with 4 or more landmarks: nothing to start the calibration from")
    scored.update({lab.frame: results[lab.frame] for lab in labels
                   if lab.frame not in anchors and lab.frame < len(results)})
    report(views, results, labels, scored, filtered, best.frame if best else None,
           anchors[best.frame] if best else None)

    (out / "calibration.json").write_text(json.dumps({
        "clip": clip.name, "source": source.name, "size": size, "fps": fps, "cuts": views.cuts,
        "frames": [{"frame": i, "main": bool(views.main[i]),
                    **({"homography": r.homography.round(8).tolist(), "score": round(r.score, 3), "how": r.how}
                       if r is not None else {})}
                   for i, r in enumerate(results)],
        "left_out": {str(f): {"homography": r.homography.round(8).tolist(), "score": round(r.score, 3), "how": r.how}
                     for f, r in scored.items() if f in anchors and r is not None},
    }))
    print(f"\n-> {out / 'calibration.json'}")
    if args.overlay:
        overlay(source, out, views, results, labels, scored, size, fps)
        print(f"-> {out / 'overlay.mp4'} and {out / 'check'}/")


if __name__ == "__main__":
    main()

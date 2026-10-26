"""Calibrate a broadcast clip frame by frame, and measure it against hand labels.

    uv run --group vision python -m matchlens.vision.broadcast [path/to/clip.mp4] --overlay

(with no clip, the newest video in ``data/local/broadcast/clips/``)

1. Sorts the frames into main camera and other views (``views.py``).
2. Starts the calibration from the labelled frame with the most landmarks and carries it through
   the clip, forwards and then from the start (``camera.py``).
3. Scores both steps against ``labels.json`` (made with ``python -m matchlens.vision.label``):
   the view filter on every labelled frame, and the calibration on every labelled main-camera frame
   except the one it started from. Calibration error is where a labelled pixel lands on the pitch
   against where that landmark really is (metres), and where the landmark is drawn against where
   it was clicked (pixels). For scale, the same is shown for a camera that never moved from the
   starting frame.

An HDR clip is read from its standard-colour copy (``footage.py``), and pixels are that copy's.

Writes ``data/local/broadcast/<clip>/calibration.json`` (each frame's view and its homography from
pitch metres to clip pixels), and with ``--overlay`` an ``overlay.mp4`` with the pitch drawn on
every calibrated frame plus a still of each labelled one under ``check/``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .camera import Calibration, CameraTracker, project, scaled, to_pitch
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


def calibrate(clip: Path, views: Views, anchors: dict[int, np.ndarray], factor: float) -> list[Calibration | None]:
    """Each frame's calibration, in clip pixels. ``anchors`` are clip-pixel homographies by frame."""
    work = {f: scaled(h, factor) for f, h in anchors.items()}
    start = min(work, default=0)
    cut = set(views.cuts)
    out: list[Calibration | None] = [None] * len(views.main)
    tracker = CameraTracker()
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
    return out


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


def report(views: Views, results: list[Calibration | None], labels: list[Labelled], anchor: int | None,
           anchor_h: np.ndarray | None) -> None:
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
    tp = sum(views.main[lab.frame] and lab.main for lab in seen)
    fp = sum(views.main[lab.frame] and not lab.main for lab in seen)
    fn = sum(not views.main[lab.frame] and lab.main for lab in seen)
    tn = len(seen) - tp - fp - fn
    print(f"\nview filter on {len(seen)} labelled frames: {tp} main kept, {tn} other dropped, "
          f"{fn} main dropped, {fp} other kept")
    if tp + fn:
        print(f"  main camera: recall {tp / (tp + fn):.0%}" + (f", precision {tp / (tp + fp):.0%}" if tp + fp else ""))

    held = [lab for lab in seen if lab.main and len(lab.names) and lab.frame != anchor]
    metres, pixels, base_m, base_px, missing = [], [], [], [], []
    for lab in held:
        cal = results[lab.frame]
        if cal is None:
            missing.append(lab.frame)
        else:
            m, p = errors(cal.homography, lab)
            metres.append(m)
            pixels.append(p)
        if anchor_h is not None:
            m, p = errors(anchor_h, lab)
            base_m.append(m)
            base_px.append(p)
    print(f"\ncalibration on {len(held)} labelled main-camera frames (frame {anchor} started it, not scored):")
    print(f"  calibrated {len(held) - len(missing)} of {len(held)}" + (f"; missed {missing}" if missing else ""))
    print(f"  metres: {summary(metres)}")
    print(f"  pixels: {summary(pixels)}")
    print(f"  camera held at the start frame, metres: {summary(base_m)}")
    print(f"  camera held at the start frame, pixels: {summary(base_px)}")


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
            size: tuple[int, int], fps: float) -> None:
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

    print(f"{clip.name}: {size[0]}x{size[1]} at {fps:g} fps; sorting views ...")
    views = classify(frames(source, WORK_WIDTH))

    labels = read_labels(out / "labels.json")
    usable = [lab for lab in labels if lab.main and len(lab.names) >= MIN_ANCHOR_POINTS]
    anchor, anchor_h, anchors = None, None, {}
    if usable:
        best = max(usable, key=lambda lab: (len(lab.names), -lab.frame))
        anchor_h, _ = cv2.findHomography(best.pitch, best.pixels, 0)
        anchor, anchors = best.frame, {best.frame: anchor_h}
        if not views.main[anchor]:
            print(f"note: the view filter dropped start frame {anchor}; calibrating it anyway")
            views.main[anchor] = True
        print(f"starting from frame {anchor} ({len(best.names)} landmarks); calibrating ...")
    else:
        print("no labelled main-camera frame with 4 or more landmarks: nothing to start the calibration from")

    results = calibrate(source, views, anchors, factor) if anchors else [None] * len(views.main)
    report(views, results, labels, anchor, anchor_h)

    (out / "calibration.json").write_text(json.dumps({
        "clip": clip.name, "source": source.name, "size": size, "fps": fps, "cuts": views.cuts,
        "frames": [{"frame": i, "main": bool(views.main[i]),
                    **({"homography": r.homography.round(8).tolist(), "score": round(r.score, 3), "how": r.how}
                       if r is not None else {})}
                   for i, r in enumerate(results)],
    }))
    print(f"\n-> {out / 'calibration.json'}")
    if args.overlay:
        overlay(source, out, views, results, labels, size, fps)
        print(f"-> {out / 'overlay.mp4'} and {out / 'check'}/")


if __name__ == "__main__":
    main()

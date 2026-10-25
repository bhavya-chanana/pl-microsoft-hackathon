"""Find people in the clip and follow them: RT-DETR on tiles of the pitch band.

A 4096-pixel panorama shrunk to the detector's 640 pixels would leave players a
few pixels tall, so the band of the frame the pitch occupies is cut into
overlapping square tiles, detected in one batch, and merged with NMS. People
whose feet are not on the pitch (stands, benches) are dropped before tracking.

Weights: ``PekingU/rtdetr_r50vd`` (Apache-2.0), loaded from safetensors only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import torch
from torchvision.ops import nms

from .tracker import LOW, Tracker
from .tracks import Tracks, from_rows

MODEL = "PekingU/rtdetr_r50vd"
NMS_IOU = 0.5


@dataclass(frozen=True)
class Tiling:
    top: int  # the pitch band, in frame rows
    bottom: int
    width: int  # frame width
    tile: int = 630
    overlap: int = 100

    def offsets(self) -> list[int]:
        step = self.tile - self.overlap
        return sorted({*range(0, self.width - self.tile, step), self.width - self.tile})


def _device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


class Detector:
    def __init__(self, tiling: Tiling, *, model: str = MODEL, size: int = 640, device: str | None = None) -> None:
        from transformers import RTDetrForObjectDetection, RTDetrImageProcessor

        self.tiling, self.size, self.device = tiling, size, device or _device()
        self._processor = RTDetrImageProcessor.from_pretrained(model)
        self._model = RTDetrForObjectDetection.from_pretrained(model, use_safetensors=True).to(self.device).eval()
        labels = {v: k for k, v in self._model.config.id2label.items()}
        self._person, self._ball = labels["person"], labels["sports ball"]

    @torch.no_grad()
    def __call__(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """People and balls in one BGR frame, each (N, 5) xyxy + score in frame pixels."""
        t = self.tiling
        band = cv2.cvtColor(image[t.top:t.bottom], cv2.COLOR_BGR2RGB)
        offsets = t.offsets()
        crops = [band[:, x:x + t.tile] for x in offsets]
        inputs = self._processor(images=crops, return_tensors="pt", size={"height": self.size, "width": self.size})
        outputs = self._model(**inputs.to(self.device))
        results = self._processor.post_process_object_detection(
            outputs, target_sizes=[c.shape[:2] for c in crops], threshold=LOW)
        found: dict[int, list[np.ndarray]] = {self._person: [], self._ball: []}
        for x, r in zip(offsets, results, strict=True):
            boxes = r["boxes"].cpu().numpy() + np.array([x, t.top, x, t.top])
            for box, label, score in zip(boxes, r["labels"].cpu().numpy(), r["scores"].cpu().numpy(), strict=True):
                if int(label) in found:
                    found[int(label)].append(np.r_[box, score])
        return self._merge(found[self._person]), self._merge(found[self._ball])

    @staticmethod
    def _merge(rows: list[np.ndarray]) -> np.ndarray:
        if not rows:
            return np.zeros((0, 5))
        boxes = np.array(rows)
        keep = nms(torch.from_numpy(boxes[:, :4]).float(), torch.from_numpy(boxes[:, 4]).float(), NMS_IOU).numpy()
        return boxes[keep]


def track_clip(video: Path, detector: Detector, frames: range,
               on_pitch: Callable[[np.ndarray], np.ndarray] | None = None,
               progress: Callable[[int], None] | None = None) -> tuple[Tracks, dict[int, np.ndarray]]:
    """Detect and track people over ``frames`` (a range with any step; skipped frames stay
    unseen for ``fill_gaps``). ``on_pitch`` maps feet pixels (N, 2) to a keep mask.
    Returns the tracks over the whole clip up to ``frames.stop`` and the ball candidates per frame."""
    tracker = Tracker()
    rows: list[list[float]] = []
    balls: dict[int, np.ndarray] = {}
    capture = cv2.VideoCapture(str(video))
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frames.start)
        current = frames.start
        for f in frames:
            while current < f:  # skipping by grab is faster than seeking
                capture.grab()
                current += 1
            ok, image = capture.read()
            current += 1
            if not ok:
                break
            people, balls[f] = detector(image)
            if on_pitch is not None and len(people):
                feet = np.c_[(people[:, 0] + people[:, 2]) / 2, people[:, 3]]
                people = people[on_pitch(feet)]
            for track_id, (x0, y0, x1, y1) in tracker.update(f, people):
                rows.append([f, track_id, x0, y0, x1 - x0, y1 - y0])
            if progress is not None:
                progress(f)
    finally:
        capture.release()
    if not rows:
        raise ValueError("no people found in the clip")
    return from_rows(np.array(rows), fps, frames=frames.stop), balls

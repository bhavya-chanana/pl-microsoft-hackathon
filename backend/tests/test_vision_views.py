"""Overlay detection: a player's scrub bar or a banner drawn over the bottom edge is not a main-camera frame."""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("scipy")

from matchlens.vision.views import classify, has_overlay, look  # noqa: E402


def pitch(w: int = 960, h: int = 544, seed: int = 0) -> np.ndarray:
    """A fictional wide shot: striped noisy grass with a few white lines and no graphics."""
    rng = np.random.default_rng(seed)
    stripe = (np.arange(w) // 80) % 2 == 0
    img = np.where(stripe[None, :, None], [52, 140, 58], [44, 122, 50]).astype(np.float32).repeat(h, 0)
    img += rng.normal(0, 6, (h, w, 1))
    img = np.clip(img, 0, 255).astype(np.uint8)
    cv2.line(img, (0, 140), (w, 200), (235, 235, 235), 3, cv2.LINE_AA)
    cv2.line(img, (0, h - 60), (w, h - 90), (235, 235, 235), 3, cv2.LINE_AA)  # a touchline just above the bottom edge
    return img


def with_banner(img: np.ndarray, text: bool = True) -> np.ndarray:
    """A dark strip along the bottom edge carrying icons and text, as a player or a lower-third draws one."""
    out = img.copy()
    h, w = out.shape[:2]
    y0 = int(h * 0.93)
    out[y0:] = (28, 24, 22)
    if text:
        for i, x in enumerate(range(20, w - 60, 90)):
            cv2.putText(out, "ABCDEF"[i % 6] + "ok", (x, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (235, 235, 235), 2, cv2.LINE_AA)
            cv2.rectangle(out, (x + 55, h - 22), (x + 70, h - 10), (200, 200, 200), -1)
    return out


def test_plain_pitch_has_no_overlay():
    assert not has_overlay(pitch())
    assert look(pitch()).main


def test_banner_over_the_bottom_edge_is_an_overlay():
    frame = with_banner(pitch())
    assert has_overlay(frame)
    assert not look(frame).main


def test_plain_dark_strip_is_not_an_overlay():
    # a dark but empty band (a shadow, a hoarding in shade) carries no text and no icons
    assert not has_overlay(with_banner(pitch(), text=False))


def test_overlay_is_independent_of_frame_size():
    frame = with_banner(pitch())
    small = cv2.resize(frame, (640, 362), interpolation=cv2.INTER_AREA)
    assert has_overlay(small)
    assert not has_overlay(cv2.resize(pitch(), (640, 362), interpolation=cv2.INTER_AREA))


def test_overlay_frames_are_vetoed_inside_a_main_shot():
    frames = [pitch(seed=i) for i in range(20)]
    for i in range(8, 13):
        frames[i] = with_banner(frames[i])
    views = classify(frames)
    assert views.cuts == []
    assert views.main.tolist() == [True] * 8 + [False] * 5 + [True] * 7

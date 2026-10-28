"""Live video input: ffmpeg frame source, statistics, and the per-frame chain on synthetic frames (never real
footage). The chain tests skip without ``uv sync --group vision``; the ffmpeg ones skip without ffmpeg."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass

import numpy as np
import pytest

from matchlens.next10.frames import (FrameStat, RollingStats, centre_origin, entity, frame_doc, is_ready)
from matchlens.next10.media import FrameReader, MediaError, build_command, classify, source_size
from matchlens.vision.jobs import fingerprint, match_id_for

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def test_frame_doc_schema():
    cx, cy = centre_origin(52.5, 34.0)
    assert (cx, cy) == (0.0, 0.0)
    doc = frame_doc("fotmob:1", 12.3456, [entity("p3", "home", -10.123, 4.5, conf=0.87654), entity("ball", "ball", 0, 0)],
                    True, wall_utc="2026-10-27T19:00:00Z", video_id="rec")
    assert doc["id"] == "fotmob:1:12346" and doc["t_s"] == 12.346 and doc["match_clock_s"] is None
    assert doc["calibrated"] is True and doc["match_id"] == "fotmob:1"
    assert doc["entities"][0] == {"id": "p3", "team": "home", "x": -10.12, "y": 4.5, "observed": True, "conf": 0.877}


def test_ready_needs_both_rates():
    assert is_ready(0.5, 0.8) and not is_ready(0.49, 1.0) and not is_ready(1.0, 0.79)
    assert not is_ready(None, None) and not is_ready(1.0, None)


def test_rolling_stats_window_and_totals():
    stats = RollingStats(every_s=0.0)
    for calibrated in (True, True, False, True):
        stats.record(FrameStat(main=True, calibrated=calibrated, people=10 if calibrated else 0,
                               on_pitch=9 if calibrated else 0), 0.1)
    stats.record(FrameStat(main=False), 0.1)
    assert stats.due()
    snap = stats.snapshot(5.0)
    assert snap["frames"] == 5 and snap["main_share"] == 0.8 and snap["calibrated_share"] == 0.75
    assert snap["on_pitch_rate"] == 0.9 and snap["live_ready"] is True and snap["fps"] == pytest.approx(10.0)
    assert stats.window.frames == 0 and snap["total"]["frames"] == 5  # the next window starts empty
    stats.record(FrameStat(main=True, calibrated=False), 0.1)
    assert stats.snapshot(6.0)["live_ready"] is False


def test_classify_sources(tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    assert classify(str(clip)).kind == "file"
    assert classify("srt://host:9000?mode=caller").kind == "url"
    assert classify("0").kind == "device" and classify("1:none").kind == "device"
    with pytest.raises(MediaError):
        classify("/no/such/file.mp4")


@needs_ffmpeg
def test_command_records_devices_and_urls_not_files(tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    rec = tmp_path / "r.mp4"
    file_cmd = build_command(classify(str(clip)), fps=5, size=(1920, 1080), record=None)
    assert "pipe:1" in file_cmd and "fps=5" in " ".join(file_cmd) and str(rec) not in file_cmd
    url_cmd = build_command(classify("srt://h:1"), fps=5, size=(1920, 1080), record=rec)
    assert url_cmd[-1] == str(rec) and "copy" in url_cmd and "-an" in url_cmd


def test_job_fingerprint_and_match_id(tmp_path):
    a, b = tmp_path / "fotmob-4803315_final.mov", tmp_path / "other.mov"
    a.write_bytes(b"abc" * 1000)
    b.write_bytes(b"abc" * 1000)
    assert fingerprint(a) == fingerprint(b)  # same bytes, different names
    b.write_bytes(b"abd" * 1000)
    assert fingerprint(a) != fingerprint(b)
    assert match_id_for(a, fingerprint(a)) == "fotmob:4803315"
    assert match_id_for(b, "f" * 64).startswith("video:ffff")
    b.with_name("other.mov.json").write_text('{"match_id": "fotmob:7"}')
    assert match_id_for(b, "f" * 64) == "fotmob:7"
    assert match_id_for(b, "f" * 64, given="x:1") == "x:1"


@pytest.fixture(scope="module")
def testsrc_clip(tmp_path_factory):
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    path = tmp_path_factory.mktemp("clip") / "testsrc.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-t", "4",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@needs_ffmpeg
def test_frame_reader_gives_frames_at_the_requested_rate(testsrc_clip):
    src = classify(str(testsrc_clip))
    size = source_size(src, 640)
    assert size == (640, 360)
    reader = FrameReader(build_command(src, fps=5, size=size), size, live=False).start()
    frames = list(reader)
    reader.close()
    assert [i for i, _ in frames] == list(range(len(frames))) and 19 <= len(frames) <= 21
    assert frames[0][1].shape == (360, 640, 3) and reader.returncode == 0


@needs_ffmpeg
def test_live_run_end_to_end_without_a_store(testsrc_clip):
    pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    from matchlens.vision.live import LiveRun

    run = LiveRun(source=str(testsrc_clip), match_id="test:lab", persist=False, quiet=True, stats_every_s=0.0,
                  width=640, record=False)
    summary = asyncio.run(run.run())
    # a test pattern is not a pitch: the gate keeps nothing, so nothing is calibrated and the run is not live-ready
    assert 19 <= summary["frames"] <= 21
    assert summary["calibrated_share"] in (None, 0.0)
    assert summary["live_ready"] is False and summary["dropped_frames"] == 0
    for key in ("main_share", "calibrated_share", "on_pitch_rate", "fps", "teams", "frames_written"):
        assert key in summary
    assert run.history, "statistics were reported while the run was going"
    for key in ("window_s", "main_share", "calibrated_share", "on_pitch_rate", "fps", "live_ready", "total"):
        assert key in run.history[0]


# ----------------------------------------------------------------------------- the chain on a synthetic pitch


@dataclass
class _Tiling:
    top: int
    bottom: int
    width: int
    tile: int = 630
    overlap: int = 100


class _Detector:
    """Reports a box on every given pitch point (feet at the point), as the real detector would."""

    def __init__(self, h: np.ndarray, points: np.ndarray) -> None:
        self.tiling = _Tiling(0, 1, 1)
        px = np.c_[points, np.ones(len(points))] @ h.T
        self.feet = px[:, :2] / px[:, 2:]
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        f = self.feet
        people = np.c_[f[:, 0] - 9, f[:, 1] - 36, f[:, 0] + 9, f[:, 1], np.full(len(f), 0.9)]
        return people, np.zeros((0, 5))


class _FixedCamera:
    def __init__(self, h: np.ndarray) -> None:
        self.h = h

    def step(self, bgr960, *, t_s, main, cut):
        return self.h if main else None


def test_chain_emits_team_labelled_frames_in_centre_origin_metres():
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    from matchlens.next10.chain import FrameChain
    from tests.test_broadcast import camera

    h = camera(pan=0.0, tilt=22.0, focal=700.0)
    rng = np.random.default_rng(1)
    left = np.c_[rng.uniform(15, 48, 9), rng.uniform(12, 56, 9)]
    right = np.c_[rng.uniform(57, 90, 9), rng.uniform(12, 56, 9)]
    points = np.r_[left, right]
    det = _Detector(h, points)
    image = np.zeros((540, 960, 3), np.uint8)
    image[:] = (50, 140, 58)  # grass
    for i, (fx, fy) in enumerate(det.feet):
        kit = (30, 30, 200) if i < len(left) else (200, 80, 30)  # red and blue shirts
        x0, y0, x1, y1 = int(fx - 9), int(fy - 36), int(fx + 9), int(fy)
        image[y0:y1, x0:x1] = kit
    chain = FrameChain("test:lab", _FixedCamera(h), detector=lambda: det, fps=5.0, video_id="synthetic")
    docs = []
    for i in range(12):
        docs += [r.doc for r in chain.process(image, i / 5) if r.doc is not None]
    docs += [r.doc for r in chain.flush() if r.doc is not None]
    assert len(docs) == 12 and all(d["calibrated"] for d in docs)
    last = docs[-1]
    teams = [e["team"] for e in last["entities"]]
    assert teams.count("home") == 9 and teams.count("away") == 9  # left side is home
    homes = [e for e in last["entities"] if e["team"] == "home"]
    assert all(e["x"] < 0 for e in homes) and all(e["observed"] for e in last["entities"])
    # positions come back in centre-origin metres
    got = sorted((e["x"], e["y"]) for e in last["entities"])
    want = sorted(centre_origin(x, y) for x, y in points)
    assert np.allclose(got, want, atol=0.5)
    stats = chain.stats.total.report()
    assert stats["calibrated_share"] == 1.0 and stats["on_pitch_rate"] == 1.0
    assert is_ready(stats["calibrated_share"], stats["on_pitch_rate"])
    assert chain.teams.kits is not None and cv2 is not None

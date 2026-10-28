"""Live video input for the next-10-seconds companion.

    uv run --group vision python -m matchlens.vision.live --source <device|url|file> --match-id <id> \\
        [--fps 5] [--venue <key>] [--max-seconds N]

``--source`` is whatever the owner points at the match: a macOS capture device index (``0``, or ``1:none``; OBS
virtual camera and capture cards show up here; ``ffmpeg -f avfoundation -list_devices true -i ""`` lists them), a
network URL (``srt://``, ``rtsp://``, ``http://``) or a video file. It never captures protected streams itself:
if a screen capture comes out black, point a camera at the screen instead.

ffmpeg reads the source at 5 fps into the chain in ``next10/chain.py`` (main-camera gate, calibration, detection,
tracking in pitch coordinates, team split). Each frame becomes a ``frames`` document in ``matchlens_next10`` and
goes out on the live hub. A device or URL is also kept as ``data/local/video/recordings/<timestamp>.mp4`` so the
match can be reprocessed offline (``vision/jobs.py``).

Every 10 s it prints calibrated share, on-pitch projection rate and fps. ``live_ready`` is true only when the
calibrated share of main-camera frames is at least 0.5 and at least 0.8 of detected people land on the pitch.
The run starts a calibration from keyframes (``labels.json`` of a known clip, found automatically for files) or
from a stadium prior (``--venue``); with neither it records and reports ``live_ready: false``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from matchlens.next10.frames import RollingStats, utc_now
from matchlens.next10.media import FrameReader, MediaError, build_command, classify, source_size
from matchlens.next10.sink import FrameSink
from matchlens.store.next10 import Next10Settings, Next10Store

log = logging.getLogger("matchlens.vision.live")

DATA = Path(__file__).resolve().parents[3] / "data" / "local" / "video"
RECORDINGS = DATA / "recordings"
STATS_EVERY_S = 10.0


def redact(source: str) -> str:
    """A source for the job document without credentials or tokens in a URL."""
    if "://" not in source:
        return source
    u = urlsplit(source)
    return f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}{u.path}"


def _detector(width: int):
    from matchlens.vision.detect import Detector, Tiling

    return lambda: Detector(Tiling(top=0, bottom=min(width, 540), width=width))


class LiveRun:
    """One live (or file-as-live) run. ``run()`` returns the final statistics."""

    def __init__(self, *, source: str, match_id: str, fps: float = 5.0, venue: str | None = None,
                 max_seconds: float | None = None, labels: Path | None = None, record: bool = True,
                 persist: bool = True, swap_teams: bool = False, device_fps: int = 30, width: int = 1920,
                 stats_every_s: float = STATS_EVERY_S, detector: Any = None, quiet: bool = False) -> None:
        self.source, self.match_id, self.fps, self.venue = source, match_id, fps, venue
        self.max_seconds, self.labels, self.record, self.persist = max_seconds, labels, record, persist
        self.swap_teams, self.device_fps, self.width, self.stats_every_s = swap_teams, device_fps, width, stats_every_s
        self.detector, self.quiet = detector, quiet
        self.stop = threading.Event()
        self.history: list[dict[str, Any]] = []
        self.recording: Path | None = None

    def _calibrator(self, original: Path | None, video: Path | None):
        """``original`` names the clip's work folder (labels); frames are read from ``video``, its SDR copy if HDR."""
        from matchlens.next10.chain import TrackerCalibrator, anchors_from_labels
        from matchlens.vision.footage import workdir

        anchors = []
        labels = self.labels or (workdir(original) / "labels.json" if original is not None else None)
        if labels is not None and labels.exists() and video is not None:
            anchors = anchors_from_labels(labels, video)
            log.info("calibration starts from %d labelled frames of %s", len(anchors), labels)
        return TrackerCalibrator(anchors, venue=self.venue)

    async def run(self) -> dict[str, Any]:
        from matchlens.next10.chain import FrameChain
        from matchlens.vision.footage import prepare

        src = classify(self.source)
        original = Path(src.spec) if src.kind == "file" else None
        video = prepare(original) if original is not None else None  # an HDR file is read from its SDR copy
        read = classify(str(video)) if video is not None else src
        size = source_size(read, self.width)
        if src.kind != "file" and self.record:
            RECORDINGS.mkdir(parents=True, exist_ok=True)
            self.recording = RECORDINGS / f"{datetime.now(UTC):%Y%m%dT%H%M%S}.mp4"
        chain = FrameChain(self.match_id, self._calibrator(original, video), detector=self.detector or _detector(size[0]),
                           fps=self.fps, video_id=self.recording.stem if self.recording else
                           (original.stem if original else None), swap_teams=self.swap_teams)
        chain.stats = RollingStats(every_s=self.stats_every_s)
        store = None
        if self.persist:
            try:
                store = await Next10Store(Next10Settings.from_env()).open()
            except Exception as exc:
                log.warning("next10 store unavailable (%s): frames go to the live hub only", exc)
        sink = FrameSink(self.match_id, store).start()
        job_id = f"live:{self.match_id}:{datetime.now(UTC):%Y%m%dT%H%M%S}"
        job: dict[str, Any] = {"id": job_id, "job_id": job_id, "kind": "live", "match_id": self.match_id,
                               "path": redact(self.source), "status": "running", "fps": self.fps,
                               "recording": str(self.recording) if self.recording else None,
                               "started_at": utc_now(), "venue": self.venue, "chunk_s": 0, "chunks_done": 0,
                               "calibration": {}}
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop.set)

        cmd = build_command(read, fps=self.fps, size=size, record=self.recording, duration_s=self.max_seconds,
                            device_fps=self.device_fps)
        reader = FrameReader(cmd, size, live=src.kind != "file").start()

        def work() -> None:
            try:
                for index, frame in reader:
                    if self.stop.is_set():
                        break
                    t_s = index / self.fps
                    for result in chain.process(frame, t_s):
                        if result.doc is not None:
                            loop.call_soon_threadsafe(queue.put_nowait, ("frame", result.doc))
                    if chain.stats.due():
                        snap = chain.stats.snapshot(t_s)
                        snap["dropped_frames"] = reader.dropped
                        loop.call_soon_threadsafe(queue.put_nowait, ("stats", snap))
                for result in chain.flush():
                    if result.doc is not None:
                        loop.call_soon_threadsafe(queue.put_nowait, ("frame", result.doc))
            except Exception as exc:
                log.exception("live chain failed")
                loop.call_soon_threadsafe(queue.put_nowait, ("error", {"error": str(exc)}))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        thread = threading.Thread(target=work, name="live-chain", daemon=True)
        thread.start()
        error = None
        try:
            while (item := await queue.get()) is not None:
                kind, doc = item
                if kind == "frame":
                    sink.add(doc)
                elif kind == "stats":
                    doc = {**doc, "match_id": self.match_id, "job_id": job_id, "teams": chain.teams.kits}
                    self.history.append(doc)
                    sink.add(doc, "stats")
                    job.update(stats=doc, updated_at=utc_now(), live_ready=doc["live_ready"])
                    if store is not None:
                        await _put_job(store, job)
                    if not self.quiet:
                        print(json.dumps({k: v for k, v in doc.items() if k != "total"}), flush=True)
                else:
                    error = doc["error"]
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
            self.stop.set()
            await asyncio.to_thread(reader.close)
            thread.join(10)
            await sink.close()
            final = chain.stats.snapshot(0.0)
            summary = {**final["total"], "live_ready": final["total_live_ready"], "dropped_frames": reader.dropped,
                       "frames_written": sink.written, "teams": chain.teams.kits,
                       "recording": str(self.recording) if self.recording else None}
            ffmpeg_failed = reader.returncode not in (0, None, -15) and not self.stop.is_set()
            job.update(status="failed" if error or ffmpeg_failed else "done", updated_at=utc_now(), summary=summary,
                       error=error or (reader.stderr if ffmpeg_failed else None))
            if store is not None:
                await _put_job(store, job)
                await store.close()
        if not self.quiet:
            print("final " + json.dumps(summary), flush=True)
        if job["status"] == "failed":
            raise MediaError(job.get("error") or "ffmpeg failed")
        return summary


async def _put_job(store: Next10Store, job: dict[str, Any]) -> None:
    try:
        await store.upsert("video_jobs", [job])
    except Exception as exc:
        log.warning("could not update the job document: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="capture device index, URL (srt://, rtsp://, ...) or file")
    parser.add_argument("--match-id", required=True, help="e.g. fotmob:4803315")
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--venue", help="stadium prior key (vision/venues.json) to start the calibration from")
    parser.add_argument("--max-seconds", type=float, help="stop after this much video")
    parser.add_argument("--labels", type=Path, help="labels.json to start the calibration from (default: the clip's)")
    parser.add_argument("--no-record", action="store_true", help="do not keep the input as a recording")
    parser.add_argument("--no-store", action="store_true", help="do not write to the next10 database")
    parser.add_argument("--swap-teams", action="store_true", help="the team on the right of the pitch is home")
    parser.add_argument("--device-fps", type=int, default=30, help="frame rate to request from a capture device")
    parser.add_argument("--stats-every", type=float, default=STATS_EVERY_S, help="seconds between statistics")
    parser.add_argument("--width", type=int, default=1920, help="width frames are decoded at")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("azure.core.pipeline.policies.http_logging_policy", "azure.cosmos", "azure.identity"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    run = LiveRun(source=args.source, match_id=args.match_id, fps=args.fps, venue=args.venue,
                  max_seconds=args.max_seconds, labels=args.labels, record=not args.no_record,
                  persist=not args.no_store, swap_teams=args.swap_teams, device_fps=args.device_fps, width=args.width,
                  stats_every_s=args.stats_every)
    try:
        asyncio.run(run.run())
    except MediaError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()

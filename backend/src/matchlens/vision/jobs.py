"""Video jobs: recordings dropped in a watched folder become 5 Hz tracking frames in the next10 database.

    uv run --group vision python -m matchlens.vision.jobs watch                 # the inbox, until stopped
    uv run --group vision python -m matchlens.vision.jobs run <file> [--match-id fotmob:123]

Inbox: ``backend/data/local/video/inbox/`` (gitignored). A file is processed once it has stopped growing. Each one
gets a fingerprint (size plus its first and last megabytes), a ``video_jobs`` document and is processed in 30 s
chunks through the chain in ``next10/chain.py`` (the same as the live input in ``vision/live.py``). The document
holds ``chunks_done``, so a stopped run resumes where it left off, and a finished file is skipped.

The match is named, in order, by ``--match-id``, a sidecar ``<file>.json`` with ``{"match_id": ...}``, a file name
containing ``fotmob-<id>`` (or ``fotmob_<id>``), or ``video:<fingerprint>``.

Calibration: ``--calibration auto|bundle`` (the default) solves the whole video once with
``matchlens.vision.bundle.solve_video`` (not once per chunk) and uses that for every frame; a frame the solution
left out ("none") falls back to the per-frame tracker in ``vision/camera.py``, started from the clip's
``labels.json`` keyframes or a ``--venue`` prior. ``--calibration frame`` uses the tracker alone. The solution is
kept in ``data/local/video/jobs/<job_id>/calibration.json`` so a resumed job does not solve again. Frame docs say
which one they got in ``calib_status`` (fitted | interpolated | none | frame).

Match clock: ``--anchors anchors.json`` (or ``<clip workdir>/anchors.json``) pairs video seconds with the broadcast
clock, ``[{"video_s": 14.0, "clock": "00:03", "period": 1}, ...]`` (or ``match_s``). ``vision.align`` turns them into
a ClockMap (kept next to the calibration as ``clockmap.json``) which fills ``match_clock_s`` (null in the half-time
break) and turns the second half round so home keeps attacking +x. Anchors that arrive after the run are applied
with ``python -m matchlens.next10.clock <match_id> --anchors anchors.json`` (no re-detection).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from matchlens.next10.clock import build_clock, clock_function, finite, load_anchors
from matchlens.next10.frames import Counts, utc_now
from matchlens.next10.media import FrameReader, MediaError, build_command, classify, source_size
from matchlens.store.next10 import Next10Settings, Next10Store
from matchlens.vision.footage import VIDEO, prepare, workdir

log = logging.getLogger("matchlens.vision.jobs")

DATA = Path(__file__).resolve().parents[3] / "data" / "local" / "video"
INBOX = DATA / "inbox"
JOBS = DATA / "jobs"
CHUNK_S = 30
FINGERPRINT_BYTES = 1 << 20
POLL_S = 5.0


def fingerprint(path: Path) -> str:
    """sha256 of the size and the first and last megabyte: cheap on a multi-gigabyte file, stable across renames."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(FINGERPRINT_BYTES))
        if size > 2 * FINGERPRINT_BYTES:
            f.seek(size - FINGERPRINT_BYTES)
            h.update(f.read(FINGERPRINT_BYTES))
    return h.hexdigest()


def match_id_for(path: Path, fp: str, given: str | None = None) -> str:
    if given:
        return given
    sidecar = path.with_name(path.name + ".json")
    if sidecar.exists():
        try:
            if mid := json.loads(sidecar.read_text()).get("match_id"):
                return str(mid)
        except (OSError, ValueError):
            log.warning("unreadable sidecar %s", sidecar)
    if m := re.search(r"fotmob[-_:](\d+)", path.name, re.IGNORECASE):
        return f"fotmob:{m.group(1)}"
    return f"video:{fp[:12]}"


def duration_s(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=False)
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (KeyError, ValueError):
        raise MediaError(f"cannot read the duration of {path}") from None


def whole_match_solver():
    """``bundle.solve_video`` when that module exists, else None (per-frame calibration then)."""
    try:
        from matchlens.vision import bundle
    except ImportError:
        return None
    return getattr(bundle, "solve_video", None)


def job_dir(job_id: str) -> Path:
    return JOBS / job_id


def make_calibrator(original: Path, video: Path, *, mode: str, fps: float, venue: str | None,
                    labels: Path | None, cache: Path | None = None) -> tuple[Any, dict[str, Any]]:
    """The calibrator for a file and what to record about it in the job document.

    Bundle modes solve the whole video once (``cache`` is where the solution is kept, and read back on a resume);
    frames the solution has nothing for are calibrated one by one."""
    from matchlens.next10.chain import BundleCalibrator, TrackerCalibrator, anchors_from_labels

    labels = labels or workdir(original) / "labels.json"
    anchors = anchors_from_labels(labels, video) if labels.exists() else []
    solver = whole_match_solver() if mode in ("auto", "bundle") else None
    if mode == "bundle" and solver is None:
        raise MediaError("--calibration bundle: matchlens.vision.bundle.solve_video is not available")
    if solver is None:
        return TrackerCalibrator(anchors, venue=venue), {"mode": "frame", "venue": venue, "anchors": len(anchors)}
    solution, reused, took = None, False, 0.0
    if cache is not None and cache.exists():
        try:
            from matchlens.vision.bundle import MatchCalibration

            solution = MatchCalibration.from_dict(json.loads(cache.read_text()))
            reused = True
        except (OSError, ValueError, KeyError, ImportError):
            log.warning("unreadable calibration %s; solving again", cache)
    if solution is None:
        t0 = time.monotonic()
        solution = solver(video, fps=fps, anchors=anchors or None, venue=venue)
        took = time.monotonic() - t0
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps(solution.to_dict()))
            tmp.replace(cache)
    summary = solution.summary() if hasattr(solution, "summary") else {}
    fallback = TrackerCalibrator(anchors, venue=venue)
    return BundleCalibrator(solution, fallback), {
        "mode": "bundle", "venue": venue, "anchors": len(anchors), "bundle": summary, "reused": reused,
        "solve_s": round(took, 1), "file": None if cache is None else str(cache)}


class VideoJob:
    def __init__(self, path: Path, *, store: Next10Store | None, match_id: str | None = None, fps: float = 5.0,
                 chunk_s: int = CHUNK_S, calibration: str = "auto", venue: str | None = None,
                 labels: Path | None = None, swap_teams: bool = False, width: int = 1920, detector: Any = None,
                 home_kit: str | None = None, force: bool = False, anchors: Path | None = None, jobs_dir: Path | None = None) -> None:
        self.path, self.store, self.fps, self.chunk_s = path.resolve(), store, fps, chunk_s
        self.calibration, self.venue, self.labels, self.swap_teams = calibration, venue, labels, swap_teams
        self.home_kit = home_kit
        self.width, self.detector, self.force = width, detector, force
        self.given_match_id = match_id
        self.anchors_path, self.jobs_dir = anchors, jobs_dir or JOBS

    async def _load(self, job_id: str) -> dict[str, Any] | None:
        return None if self.store is None else await self.store.read("video_jobs", job_id, job_id)

    async def _save(self, job: dict[str, Any]) -> None:
        job["updated_at"] = utc_now()
        if self.store is not None:
            await self.store.upsert("video_jobs", [job])

    async def run(self) -> dict[str, Any]:
        """Process the file to the end (or from where the stored job stopped). Returns the job document."""
        from matchlens.next10.chain import FrameChain

        fp = await asyncio.to_thread(fingerprint, self.path)
        job_id = f"vid-{fp[:16]}"
        job = await self._load(job_id)
        if job and job.get("status") == "done" and not self.force:
            log.info("%s: already done (%s)", self.path.name, job_id)
            return job
        match_id = match_id_for(self.path, fp, self.given_match_id)
        video = await asyncio.to_thread(prepare, self.path)  # an HDR recording is read from its SDR copy
        total_s = await asyncio.to_thread(duration_s, video)
        chunks = max(1, -(-int(total_s) // self.chunk_s))
        done = 0 if job is None or self.force or job.get("chunk_s") != self.chunk_s else int(job.get("chunks_done", 0))
        prior = (job or {}).get("stats_total", {}) if done else {}
        job = {**(job or {}), "id": job_id, "job_id": job_id, "kind": "file", "path": self.path.name,
               "fingerprint": fp, "match_id": match_id, "status": "running", "chunk_s": self.chunk_s,
               "chunks_done": done, "total_chunks": chunks, "swap_teams": self.swap_teams, "home_kit": self.home_kit, "duration_s": round(total_s, 1), "fps": self.fps,
               "started_at": (job or {}).get("started_at") or utc_now(), "error": None}
        await self._save(job)
        log.info("%s -> %s: %d chunks of %ds, resuming at chunk %d", self.path.name, match_id, chunks, self.chunk_s, done)

        out = self.jobs_dir / job_id
        calibrator, cal_info = await asyncio.to_thread(make_calibrator, self.path, video, mode=self.calibration,
                                                       fps=self.fps, venue=self.venue, labels=self.labels,
                                                       cache=out / "calibration.json")
        job["calibration"] = {**job.get("calibration", {}), **cal_info}
        clock_map, clock_info = self._clock(out)
        job["clock"] = clock_info
        await self._save(job)
        centres = job["calibration"].get("team_centres")
        chain = FrameChain(match_id, calibrator, detector=self.detector or _detector(self.width), fps=self.fps,
                           video_id=self.path.stem, swap_teams=self.swap_teams, home_kit=self.home_kit, clock=clock_function(clock_map),
                           team_centres=None if centres is None else _array(centres))
        src = classify(str(video))
        size = source_size(src, self.width)
        totals = Counts(**{k: prior.get(k, 0) for k in ("frames", "main", "calibrated", "people", "on_pitch")},
                        seconds=prior.get("seconds", 0.0))
        started = time.monotonic()
        job["frames_written"] = int(job.get("frames_written", 0)) if done else 0
        try:
            for k in range(done, chunks):
                docs = await asyncio.to_thread(self._chunk, chain, src, size, k)
                if self.store is not None and docs:
                    await self.store.upsert("frames", docs)
                job["frames_written"] += len(docs)
                job["chunks_done"] = k + 1
                snap = chain.stats.snapshot(min((k + 1) * self.chunk_s, total_s))
                totals.frames, totals.main, totals.calibrated = (totals.frames + snap["frames"],
                                                                 totals.main + snap["main_frames"],
                                                                 totals.calibrated + snap["calibrated_frames"])
                totals.people, totals.on_pitch = totals.people + snap["people"], totals.on_pitch + snap["on_pitch"]
                totals.seconds += snap["window_s"]
                job["stats_total"] = {"frames": totals.frames, "main": totals.main, "calibrated": totals.calibrated,
                                      "people": totals.people, "on_pitch": totals.on_pitch, "seconds": totals.seconds}
                job["stats"] = totals.report()
                if chain.teams.centres is not None:
                    job["calibration"]["team_centres"] = chain.teams.centres.round(2).tolist()
                    job["calibration"]["kits"] = chain.teams.kits
                await self._save(job)
                log.info("chunk %d/%d: %d frames, calibrated %s, on-pitch %s", k + 1, chunks, snap["frames"],
                         snap["calibrated_share"], snap["on_pitch_rate"])
            job["status"] = "done"
        except Exception as exc:
            job.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            log.exception("job %s failed", job_id)
        job["elapsed_s"] = round(time.monotonic() - started, 1)
        await self._save(job)
        return job

    def _clock(self, out: Path) -> tuple[Any, dict[str, Any]]:
        """The ClockMap from the anchors file (saved as ``clockmap.json`` beside the calibration), if there is one."""
        path = self.anchors_path or workdir(self.path) / "anchors.json"
        if not path.exists():
            return None, {"anchors": 0, "map": None}
        anchors = load_anchors(path)
        clock_map = build_clock(anchors)
        out.mkdir(parents=True, exist_ok=True)
        (out / "anchors.json").write_text(path.read_text())
        if clock_map is not None:
            (out / "clockmap.json").write_text(json.dumps(clock_map.to_dict()))
        report = None if clock_map is None else finite({k: v for k, v in clock_map.report.to_dict().items() if k != "anchors"})
        return clock_map, {"anchors": len(anchors), "map": None if clock_map is None else str(out / "clockmap.json"),
                           "report": report}

    def _chunk(self, chain: Any, src: Any, size: tuple[int, int], k: int) -> list[dict[str, Any]]:
        start = k * self.chunk_s
        cmd = build_command(src, fps=self.fps, size=size, start_s=start, duration_s=self.chunk_s)
        reader = FrameReader(cmd, size, live=False).start()
        docs: list[dict[str, Any]] = []
        try:
            for index, frame in reader:
                docs += [r.doc for r in chain.process(frame, start + index / self.fps) if r.doc is not None]
        finally:
            reader.close()
        if reader.returncode not in (0, None):
            raise MediaError(f"ffmpeg failed on chunk {k}: {reader.stderr.strip()[-300:]}")
        docs += [r.doc for r in chain.flush() if r.doc is not None]
        return docs


def _array(values: Any):
    import numpy as np

    return np.asarray(values, np.float64)


def _detector(width: int):
    from matchlens.vision.detect import Detector, Tiling

    return lambda: Detector(Tiling(top=0, bottom=min(width, 540), width=width))


def stable(path: Path, seen: dict[Path, tuple[int, float]]) -> bool:
    """True when the file has not changed since the previous poll (a recording that is still being written grows)."""
    st = path.stat()
    key = (st.st_size, st.st_mtime)
    previous, seen[path] = seen.get(path), key
    return previous == key and st.st_size > 0


async def watch(inbox: Path, store: Next10Store | None, *, once: bool = False, **options: Any) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    log.info("watching %s", inbox)
    seen: dict[Path, tuple[int, float]] = {}
    handled: set[tuple[Path, int, float]] = set()
    while True:
        for path in sorted(inbox.iterdir()):
            if (path.suffix.lower() not in VIDEO or path.name.startswith(".") or not path.is_file()
                    or not stable(path, seen)):
                continue
            st = path.stat()
            if (path, st.st_size, st.st_mtime) in handled:
                continue
            job = await VideoJob(path, store=store, **options).run()
            handled.add((path, st.st_size, st.st_mtime))
            print(json.dumps({k: job.get(k) for k in ("job_id", "path", "match_id", "status", "chunks_done",
                                                      "total_chunks", "frames_written", "stats", "error")}), flush=True)
        if once:
            return
        await asyncio.sleep(POLL_S)


async def _main(args: argparse.Namespace) -> None:
    store = None
    if not args.no_store:
        store = await Next10Store(Next10Settings.from_env()).open()
    options = dict(fps=args.fps, chunk_s=args.chunk_s, calibration=args.calibration, venue=args.venue,
                   labels=args.labels, swap_teams=args.swap_teams, width=args.width, anchors=args.anchors, home_kit=args.home_kit)
    try:
        if args.cmd == "run":
            job = await VideoJob(args.file, store=store, match_id=args.match_id, force=args.force, **options).run()
            print(json.dumps({k: v for k, v in job.items() if k not in ("calibration",)}, indent=2))
            if job["status"] != "done":
                raise SystemExit(1)
        else:
            await watch(args.inbox, store, once=args.once, match_id=args.match_id, **options)
    finally:
        if store is not None:
            await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("watch", "process every video that lands in the inbox"), ("run", "process one file")):
        p = sub.add_parser(name, help=helptext)
        if name == "run":
            p.add_argument("file", type=Path)
            p.add_argument("--force", action="store_true", help="start over even if the job is done")
        else:
            p.add_argument("--inbox", type=Path, default=INBOX)
            p.add_argument("--once", action="store_true", help="process what is there now, then exit")
        p.add_argument("--match-id", help="e.g. fotmob:4803315 (default: sidecar json, file name, fingerprint)")
        p.add_argument("--fps", type=float, default=5.0)
        p.add_argument("--chunk-s", type=int, default=CHUNK_S)
        p.add_argument("--calibration", choices=["auto", "bundle", "frame"], default="auto")
        p.add_argument("--venue", help="stadium prior key (vision/venues.json)")
        p.add_argument("--labels", type=Path, help="labels.json to start the per-frame calibration from")
        p.add_argument("--anchors", type=Path, help="video-time / broadcast-clock pairs (default: <clip workdir>/anchors.json)")
        p.add_argument("--swap-teams", action="store_true",
                       help="home attacks towards -x in the first half (frames are turned so home attacks +x)")
        p.add_argument("--home-kit", help="home shirt colour, e.g. '#a03040': names the teams by kit, not by side")
        p.add_argument("--width", type=int, default=1920)
        p.add_argument("--no-store", action="store_true", help="do not write to the next10 database (no resume)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("azure.core.pipeline.policies.http_logging_policy", "azure.cosmos", "azure.identity", "httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass
    except MediaError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()

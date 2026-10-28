"""ffmpeg as a frame source: a capture device, a network URL or a file, down to raw BGR frames at a fixed rate.

It reads whatever the owner points it at (an OBS virtual camera or capture card by avfoundation index, an
``srt://`` / ``rtsp://`` / ``http`` URL, a file) and never tries to capture protected streams itself.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DEFAULT_SIZE = (1920, 1080)
DEVICE_FPS = 30


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Source:
    kind: str  # "device", "url" or "file"
    spec: str  # device index/name, URL, or path


def classify(source: str) -> Source:
    if "://" in source:
        return Source("url", source)
    if Path(source).expanduser().exists():
        return Source("file", str(Path(source).expanduser().resolve()))
    if source.startswith("/dev/") or re.fullmatch(r"\d+(:\w+)?|[\w .\-()]+", source):
        return Source("device", source)
    raise MediaError(f"{source!r} is not a file, URL or capture device")


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise MediaError("ffmpeg is not installed (brew install ffmpeg)")
    return path


def probe_size(path: str) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", path], capture_output=True, text=True, check=False)
    streams = json.loads(out.stdout or "{}").get("streams", [])
    if not streams:
        raise MediaError(f"no video stream in {path}")
    return int(streams[0]["width"]), int(streams[0]["height"])


def source_size(src: Source, width: int = DEFAULT_SIZE[0]) -> tuple[int, int]:
    """Output frame size: a file keeps its aspect ratio; a device or URL is letterboxed to 16:9."""
    if src.kind == "file":
        w, h = probe_size(src.spec)
        return width, max(2, round(width * h / w / 2) * 2)
    return width, round(width * 9 / 16 / 2) * 2


def input_args(src: Source, device_fps: int = DEVICE_FPS) -> list[str]:
    if src.kind == "device":
        if src.spec.startswith("/dev/"):
            return ["-f", "v4l2", "-framerate", str(device_fps), "-i", src.spec]
        if sys.platform != "darwin":
            raise MediaError("capture devices by index are supported on macOS (avfoundation); pass a URL or file")
        index = src.spec if ":" in src.spec else f"{src.spec}:none"
        return ["-f", "avfoundation", "-framerate", str(device_fps), "-capture_cursor", "0", "-i", index]
    if src.kind == "url" and src.spec.startswith("rtsp://"):
        return ["-rtsp_transport", "tcp", "-i", src.spec]
    return ["-i", src.spec]


def recording_args(src: Source, path: Path) -> list[str]:
    """Second output: the raw input kept as a (fragmented, so a killed run still plays) mp4."""
    if src.kind == "url":
        codec = ["-c:v", "copy"]  # already compressed: keep it as it came
    elif sys.platform == "darwin":
        codec = ["-c:v", "h264_videotoolbox", "-b:v", "8M", "-pix_fmt", "yuv420p"]
    else:
        codec = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p"]
    return ["-map", "0:v:0", *codec, "-an", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4", str(path)]


def build_command(src: Source, *, fps: float, size: tuple[int, int], record: Path | None = None,
                  start_s: float = 0.0, duration_s: float | None = None, device_fps: int = DEVICE_FPS) -> list[str]:
    w, h = size
    cmd = [require_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if src.kind == "file" and start_s > 0:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += input_args(src, device_fps)
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.3f}"]
    fit = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    cmd += ["-map", "0:v:0", "-vf", f"fps={fps:g},{fit}", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    if record is not None:
        cmd += recording_args(src, record)
    return cmd


class FrameReader:
    """Raw frames from an ffmpeg process, read on a thread so the pipe never stalls.

    ``live`` sources drop the oldest frame when the consumer is behind (latency stays bounded and ``dropped``
    counts them); files wait for the consumer. Frames carry their index at the output rate, so time is
    ``start_s + index / fps`` even after drops.
    """

    def __init__(self, cmd: list[str], size: tuple[int, int], *, live: bool, buffer: int = 10) -> None:
        self.cmd, self.size, self.live, self.buffer = cmd, size, live, buffer
        self.dropped = 0
        self.returncode: int | None = None
        self.stderr = ""
        self._frames: deque[tuple[int, np.ndarray]] = deque()
        self._cond = threading.Condition()
        self._eof = False
        self._closed = False
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> FrameReader:
        self._proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self._thread = threading.Thread(target=self._pump, name="ffmpeg-reader", daemon=True)
        self._thread.start()
        return self

    def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        errors: list[bytes] = []
        threading.Thread(target=lambda: errors.append(self._proc.stderr.read() if self._proc.stderr else b""),
                         daemon=True).start()
        w, h = self.size
        n = w * h * 3
        index = 0
        try:
            while not self._closed:
                raw = self._read_exact(n)
                if raw is None:
                    break
                frame = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
                with self._cond:
                    if self.live and len(self._frames) >= self.buffer:
                        self._frames.popleft()
                        self.dropped += 1
                    while not self.live and len(self._frames) >= self.buffer and not self._closed:
                        self._cond.wait(0.2)
                    self._frames.append((index, frame))
                    self._cond.notify_all()
                index += 1
        finally:
            self.returncode = self._proc.wait()
            self.stderr = (errors[0] if errors else b"").decode(errors="replace")[-2000:]
            with self._cond:
                self._eof = True
                self._cond.notify_all()

    def _read_exact(self, n: int) -> bytes | None:
        assert self._proc is not None and self._proc.stdout is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._proc.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def __iter__(self) -> Iterator[tuple[int, np.ndarray]]:
        while True:
            with self._cond:
                while not self._frames and not self._eof and not self._closed:
                    self._cond.wait(0.2)
                if self._frames:
                    item = self._frames.popleft()
                    self._cond.notify_all()
                else:
                    return
            yield item

    def close(self) -> None:
        self._closed = True
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._thread is not None:
            self._thread.join(5)
        with self._cond:
            self._cond.notify_all()

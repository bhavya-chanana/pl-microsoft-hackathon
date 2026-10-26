"""Hand labels for a broadcast clip: which frames are the main camera, and where its landmarks are.

    uv run --group vision python -m matchlens.vision.label [path/to/clip.mp4] --frames 20

(with no clip, the newest video in ``data/local/broadcast/clips/``) pulls evenly spaced frames out of the clip into ``data/local/broadcast/<clip>/frames/`` and serves a
page on http://127.0.0.1:8777 to label them: mark each frame main camera or not, pick a landmark on
the pitch diagram and click where it is in the frame. Labels save as you go to
``data/local/broadcast/<clip>/labels.json``, which ``python -m matchlens.vision.broadcast`` uses to
start the calibration and to measure it.

Frames are numbered in decoding order, as the calibration reads them, so they are pulled out by
reading the clip through rather than seeking (seeking can land a few frames off).
"""

from __future__ import annotations

import argparse
import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

from .markings import landmarks, polylines

DATA = Path(__file__).resolve().parents[3] / "data" / "local" / "broadcast"
CLIPS = DATA / "clips"
PAGE = Path(__file__).with_name("label.html")
VIDEO = {".mp4", ".mov", ".mkv", ".avi", ".ts", ".m4v", ".webm"}


def default_clip() -> Path:
    """The newest video in ``CLIPS``."""
    found = sorted((p for p in CLIPS.glob("*") if p.suffix.lower() in VIDEO), key=lambda p: p.stat().st_mtime)
    if not found:
        raise SystemExit(f"no clip given and no video in {CLIPS}")
    return found[-1]


def workdir(clip: Path) -> Path:
    return DATA / clip.stem


def pick_frames(total: int, count: int) -> list[int]:
    return sorted({round(f) for f in np.linspace(0, total - 1, count + 2)[1:-1]})


def extract(clip: Path, frames: list[int], out: Path) -> tuple[int, int]:
    """Save the chosen frames as JPEGs; returns the clip's frame size."""
    out.mkdir(parents=True, exist_ok=True)
    wanted = {f for f in frames if not (out / f"{f}.jpg").exists()}
    capture = cv2.VideoCapture(str(clip))
    size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    try:
        index = 0
        while wanted:
            ok, frame = capture.read()
            if not ok:
                break
            if index in wanted:
                cv2.imwrite(str(out / f"{index}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                wanted.discard(index)
            index += 1
    finally:
        capture.release()
    return size


def load_labels(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"frames": {}}


def serve(clip: Path, frames: list[int], size: tuple[int, int], port: int) -> None:
    root = workdir(clip)
    labels_path = root / "labels.json"
    state = {
        "clip": clip.name,
        "size": size,
        "frames": frames,
        "landmarks": landmarks(),
        "markings": [line.round(2).tolist() for line in polylines(0.5)],
    }

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, kind: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/":
                self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                body = {**state, "labels": load_labels(labels_path)}
                self._send(json.dumps(body).encode(), "application/json")
            elif m := re.fullmatch(r"/frames/(\d+)\.jpg", self.path):
                path = root / "frames" / f"{int(m.group(1))}.jpg"
                if path.exists():
                    self._send(path.read_bytes(), "image/jpeg")
                else:
                    self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
            else:
                self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path != "/api/labels":
                self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            body.update(clip=clip.name, size=size)
            tmp = labels_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(body, indent=1))
            tmp.replace(labels_path)
            self._send(b"{}", "application/json")

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"labelling {len(frames)} frames of {clip.name}: http://127.0.0.1:{port}  (Ctrl+C to stop)")
    print(f"labels save to {labels_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clip", type=Path, nargs="?", help=f"default: the newest video in {CLIPS}")
    parser.add_argument("--frames", type=int, default=20, help="how many evenly spaced frames to label")
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args()
    clip = (args.clip or default_clip()).resolve()
    capture = cv2.VideoCapture(str(clip))
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    if total <= 0:
        raise SystemExit(f"cannot read {clip}")
    labelled = [int(f) for f in load_labels(workdir(clip) / "labels.json")["frames"]]
    frames = sorted(set(pick_frames(total, args.frames)) | set(labelled))
    out = workdir(clip) / "frames"
    size = extract(clip, frames, out)
    # The container's frame count can overstate the frames that decode.
    serve(clip, [f for f in frames if (out / f"{f}.jpg").exists()], size, args.port)


if __name__ == "__main__":
    main()

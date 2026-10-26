"""Turn one SportsMOT football clip into a live-replayable Football Intelligence match.

SportsMOT (https://huggingface.co/datasets/MCG-NJU/SportsMOT, CC BY-NC 4.0) gives broadcast frames plus a box and
track id for every player. This script estimates where each player stands on the pitch and writes the result for
the backend's live runner, which posts it through the vest ingest API second by second.

Positions are estimates, not vest data: the foot point of each box is mapped to pitch metres with a homography
fitted to the halfway line and centre circle, after removing the camera's horizontal pan (median image motion of all
players between frames). Teams come from shirt colour; referees (yellow) are dropped.

    uv run --with numpy --with pillow python scripts/sportsmot_to_live.py backend/.data/sportsmot/clip/train/<clip>
"""

from __future__ import annotations

import configparser
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "backend" / ".data" / "sportsmot" / "live_match.json"
SAMPLE_EVERY = 5  # 25 fps → 5 Hz, a typical vest rate band

# Image points (u, v) → pitch metres (x along the length, y across), read off the frame at the clip's midpoint.
IMAGE_PTS = np.array([[720, 215], [720, 378], [720, 560], [300, 467], [1150, 467]], dtype=float)
PITCH_PTS = np.array([[52.5, 0.0], [52.5, 24.85], [52.5, 43.15], [43.35, 34.0], [61.65, 34.0]], dtype=float)


def homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    rows = []
    for (u, v), (x, y) in zip(src, dst):
        rows.append([-u, -v, -1, 0, 0, 0, u * x, v * x, x])
        rows.append([0, 0, 0, -u, -v, -1, u * y, v * y, y])
    _, _, vt = np.linalg.svd(np.array(rows))
    h = vt[-1].reshape(3, 3)
    return h / h[2, 2]


def to_pitch(h: np.ndarray, u: float, v: float) -> tuple[float, float]:
    x, y, w = h @ np.array([u, v, 1.0])
    return float(np.clip(x / w, 0, 105)), float(np.clip(y / w, 0, 68))


def main(clip_dir: Path) -> None:
    info = configparser.ConfigParser()
    info.read(clip_dir / "seqinfo.ini")
    seq = info["Sequence"]
    fps, length = int(seq["frameRate"]), int(seq["seqLength"])

    boxes: dict[int, dict[int, tuple[float, float, float, float]]] = defaultdict(dict)  # frame -> id -> box
    for line in (clip_dir / "gt" / "gt.txt").read_text().splitlines():
        f, tid, x, y, w, h, *_ = (float(v) for v in line.split(","))
        boxes[int(f)][int(tid)] = (x, y, w, h)

    # Camera pan: median horizontal shift of foot points shared by consecutive frames.
    pan = {1: 0.0}
    for f in range(2, length + 1):
        shared = boxes[f].keys() & boxes[f - 1].keys()
        shifts = [(boxes[f][i][0] + boxes[f][i][2] / 2) - (boxes[f - 1][i][0] + boxes[f - 1][i][2] / 2) for i in shared]
        pan[f] = pan[f - 1] + (float(np.median(shifts)) if shifts else 0.0)
    mid = length // 2
    h = homography(IMAGE_PTS, PITCH_PTS)

    # Shirt colour per track from torso crops in a few frames.
    colours: dict[int, list[np.ndarray]] = defaultdict(list)
    for f in range(1, length + 1, 60):
        img = np.asarray(Image.open(clip_dir / "img1" / f"{f:06d}.jpg").convert("RGB"), dtype=float)
        for tid, (x, y, w, bh) in boxes[f].items():
            crop = img[int(y + bh * 0.2): int(y + bh * 0.45), int(x + w * 0.25): int(x + w * 0.75)]
            if crop.size:
                colours[tid].append(crop.reshape(-1, 3).mean(axis=0))
    kit: dict[int, str] = {}
    for tid, cs in colours.items():
        r, g, b = np.mean(cs, axis=0)
        if r > 140 and g > 140 and b < 100:
            kit[tid] = "referee"
        elif (r + g + b) / 3 > 150:
            kit[tid] = "home"   # white kits
        else:
            kit[tid] = "away"   # dark kits

    frames = []
    tracks: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    for f in range(1, length + 1, SAMPLE_EVERY):
        t_ms = round((f - 1) * 1000 / fps)
        for tid, (x, y, w, bh) in boxes[f].items():
            if kit.get(tid) not in ("home", "away"):
                continue
            # Undo the pan relative to the calibration frame, then map the foot point.
            u = x + w / 2 - (pan[f] - pan[mid])
            px, py = to_pitch(h, u, y + bh)
            tracks[tid].append((t_ms, px, py))
    for tid, pts in tracks.items():
        for i, (t_ms, px, py) in enumerate(pts):
            j = max(0, i - 5)  # speed over ~1 s, which smooths box jitter
            dt = (t_ms - pts[j][0]) / 1000
            speed = min(10.0, float(np.hypot(px - pts[j][1], py - pts[j][2]) / dt)) if dt > 0 else 0.0
            # Home attacks to the right in this frame; express x as "towards the opponent goal".
            x_pct = px / 105 * 100 if kit[tid] == "home" else 100 - px / 105 * 100
            frames.append({"player_id": f"t{tid}", "media_ms": t_ms, "x": round(x_pct, 2), "y": round(py / 68 * 100, 2),
                           "speed_mps": round(speed, 2), "heart_rate": None, "on_pitch": True})

    numbers = {"home": 0, "away": 0}
    players = []
    for tid in sorted(tracks):
        side = kit[tid]
        numbers[side] += 1
        xs = [p[1] for p in tracks[tid]]
        ys = [p[2] for p in tracks[tid]]
        # Track ids are not shirt numbers, so players are named by club and tracking order.
        players.append({"id": f"t{tid}", "name": f"{'Leipzig' if side == 'home' else 'Bayern'} player {numbers[side]}",
                        "shirt": numbers[side], "team": side, "position": "MF",
                        "base": {"x": round(float(np.mean(xs)) / 105 * 100, 1), "y": round(float(np.mean(ys)) / 68 * 100, 1)},
                        "startsOn": True})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": f"SportsMOT {seq['name']} (CC BY-NC 4.0). Positions estimated from broadcast video, not vests.",
        "clip": seq["name"], "durationMs": round(length * 1000 / fps), "players": players,
        "frames": sorted(frames, key=lambda fr: fr["media_ms"]),
    }))
    print(f"{len(players)} players ({sum(1 for p in players if p['team'] == 'home')} white, "
          f"{sum(1 for p in players if p['team'] == 'away')} dark), {len(frames)} frames → {OUT}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

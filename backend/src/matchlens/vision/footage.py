"""Broadcast clips on disk: where they live, and a standard-colour copy of any HDR one.

Clips go in ``data/local/broadcast/clips/`` (gitignored); everything made from a clip goes in
``data/local/broadcast/<clip name>/``.

A screen recording on a recent Mac, or a download of a modern broadcast, is often HDR: 10-bit
colour with a PQ or HLG transfer curve. OpenCV decodes it without tone mapping, so the picture comes
out grey and flat, and the white lines lose the contrast the calibration fits to. Such a clip is
converted once to 8-bit standard dynamic range at no more than 1920 x 1080 (macOS ``avconvert``, or
FFmpeg with zscale), and the copy is what gets labelled and calibrated, so frame numbers and pixels
agree between the two. The copy remembers the size and modification time of the file it came from,
so a clip replaced under the same name is converted afresh.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

DATA = Path(__file__).resolve().parents[3] / "data" / "local" / "broadcast"
CLIPS = DATA / "clips"
VIDEO = {".mp4", ".mov", ".mkv", ".avi", ".ts", ".m4v", ".webm"}
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ and HLG
# FFmpeg's route when avconvert is missing: to linear light, tone-map, back to BT.709.
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale='min(1920,iw)':-2")


def default_clip() -> Path:
    """The newest video in ``CLIPS``."""
    found = sorted((p for p in CLIPS.glob("*") if p.suffix.lower() in VIDEO), key=lambda p: p.stat().st_mtime)
    if not found:
        raise SystemExit(f"no clip given and no video in {CLIPS}")
    return found[-1]


def workdir(clip: Path) -> Path:
    return DATA / clip.stem


def is_hdr(clip: Path) -> bool:
    """Whether the clip's video is HDR, by its transfer curve or bit depth (False without ffprobe)."""
    if shutil.which("ffprobe") is None:
        return False
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt,color_transfer",
         "-of", "json", str(clip)], capture_output=True, text=True, check=False)
    streams = json.loads(out.stdout or "{}").get("streams", [])
    if not streams:
        return False
    pix_fmt = streams[0].get("pix_fmt", "")
    return streams[0].get("color_transfer") in HDR_TRANSFERS or "p10" in pix_fmt or "p12" in pix_fmt


def _has_zscale() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=False)
    return " zscale " in out.stdout


def prepare(clip: Path) -> Path:
    """The file to read frames from: the clip itself, or for an HDR clip its standard-colour copy
    (``<workdir>/sdr.mov``), made on first use."""
    if not is_hdr(clip):
        return clip
    out = workdir(clip) / "sdr.mov"
    stamp = out.with_name("sdr.json")
    stat = clip.stat()
    origin = {"source": clip.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if out.exists() and stamp.exists() and json.loads(stamp.read_text()) == origin:
        return out
    if out.exists():
        # A different video under the same name: what was made from the old one no longer fits.
        shutil.rmtree(out.parent / "frames", ignore_errors=True)
        labels = out.parent / "labels.json"
        if labels.exists():
            labels.replace(out.parent / "labels.replaced.json")
            print(f"{clip.name} has changed: its old labels are kept as labels.replaced.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name("sdr.partial.mov")
    print(f"{clip.name} is HDR: making a standard-colour copy for reading (once) ...")
    if shutil.which("avconvert"):
        cmd = ["avconvert", "--preset", "Preset1920x1080", "--source", str(clip), "--output", str(partial),
               "--replace"]
    elif _has_zscale():
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vf", TONEMAP, "-c:v", "libx264", "-crf", "16",
               "-an", str(partial)]
    else:
        raise SystemExit(f"{clip.name} is HDR and neither avconvert nor FFmpeg with zscale is available: "
                         "convert it to 8-bit SDR first")
    subprocess.run(cmd, check=True)
    partial.replace(out)
    stamp.write_text(json.dumps(origin))
    return out

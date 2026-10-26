"""Download one SoccerNet match video and stitch it into the demo's timeline.

SoccerNet videos are released only after signing their NDA (https://www.soccer-net.org/data).
You receive a password by email; pass it in the environment, never on the command line history:

    pip install SoccerNet
    SOCCERNET_PASSWORD=... python scripts/fetch_soccernet_video.py

Writes frontend/public/video/nor-har.mp4: first half trimmed to 45:00, then the second half, which is
the same "half time collapsed" clock the demo and soccernet-calibration.ts use. Needs ffmpeg on PATH.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

GAME = "england_epl/2015-2016/2015-08-23 - 15-30 West Brom 2 - 3 Chelsea"
OUT = Path(__file__).resolve().parents[1] / "frontend" / "public" / "video" / "nor-har.mp4"
QUALITY = os.environ.get("SOCCERNET_QUALITY", "224p")  # "224p" (~250 MB/half) or "720p" (~1 GB/half)


def main() -> None:
    password = os.environ.get("SOCCERNET_PASSWORD")
    if not password:
        sys.exit("Set SOCCERNET_PASSWORD to the password from your SoccerNet NDA email.")
    from SoccerNet.Downloader import SoccerNetDownloader

    work = Path(tempfile.mkdtemp(prefix="soccernet-"))
    dl = SoccerNetDownloader(LocalDirectory=str(work))
    dl.password = password
    halves = [f"1_{QUALITY}.mkv", f"2_{QUALITY}.mkv"]
    dl.downloadGame(game=GAME, files=halves)
    src = work / GAME
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-i", str(src / halves[0]), "-i", str(src / halves[1]),
        "-filter_complex", "[0:v]trim=0:2700,setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-movflags", "+faststart", "-an", str(OUT),
    ], check=True)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()

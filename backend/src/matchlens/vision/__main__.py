"""Turn a SoccerTrack v2 clip into a Matchlens match and score it against the labels.

    uv run --group vision python -m matchlens.vision 128057
    uv run --group vision python -m matchlens.vision 128057 --detect 30

writes ``data/local/feeds/video-<match>.json``, which ``POST /api/replay/feed``
plays like any other match. ``--detect`` also runs the player detector and
tracker on the clip's first seconds and scores them against the labelled boxes
(it downloads the RT-DETR weights, about 172 MB, on first use).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from .evaluate import LABELS, Labelled, score, score_tracks
from .events import detect
from .feed import build_match
from .soccertrack import DEAD, SoccerTrackMatch
from .teams import LEFT, RIGHT, sample_frames, split_teams, track_colours
from .tracks import Tracks, fill_gaps, project, smooth

DATA = Path(__file__).resolve().parents[3] / "data" / "local"
BALL_NEAR_M = 3.0
HEAD_ROOM_PX = 80


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("match", help="SoccerTrack match id, e.g. 128057")
    parser.add_argument("--root", type=Path, default=DATA / "soccertrack")
    parser.add_argument("--out", type=Path, default=DATA / "feeds")
    parser.add_argument("--samples", type=int, default=30, help="frames sampled for kit colours")
    parser.add_argument("--detect", type=float, default=0.0, metavar="SECONDS",
                        help="also detect and track players over the first SECONDS, every other frame")
    args = parser.parse_args()

    source = SoccerTrackMatch(args.root, args.match)
    tracks = source.tracks()
    print(f"{tracks.frames} frames, {len(tracks.ids)} tracks at {tracks.fps:g} fps")
    players = smooth(fill_gaps(project(tracks, source.projector())))
    split = split_teams(track_colours(source.clip, tracks, sample_frames(tracks.frames, args.samples)),
                        np.nanmedian(players[..., 0], axis=0))
    print(f"teams: {np.sum(split.labels == LEFT)} left ({split.kits[0]}), {np.sum(split.labels == RIGHT)} right "
          f"({split.kits[1]}), keepers {sorted(split.keepers)}")
    ball = source.ball(tracks.frames)
    found = detect(players, ball.positions, ball.status != DEAD, split.labels, split.keepers, tracks.fps)

    side = {"left": LEFT, "right": RIGHT}
    labelled = [Labelled(a.frame, LABELS[a.label], side[a.side]) for a in source.actions(tracks.frames)
                if a.label in LABELS]
    print(f"\n{'event':<12}{'labelled':>9}{'found':>7}{'matched':>9}{'recall':>8}{'precision':>11}")
    for s in score(found, labelled, tracks.fps):
        print(f"{s.type.value:<12}{s.labelled:>9}{s.detected:>7}{s.matched:>9}{s.recall:>8.0%}{s.precision:>11.0%}")

    if args.detect > 0:
        detection(source, tracks, args.detect)

    match = build_match(f"video-{args.match}", split, players, ball.positions, found, tracks.fps)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{match.meta.match_id}.json"
    path.write_text(match.model_dump_json())
    print(f"\n{len(match.events)} events, {len(match.tracking)} tracking frames -> {path}")


def detection(source: SoccerTrackMatch, truth: Tracks, seconds: float) -> None:
    from .detect import Detector, Tiling, track_clip

    projector = source.projector()
    frames = range(0, min(int(seconds * truth.fps), truth.frames), 2)
    # The band of the frame the pitch spans (from the surveyed lines, not the player labels), with
    # room for a player standing on the far touchline.
    rows = source.keypoints()[0][:, 1]
    tiling = Tiling(top=max(int(rows.min()) - HEAD_ROOM_PX, 0), bottom=int(rows.max()) + 40, width=source.width())
    print(f"\ndetecting players on {len(frames)} frames ...")
    found, balls = track_clip(source.clip, Detector(tiling), frames, on_pitch=projector.on_pitch)
    s = score_tracks(found, truth, frames)
    print(f"players: recall {s.recall:.0%}, precision {s.precision:.0%}, {s.switches} identity switches, "
          f"MOTA {s.mota:.2f}, identity purity {s.purity:.0%} ({len(found.ids)} track ids)")
    ball = source.ball(truth.frames)
    in_play = [f for f in frames if ball.status[f] != DEAD]
    near = 0
    for f in in_play:
        if len(balls[f]):
            centres = projector.to_pitch(np.c_[balls[f][:, [0, 2]].mean(axis=1), balls[f][:, [1, 3]].mean(axis=1)])
            gaps = np.linalg.norm(centres - ball.positions[f], axis=1)
            near += bool(np.isfinite(gaps).any() and np.nanmin(gaps) <= BALL_NEAR_M)
    print(f"ball: found within {BALL_NEAR_M:g} m on {near / max(len(in_play), 1):.0%} of in-play frames")


if __name__ == "__main__":
    main()

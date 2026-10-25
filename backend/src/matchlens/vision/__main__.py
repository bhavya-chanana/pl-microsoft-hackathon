"""Turn a SoccerTrack v2 clip into a Matchlens match and score it against the labels.

    uv run --group vision python -m matchlens.vision 128057

writes ``data/local/feeds/video-<match>.json``, which ``POST /api/replay/feed``
plays like any other match.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from .evaluate import LABELS, Labelled, score
from .events import detect
from .feed import build_match
from .soccertrack import DEAD, SoccerTrackMatch
from .teams import LEFT, RIGHT, sample_frames, split_teams, track_colours
from .tracks import fill_gaps, project, smooth

DATA = Path(__file__).resolve().parents[3] / "data" / "local"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("match", help="SoccerTrack match id, e.g. 128057")
    parser.add_argument("--root", type=Path, default=DATA / "soccertrack")
    parser.add_argument("--out", type=Path, default=DATA / "feeds")
    parser.add_argument("--samples", type=int, default=30, help="frames sampled for kit colours")
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

    match = build_match(f"video-{args.match}", split, players, ball.positions, found, tracks.fps)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{match.meta.match_id}.json"
    path.write_text(match.model_dump_json())
    print(f"\n{len(match.events)} events, {len(match.tracking)} tracking frames -> {path}")


if __name__ == "__main__":
    main()

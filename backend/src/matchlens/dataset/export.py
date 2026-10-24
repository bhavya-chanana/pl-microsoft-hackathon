"""Open synthetic dataset: export and load generated matches (FR-011).

Each match is written to its own folder:

    <out>/<match_id>/meta.json           match metadata, seed, storyline and summary stats
    <out>/<match_id>/events.jsonl        one Event per line, in feed (sequence) order
    <out>/<match_id>/tracking.jsonl.gz   one TrackingFrame per line, 1 Hz

plus ``<out>/index.json`` listing every match. Output is byte-for-byte
reproducible for the same seeds. Schemas: ``docs/schema`` (see schemas.py).

Run:
    uv run python -m matchlens.dataset.export --out ../dataset --schemas ../docs/schema
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

from matchlens.dataset.schemas import write_schemas
from matchlens.domain.models import Event, MatchMeta, SyntheticMatch, TrackingFrame
from matchlens.simulator.calibration import NEUTRAL, REFERENCE, measure_match
from matchlens.simulator.engine import MatchSimulator
from matchlens.simulator.roster import default_match_meta
from matchlens.simulator.storyline import Storyline, comeback_storyline

FORMAT = "matchlens-dataset/1"
HERO_SEED = 7


def _line(model: Event | TrackingFrame) -> str:
    return json.dumps(model.model_dump(mode="json"), separators=(",", ":")) + "\n"


def generate(seed: int, storyline: Storyline, match_id: str) -> SyntheticMatch:
    meta = default_match_meta(match_id=match_id, storyline_title=storyline.title)
    return MatchSimulator(seed=seed, meta=meta, storyline=storyline).run()


def export_match(match: SyntheticMatch, storyline: Storyline, out: Path) -> dict[str, Any]:
    """Write one match folder and return its index entry."""
    folder = out / match.meta.match_id
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "events.jsonl").open("w") as fh:
        fh.writelines(_line(e) for e in match.events)
    # mtime=0 keeps the archive reproducible.
    with gzip.GzipFile(folder / "tracking.jsonl.gz", "wb", mtime=0) as gz:
        for frame in match.tracking:
            gz.write(_line(frame).encode())
    summary = {k: round(v, 1) for k, v in measure_match(match).items()}
    meta = {
        "format": FORMAT,
        "seed": match.seed,
        "storyline": storyline.model_dump(mode="json"),
        "summary": summary,
        "counts": {"events": len(match.events), "tracking_frames": len(match.tracking)},
        "meta": match.meta.model_dump(mode="json"),
    }
    (folder / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return {
        "match_id": match.meta.match_id,
        "seed": match.seed,
        "storyline": storyline.title,
        "scripted": bool(storyline.beats),
        "final_score": _final_score(match),
        "events": len(match.events),
        "tracking_frames": len(match.tracking),
        "path": folder.name,
    }


def _final_score(match: SyntheticMatch) -> dict[str, int]:
    ft = next(e for e in reversed(match.events) if e.type == "full_time")
    return {match.meta.home.team_id: int(ft.attrs["home_score"]), match.meta.away.team_id: int(ft.attrs["away_score"])}


def load_match(folder: Path) -> SyntheticMatch:
    """Read a match folder back into the schema models (round-trip of export_match)."""
    meta = json.loads((folder / "meta.json").read_text())
    with (folder / "events.jsonl").open() as fh:
        events = [Event.model_validate_json(line) for line in fh if line.strip()]
    with gzip.open(folder / "tracking.jsonl.gz", "rt") as gz:
        tracking = [TrackingFrame.model_validate_json(line) for line in gz if line.strip()]
    return SyntheticMatch(meta=MatchMeta.model_validate(meta["meta"]), events=events, tracking=tracking,
                          seed=meta["seed"])


def export_dataset(out: Path, unscripted: int = 4) -> dict[str, Any]:
    """The hero demo match (same ids as the live demo) plus unscripted calibration matches."""
    matches = [(HERO_SEED, comeback_storyline(), "ml-demo-001")]
    matches += [(seed, NEUTRAL, f"ml-open-s{seed:03d}") for seed in range(1, unscripted + 1)]
    entries = [export_match(generate(seed, story, match_id), story, out) for seed, story, match_id in matches]
    index = {
        "format": FORMAT,
        "description": "Matchlens synthetic football matches: fictional clubs and players, 105 x 68 m pitch.",
        "license": "CC-BY-4.0",
        "calibration_reference": {k: list(v) for k, v in REFERENCE.items()},
        "matches": entries,
    }
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("../dataset"))
    parser.add_argument("--schemas", type=Path, default=Path("../docs/schema"))
    parser.add_argument("--unscripted", type=int, default=4, help="number of unscripted matches besides the hero")
    args = parser.parse_args()
    index = export_dataset(args.out, args.unscripted)
    paths = write_schemas(args.schemas)
    for m in index["matches"]:
        print(f"{m['match_id']:<16} seed {m['seed']:<3} {m['final_score']}  {m['events']} events, "
              f"{m['tracking_frames']} frames  ({m['storyline']})")
    print(f"wrote {len(index['matches'])} matches to {args.out} and {len(paths)} schemas to {args.schemas}")


if __name__ == "__main__":
    main()

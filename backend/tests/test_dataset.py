import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from matchlens.dataset.export import export_match, load_match
from matchlens.dataset.schemas import SCHEMAS, render, schema_for
from matchlens.domain.models import Event, TrackingFrame
from matchlens.ingest.adapters import JsonLinesAdapter, write_json_lines
from matchlens.simulator.storyline import comeback_storyline

ROOT = Path(__file__).resolve().parents[2]


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(schema_for(name))


@pytest.mark.parametrize("name", list(SCHEMAS))
def test_published_schemas_match_the_models(name):
    path = ROOT / "docs" / "schema" / f"{name}.schema.json"
    assert path.read_text() == render(name), "run: uv run python -m matchlens.dataset.export"


def test_export_round_trips_and_is_reproducible(demo_match, tmp_path):
    story = comeback_storyline()
    entry = export_match(demo_match, story, tmp_path / "a")
    export_match(demo_match, story, tmp_path / "b")
    folder = tmp_path / "a" / entry["path"]
    assert load_match(folder) == demo_match
    for name in ("meta.json", "events.jsonl", "tracking.jsonl.gz"):
        assert (folder / name).read_bytes() == (tmp_path / "b" / entry["path"] / name).read_bytes()
    assert entry["final_score"] == {"NBR": 3, "HBC": 2}


def _assert_close(actual, expected, path="match"):
    """Equal except for float noise: macOS and Linux differ in the last digit of some coordinates."""
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), f"{path}: keys differ"
        for key in expected:
            _assert_close(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert len(actual) == len(expected), f"{path}: length differs"
        for i, (a, e) in enumerate(zip(actual, expected)):
            _assert_close(a, e, f"{path}[{i}]")
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9), f"{path}: {actual} != {expected}"
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def test_published_hero_match_is_the_live_demo(demo_match):
    folder = ROOT / "dataset" / "ml-demo-001"
    published = load_match(folder).model_dump(mode="json")
    _assert_close(published, demo_match.model_dump(mode="json"))  # else the dataset is stale; re-run the export


def test_dataset_rows_validate_against_schemas(demo_match):
    events, frames = validator("event"), validator("tracking-frame")
    for e in demo_match.events:
        events.validate(e.model_dump(mode="json"))
    for f in demo_match.tracking[::60]:
        frames.validate(f.model_dump(mode="json"))
    meta = json.loads((ROOT / "dataset" / "ml-demo-001" / "meta.json").read_text())
    validator("match-meta").validate(meta["meta"])
    validator("storyline").validate(meta["storyline"])


def test_json_lines_adapter_replays_an_exported_feed(demo_match, tmp_path):
    path = tmp_path / "feed.jsonl"
    write_json_lines(demo_match, path)

    async def collect():
        return [item async for item in JsonLinesAdapter(demo_match.meta, path, speed=1e9).stream()]

    items = asyncio.run(collect())
    events = [i for i in items if isinstance(i, Event)]
    assert events == demo_match.events
    assert sum(isinstance(i, TrackingFrame) for i in items) == len(demo_match.tracking)
    elapsed = [i.elapsed for i in items]
    assert elapsed == sorted(elapsed)

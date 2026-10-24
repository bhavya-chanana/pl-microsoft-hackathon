"""JSON Schemas for the published data contracts (FR-011, FR-027, BRD §9).

Generated from the Pydantic models, so the documented schema is always the
one the code validates against. ``tests/test_dataset.py`` fails if the files
in ``docs/schema`` drift from the models.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from matchlens.agents.contracts import Insight, Overlay
from matchlens.domain.models import Event, MatchMeta, TrackingFrame
from matchlens.simulator.storyline import Storyline

SCHEMA_BASE = "https://raw.githubusercontent.com/bhavya-chanana/pl-microsoft-hackathon/main/docs/schema/"

SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    "event": (Event, "One match event in the provider-neutral feed schema."),
    "tracking-frame": (TrackingFrame, "Positions and speeds of every player on the pitch and the ball at one instant."),
    "match-meta": (MatchMeta, "Match metadata: teams, lineups, formations, players, venue and kick-off."),
    "storyline": (Storyline, "Scripted storyline the simulator plays out."),
    "overlay": (Overlay, "Timed broadcast graphic, as published to a rendering partner."),
    "insight": (Insight, "Fact-checked insight with every personalised variant and its provenance."),
}


def schema_for(name: str) -> dict[str, Any]:
    model, description = SCHEMAS[name]
    body = model.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{SCHEMA_BASE}{name}.schema.json",
        **body,
        "description": description,
    }


def render(name: str) -> str:
    return json.dumps(schema_for(name), indent=2, ensure_ascii=False) + "\n"


def write_schemas(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in SCHEMAS:
        path = out / f"{name}.schema.json"
        path.write_text(render(name))
        paths.append(path)
    return paths

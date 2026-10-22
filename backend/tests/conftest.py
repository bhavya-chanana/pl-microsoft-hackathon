from __future__ import annotations

import os

import pytest

# Tests never call Azure, even when backend/.env points at a live endpoint.
os.environ["MATCHLENS_LLM"] = "offline"

from matchlens.simulator.engine import generate_match


@pytest.fixture(scope="session")
def demo_match():
    """The hero demo match (comeback storyline, seed 7)."""
    return generate_match(seed=7)


def replay_order(match):
    """Events and tracking frames interleaved in elapsed order, as a live feed would deliver them."""
    items = [(e.elapsed, 1, e.sequence, e) for e in match.events]
    items += [(f.elapsed, 0, 0, f) for f in match.tracking]
    items.sort(key=lambda t: (t[0], t[1], t[2]))
    return [(kind, x) for _, kind, _, x in items]

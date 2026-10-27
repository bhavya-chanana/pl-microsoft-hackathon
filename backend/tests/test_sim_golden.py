"""Golden output of the simulator.

The hero demo cassette is keyed by signal ids derived from the seed-7 match, so
``run()`` must stay byte-for-byte identical while style, intent and fork hooks
are added to the engine. Hashes were captured before any of those changes.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from matchlens.simulator.engine import generate_match

GOLDEN = {
    7: "ab8c64e7daaa778203e0ccc03868691a3d8549e800f62b01fb42566653559749",
    3: "626fd98a42fe7473d2294b499d404b690abe036bd1732b73d3d62906b11c23f3",
    11: "663f8726e9186d7f653e7d15734efc000b999f2b972f1d5bd0183d3a1345ccfd",
}


def match_digest(match) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([e.model_dump(mode="json") for e in match.events], sort_keys=True).encode())
    h.update(json.dumps([f.model_dump(mode="json") for f in match.tracking], sort_keys=True).encode())
    return h.hexdigest()


@pytest.mark.parametrize("seed", sorted(GOLDEN))
def test_simulator_output_is_unchanged(seed, demo_match):
    match = demo_match if seed == 7 else generate_match(seed=seed)
    assert match_digest(match) == GOLDEN[seed]

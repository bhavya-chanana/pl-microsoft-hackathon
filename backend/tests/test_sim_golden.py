"""Golden output of the simulator.

The hero demo cassette is keyed by signal ids derived from the seed-7 match, so
``run()`` must stay identical (to six decimals, so macOS and Linux libm agree; see ``_stable``) while
style, intent and fork hooks are added to the engine. The hashes were first captured before any of those
changes, over the unrounded output, and re-taken over the rounded output once Linux CI showed last-bit
differences; the unrounded hashes still matched on macOS at that point.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from matchlens.simulator.engine import generate_match

GOLDEN = {
    7: "733d2e63e096569dccbc265e1fb819b66a9413897490cd0e9d7abdfed70f8d63",
    3: "166b3127cf6692ad47f7c8af78f4d993f4af17bc01c89cb6e4f9c9ad9f42b95e",
    11: "fe86342b883651f56d0a7a6e4410d7a57328d2c5cd07b3c2012403b1aee7c958",
}


def _stable(value, places: int = 6):
    """Floats rounded to ``places`` decimals, so the digest does not depend on the platform's libm.

    The engine places events with sin/cos/atan2/exp, and those differ in the last bit between macOS and glibc
    (event coordinates came out as 3.999784381917528 on one and 3.9997843819175287 on the other, and nowhere
    else: every tracking frame and every decision the simulator takes was identical). Six decimals is a
    micrometre on a pitch measured in metres and a microsecond on a clock in seconds: far below anything the
    match shows, yet a hundred million times coarser than the difference being ignored.
    """
    if isinstance(value, float):
        return round(value, places) + 0.0  # + 0.0: a tiny negative must not hash as "-0.0"
    if isinstance(value, dict):
        return {k: _stable(v, places) for k, v in value.items()}
    if isinstance(value, list):
        return [_stable(v, places) for v in value]
    return value


def match_digest(match) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(_stable([e.model_dump(mode="json") for e in match.events]), sort_keys=True).encode())
    h.update(json.dumps(_stable([f.model_dump(mode="json") for f in match.tracking]), sort_keys=True).encode())
    return h.hexdigest()


@pytest.mark.parametrize("seed", sorted(GOLDEN))
def test_simulator_output_is_unchanged(seed, demo_match):
    match = demo_match if seed == 7 else generate_match(seed=seed)
    assert match_digest(match) == GOLDEN[seed]

"""Team playing style as simulator knobs.

Every field is neutral by default: multipliers default to 1.0 and offsets to
0.0, and the engine applies them as ``const * mult`` or ``const + offset`` so a
default ``SimStyle`` leaves the generated match byte-for-byte unchanged (see
tests/test_sim_golden.py). Styles come from observed play
(``tactics.styles.derive_style``) or from real league data
(``tactics.styles.from_team_style``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SimStyle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Out of possession
    press_intensity: float = Field(1.0, ge=0.3, le=2.0)  # press radius (6 m) and trigger chance (0.8)
    block_height: float = Field(0.0, ge=-15.0, le=15.0)  # metres added to the out-of-possession shift
    line_height: float = Field(0.0, ge=-12.0, le=12.0)  # metres, defenders only
    compactness_out: float = Field(1.0, ge=0.6, le=1.4)  # multiplies the out-of-possession width squeeze
    # In possession
    width_in_poss: float = Field(1.0, ge=0.7, le=1.4)
    directness: float = Field(1.0, ge=0.3, le=2.5)  # multiplies the long-ball chance
    verticality: float = Field(1.0, ge=0.5, le=2.0)  # multiplies the forward weight of pass choice
    tempo: float = Field(1.0, ge=0.6, le=1.6)  # >1 = quicker circulation (shorter reception delays)
    crossing: float = Field(1.0, ge=0.3, le=2.5)
    dribbling: float = Field(1.0, ge=0.3, le=2.5)
    shot_appetite: float = Field(1.0, ge=0.4, le=2.5)
    # Transitions
    counter_boost: float = Field(0.0, ge=0.0, le=1.0)  # extra verticality right after a regain
    counter_window_s: float = Field(8.0, ge=2.0, le=20.0)

    def with_delta(self, delta: dict[str, float]) -> SimStyle:
        """Apply additive changes, clamped to each field's bounds."""
        values = self.model_dump()
        for key, change in delta.items():
            if key not in values:
                raise KeyError(f"unknown style knob: {key}")
            field = type(self).model_fields[key]
            lo = next(m.ge for m in field.metadata if hasattr(m, "ge"))
            hi = next(m.le for m in field.metadata if hasattr(m, "le"))
            values[key] = min(max(values[key] + change, lo), hi)
        return SimStyle(**values)


NEUTRAL = SimStyle()
STYLE_KNOBS: tuple[str, ...] = tuple(SimStyle.model_fields)

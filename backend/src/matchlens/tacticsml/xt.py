"""Expected threat (xT): the chance a possession in a zone ends in a goal, on a 16 x 12 grid.

Karun Singh's model fitted on StatsBomb open data by ``train/xt_fit.py`` and shipped as ``xt_grid.json``.
Coordinates are absolute metres on the 105 x 68 pitch; ``attacking_right`` says which goal is attacked.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH

GRID_PATH = Path(__file__).with_name("xt_grid.json")


class XtGrid:
    def __init__(self, values: np.ndarray, provenance: dict[str, Any] | None = None) -> None:
        self.values = np.asarray(values, dtype=float)  # [rows (y), cols (x)], attacking towards x=105
        self.rows, self.cols = self.values.shape
        self.provenance = provenance or {}

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> XtGrid:
        raw = json.loads(GRID_PATH.read_text())
        return cls(np.array(raw["values"], dtype=float), raw.get("provenance"))

    def value(self, x: float, y: float, attacking_right: bool = True) -> float:
        return float(self.values_at(np.array([x]), np.array([y]), attacking_right)[0])

    def values_at(self, x: np.ndarray, y: np.ndarray, attacking_right: bool = True) -> np.ndarray:
        """Vectorised lookup for arrays of absolute coordinates."""
        xr = np.asarray(x, dtype=float)
        yr = np.asarray(y, dtype=float)
        if not attacking_right:
            xr, yr = PITCH_LENGTH - xr, PITCH_WIDTH - yr
        c = np.clip((xr / PITCH_LENGTH * self.cols).astype(int), 0, self.cols - 1)
        r = np.clip((yr / PITCH_WIDTH * self.rows).astype(int), 0, self.rows - 1)
        return self.values[r, c]

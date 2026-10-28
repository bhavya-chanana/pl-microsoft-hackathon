"""Numpy-only definitions shared by the Next10 trainer and the serve-time inference (no torch here).

* the ball-zone grid (16 columns along x, 12 rows along y over the 105 x 68 m pitch, canonical centred metres),
  the Gaussian-smoothed target and the D2 action on a grid;
* the receiver-marginalised shot probability ``P(shot) = sum_i P(shot | receiver i) P(receiver i)``;
* the team table behind the FiLM conditioning: club names -> integer ids (0 = unknown) and a style vector from
  the league-data playbooks (``backend/data/local/leaguedata/kb/playbooks.json``) when the club is there.

Everything is in the canonical frame of ``features`` (centred metres, possession team attacks +x) unless a
function says otherwise.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from matchlens.tacticsml import features as F

# ---------------------------------------------------------------- ball-zone grid

GRID_COLS = 16
GRID_ROWS = 12
N_CELLS = GRID_COLS * GRID_ROWS
CELL_W = 2 * F.HALF_L / GRID_COLS  # 6.5625 m
CELL_H = 2 * F.HALF_W / GRID_ROWS  # 5.6667 m
ZONE_SIGMA = 1.0  # cells: width of the Gaussian-smoothed target
ZONE_HORIZON = 10  # seconds

_cx = -F.HALF_L + (np.arange(GRID_COLS) + 0.5) * CELL_W
_cy = -F.HALF_W + (np.arange(GRID_ROWS) + 0.5) * CELL_H
CELL_CENTRES = np.stack(np.meshgrid(_cx, _cy), axis=-1).reshape(N_CELLS, 2).astype(np.float32)  # (192, 2), row-major


def cell_of(xy: np.ndarray) -> np.ndarray:
    """(..., 2) canonical metres -> flat cell index ``row * 16 + col`` (positions outside the pitch are clamped)."""
    xy = np.asarray(xy, dtype=np.float64)
    col = np.clip(np.floor((xy[..., 0] + F.HALF_L) / CELL_W), 0, GRID_COLS - 1).astype(np.int64)
    row = np.clip(np.floor((xy[..., 1] + F.HALF_W) / CELL_H), 0, GRID_ROWS - 1).astype(np.int64)
    return row * GRID_COLS + col


def smoothed_target(cell: np.ndarray, sigma: float = ZONE_SIGMA) -> np.ndarray:
    """Gaussian bump (std ``sigma`` cells, in cell-index units) around each flat cell index, normalised over the
    grid: ``(B,) -> (B, 192)`` rows summing to 1."""
    cell = np.asarray(cell, dtype=np.int64)
    r0, c0 = cell // GRID_COLS, cell % GRID_COLS
    rr, cc = np.divmod(np.arange(N_CELLS), GRID_COLS)
    d2 = (rr[None] - r0[:, None]) ** 2 + (cc[None] - c0[:, None]) ** 2
    w = np.exp(-d2 / (2.0 * sigma ** 2))
    return (w / w.sum(-1, keepdims=True)).astype(np.float32)


def reflect_grid(g: np.ndarray, signs: tuple[float, float]) -> np.ndarray:
    """Action of a D2 element on a grid ``(..., 12, 16)``: mirror x flips the columns, mirror y the rows."""
    if signs[0] < 0:
        g = np.flip(g, axis=-1)
    if signs[1] < 0:
        g = np.flip(g, axis=-2)
    return g


def cell_features(ball_xy: np.ndarray) -> np.ndarray:
    """Per-cell query features for the zone head given the ball position in the (possibly reflected) frame:
    ``(B, 2) -> (B, 192, 6)``: cell centre / (52.5, 34), cell minus ball / 30, distance to the ball / 30, and
    cell x relative to the target goal. Torch version: ``heads.cell_features`` (parity is tested)."""
    c = CELL_CENTRES[None]
    b = np.asarray(ball_xy, dtype=np.float32)[:, None]
    rel = (c - b) / 30.0
    return np.concatenate([np.broadcast_to(c / np.array([F.HALF_L, F.HALF_W], dtype=np.float32), rel.shape), rel,
                           np.linalg.norm(rel, axis=-1, keepdims=True),
                           np.broadcast_to((F.HALF_L - c[..., :1]) / F.HALF_L, rel.shape[:2] + (1,))], axis=-1
                          ).astype(np.float32)


# ---------------------------------------------------------------- receiver-marginalised shot

def marginal_shot(receiver_probs: np.ndarray, shot_given: np.ndarray) -> np.ndarray:
    """``P(shot) = sum_i P(shot | recv i) P(recv i)`` over the 11 possession-team slots and the "lost" outcome.

    ``receiver_probs`` (..., 12) sums to 1 (last = lost); ``shot_given`` (..., 12) holds P(shot | outcome i)."""
    return (np.asarray(receiver_probs) * np.asarray(shot_given)).sum(-1)


# ---------------------------------------------------------------- team table (FiLM conditioning)

STYLE_KEYS = ("ppda", "ppda_allowed", "deep_share", "directness", "xg_for90", "xg_against90", "xg_per_shot",
              "shot_distance_mean", "box_shot_share", "counter_proxy", "create_cross", "create_through_ball",
              "fouls90", "sit_back_index")
KB_PLAYBOOKS = Path(__file__).resolve().parents[3] / "data" / "local" / "leaguedata" / "kb" / "playbooks.json"
_ALIASES = {"fc bayern munchen": "bayern munich", "bayern munchen": "bayern munich",
            "rasenballsport leipzig": "rb leipzig", "rb leipzig": "rb leipzig",
            "1 fc union berlin": "union berlin", "borussia monchengladbach": "borussia monchengladbach",
            "fc barcelona": "barcelona", "atletico madrid": "atletico de madrid", "psg": "paris saint-germain",
            "man city": "manchester city", "manchester city fc": "manchester city", "liverpool": "liverpool fc",
            "brighton": "brighton hove albion", "brighton and hove albion": "brighton hove albion"}
_STOP = {"fc", "cf", "afc", "sc", "ac", "sv", "club", "football", "calcio", "futbol", "the", "1", "de", "fussballclub"}


def normalise_team(name: str | None) -> str:
    """Lower-case, accent-free, punctuation-free club name without generic tokens ("FC", "1.", ...)."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s.replace("&", " ").replace("-", " "))
    s = " ".join(s.split())
    s = _ALIASES.get(s, s)
    toks = [t for t in s.split() if t not in _STOP]
    out = " ".join(toks)
    return _ALIASES.get(out, out)


@dataclass
class TeamTable:
    """Club vocabulary for the FiLM conditioning. Id 0 is the "unknown team" slot (zero embedding, zero style)."""

    names: list[str]  # normalised names, id = index + 1
    style: np.ndarray  # (V + 1, S) z-scores, 0 where missing
    style_mask: np.ndarray  # (V + 1, S) 1 where the playbook has the value

    def __post_init__(self) -> None:
        self._index = {n: i + 1 for i, n in enumerate(self.names)}

    @property
    def size(self) -> int:
        return len(self.names) + 1

    def lookup(self, name: str | None) -> int:
        return self._index.get(normalise_team(name), 0)

    def lookup_many(self, names: Iterable[str]) -> np.ndarray:
        return np.array([self.lookup(n) for n in names], dtype=np.int64)

    def to_arrays(self) -> dict[str, np.ndarray]:
        return {"team_names": np.array(self.names, dtype="<U64"), "team_style": self.style.astype(np.float32),
                "team_style_mask": self.style_mask.astype(np.float32)}

    @classmethod
    def from_arrays(cls, z) -> "TeamTable":
        return cls([str(n) for n in z["team_names"]], np.asarray(z["team_style"], dtype=np.float32),
                   np.asarray(z["team_style_mask"], dtype=np.float32))


def load_playbook_styles(path: Path | None = None) -> dict[str, np.ndarray]:
    """Normalised club name -> (S,) style z-scores with NaN for missing, latest season of each club. Empty when the
    league-data knowledge base is not on disk."""
    path = Path(path or KB_PLAYBOOKS)
    if not path.is_file():
        return {}
    out: dict[str, tuple[str, np.ndarray]] = {}
    for entry in json.loads(path.read_text()):
        style = entry.get("style") or {}
        z = {m["key"]: m.get("z") for m in style.get("metrics", [])}
        vec = np.array([np.nan if z.get(k) is None else float(z[k]) for k in STYLE_KEYS], dtype=np.float32)
        season = str(entry.get("season", ""))
        for nm in {style.get("team_name"), entry.get("team_id", "").replace("-", " ")}:
            key = normalise_team(nm)
            if key and (key not in out or season >= out[key][0]):
                out[key] = (season, vec)
    return {k: v[1] for k, v in out.items()}


def build_team_table(names: Sequence[str], playbooks: Path | None = None, min_count: int = 1,
                     counts: dict[str, int] | None = None) -> TeamTable:
    """Vocabulary of the clubs seen in training data (``names``: normalised or raw), with playbook style vectors
    where the club is in the knowledge base. Clubs seen fewer than ``min_count`` times stay unknown."""
    styles = load_playbook_styles(playbooks)
    uniq = sorted({normalise_team(n) for n in names} - {""})
    if counts is not None:
        uniq = [n for n in uniq if counts.get(n, 0) >= min_count]
    S = len(STYLE_KEYS)
    style = np.zeros((len(uniq) + 1, S), dtype=np.float32)
    mask = np.zeros_like(style)
    for i, n in enumerate(uniq):
        vec = styles.get(n)
        if vec is not None:
            ok = ~np.isnan(vec)
            style[i + 1, ok], mask[i + 1, ok] = vec[ok], 1.0
    return TeamTable(uniq, style, mask)


def team_ids_for(poss: Sequence[str], opp: Sequence[str], table: TeamTable) -> np.ndarray:
    """(B, 2) ids [possession team, opponent] for the batch."""
    return np.stack([table.lookup_many(poss), table.lookup_many(opp)], axis=1)

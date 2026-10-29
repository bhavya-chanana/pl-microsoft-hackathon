"""Two teams from the shirt colours of tracked people, robust to referees, keepers, staff and one-sided views.

``vision/teams.split_teams`` starts its 2-means from the two most different colours, which on broadcast footage are
a black referee and a white linesman or keeper: both teams end up in one cluster (the Liverpool v Brighton clip came
out 99% "home"). Here the start is the two most different colours *among colours that several tracks share* (a
kit is worn by many players, a referee's by one or two), the clusters are refined with a distance cut-off so people
in other colours stay outside both, and the split is refused unless the two kits really differ.
"""

from __future__ import annotations

import numpy as np

from matchlens.vision.teams import LEFT, OTHER, OUTLIER_LAB, OUTLIER_SPREAD, RIGHT, TeamSplit, _hex

NEIGHBOUR_LAB = 18.0  # colours this close count as the same kit
MIN_SHARE = 2  # a kit is shared by at least this many other tracks
MIN_KIT_SEPARATION = 22.0  # two kits closer than this in Lab are not two teams
KEEPER_ZONE_M = 25.0
PITCH_L = 105.0
ROUNDS = 12


def _kit_clusters(colours: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(labels 0/1/-1, centres (2, 3)) of the two dominant kits. Raises ValueError when there are not two."""
    dist = np.linalg.norm(colours[:, None] - colours[None], axis=2)
    near = ((dist <= NEIGHBOUR_LAB) * weights[None]).sum(axis=1) - weights  # tracks sharing the colour, not itself
    dense = np.flatnonzero(near >= MIN_SHARE)
    if len(dense) < 2:
        raise ValueError("no kit is shared by enough tracks")
    # The two biggest groups of alike colours that differ from each other: the teams (a referee's black and a
    # keeper's colour are smaller groups, even when they are further from everything else).
    first = dense[np.argmax(near[dense])]
    apart = dense[dist[first, dense] >= MIN_KIT_SEPARATION]
    if not len(apart):
        raise ValueError("only one kit colour is visible")
    second = apart[np.argmax(near[apart])]
    centres = colours[[first, second]].copy()
    labels = np.full(len(colours), OTHER)
    for round_ in range(ROUNDS):
        d = np.linalg.norm(colours[:, None] - centres[None], axis=2)
        nearest = d.argmin(axis=1)
        cut = np.full(2, OUTLIER_LAB)
        if round_ > 0:  # the first pass trusts only the fixed cut-off; later ones widen it to each kit's own spread
            for k in (0, 1):
                if (nearest == k).any():
                    cut[k] = max(OUTLIER_LAB, OUTLIER_SPREAD * float(np.median(d[nearest == k, k])))
        new = np.where(d[np.arange(len(colours)), nearest] <= cut[nearest], nearest, OTHER)
        updated = centres.copy()
        for k in (0, 1):
            if (new == k).any():
                updated[k] = np.median(colours[new == k], axis=0)
        labels = new
        if np.allclose(updated, centres, atol=0.25):
            break
        centres = updated
    if np.linalg.norm(centres[0] - centres[1]) < MIN_KIT_SEPARATION or min((labels == 0).sum(), (labels == 1).sum()) < 1:
        raise ValueError("the two kit clusters are not distinct")
    return labels, centres


def split_kits(colours: np.ndarray, median_x: np.ndarray, weights: np.ndarray | None = None) -> TeamSplit:
    """Teams from each track's median shirt colour (tracks, 3, Lab) and median pitch x in metres (corner origin).

    The kit worn further left is ``LEFT``. A track in neither kit that stays within the keeper zone of a goal line is
    that end's keeper (and counts for that end's team), else it is ``OTHER`` (referee, staff)."""
    ok = np.isfinite(colours).all(axis=1) & np.isfinite(median_x)
    if ok.sum() < 3:
        raise ValueError("not enough tracks with a visible shirt to split teams")
    idx = np.flatnonzero(ok)
    w = np.ones(len(idx)) if weights is None else np.asarray(weights, float)[idx]
    labels_kit, centres = _kit_clusters(colours[idx], w)
    if np.median(median_x[idx][labels_kit == 0]) > np.median(median_x[idx][labels_kit == 1]):
        labels_kit = np.where(labels_kit == OTHER, OTHER, 1 - labels_kit)
        centres = centres[::-1]
    labels = np.full(len(colours), OTHER)
    labels[idx] = labels_kit
    keepers: set[int] = set()
    for t in idx[labels_kit == OTHER]:
        if median_x[t] <= KEEPER_ZONE_M:
            labels[t] = LEFT
        elif median_x[t] >= PITCH_L - KEEPER_ZONE_M:
            labels[t] = RIGHT
        else:
            continue
        keepers.add(int(t))
    return TeamSplit(labels=labels, keepers=frozenset(keepers), kits=(_hex(centres[0]), _hex(centres[1])))

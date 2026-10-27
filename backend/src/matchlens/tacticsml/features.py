"""Graph features for the tactical models (numpy only, no torch).

Convention (canonical frame)
----------------------------
Coordinates are metres with the origin at the pitch centre: x in [-52.5, 52.5], y in [-34, 34].
The team in possession always attacks towards +x. ``contract.TFrame`` uses absolute coordinates
(0..105, 0..68) with a per-team ``attacking_right``; ``canonicalize`` / ``to_canonical_xy`` /
``from_canonical_xy`` convert between the two. Canonicalisation centres and, when the possession
team attacks left, mirrors x (a D2 element, so no handedness bookkeeping is needed beyond a bool).

Graph (TacticAI+, 25 nodes)
---------------------------
Node order is fixed: [0:11] possession team, [11:22] opponent (each GK first, then player_id order,
unused slots are "pad"), 22 ball, 23 own goal (defended by the possession team, at x=-52.5),
24 target goal (at x=+52.5). Goals are anchor nodes so reflecting the *whole scene* is an exact
physical symmetry.

D2 symmetry
-----------
``D2 = ("e", "fx", "fy", "fxy")``: identity, mirror x, mirror y, both (rotation by 180 degrees).
They act on positions and velocities about the centre. DECISION: ``augment`` keeps node identity
and only transforms coordinates (the goal nodes keep their roles and move to the mirrored
position, e.g. after "fx" ``goal_target`` sits at x=-52.5). Node features and edge features are
D2-invariant, so a D2-invariant model must output the same probabilities for ``augment(s, g)``
for every g. Each element is its own inverse.

Node feature columns (F = 7 + 2 * P, P = len(profile_keys))
-----------------------------------------------------------
0 is_ball, 1 is_goal_own, 2 is_goal_target, 3 is_gk, 4 is_carrier, 5 possession_team
(1 for possession-team players), 6 observed (1 for real/observed nodes, 0 for pad/unseen),
7..7+P profile values (raw, 0 where missing), 7+P..7+2P profile masks (1 where present).

Edge feature columns (E = 5, all D2-invariant, computed at the last time step)
-------------------------------------------------------------------------------
0 same_team (both players of the same team, diagonal included), 1 opponent, 2 involves_ball,
3 involves_goal, 4 distance / 10. Edges touching a pad slot are all zero.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH
from matchlens.tacticsml.contract import TBall, TFrame, TPlayer

HALF_L = PITCH_LENGTH / 2.0  # 52.5
HALF_W = PITCH_WIDTH / 2.0  # 34.0

D2 = ("e", "fx", "fy", "fxy")
_SIGNS = {"e": (1.0, 1.0), "fx": (-1.0, 1.0), "fy": (1.0, -1.0), "fxy": (-1.0, -1.0)}

T_STEPS = 5
N_TEAM = 11
N_NODES = 25
BALL = 22
GOAL_OWN = 23
GOAL_TARGET = 24
N_EDGE = 5

# node feature column indices
F_IS_BALL, F_IS_GOAL_OWN, F_IS_GOAL_TARGET, F_IS_GK, F_IS_CARRIER, F_POSSESSION, F_OBSERVED = range(7)
F_BASE = 7
# edge feature column indices
E_SAME_TEAM, E_OPPONENT, E_INVOLVES_BALL, E_INVOLVES_GOAL, E_DISTANCE = range(5)

GOAL_XY = {GOAL_OWN: (-HALF_L, 0.0), GOAL_TARGET: (HALF_L, 0.0)}


# ---------------------------------------------------------------- D2 group

def _check(g: str) -> None:
    if g not in _SIGNS:
        raise ValueError(f"unknown D2 element {g!r}")


def d2_signs(g: str) -> tuple[float, float]:
    _check(g)
    return _SIGNS[g]


def compose(a: str, b: str) -> str:
    """Group product a∘b (D2 is abelian: componentwise sign product)."""
    sa, sb = d2_signs(a), d2_signs(b)
    s = (sa[0] * sb[0], sa[1] * sb[1])
    return next(g for g, v in _SIGNS.items() if v == s)


def invert(g: str) -> str:
    """Inverse element (every D2 element is an involution)."""
    _check(g)
    return g


def apply_d2(g: str, xy: np.ndarray, vxy: np.ndarray | None = None
             ) -> tuple[np.ndarray, np.ndarray | None]:
    """Reflect centred positions (and velocities) about the pitch centre. Arrays are (..., 2)."""
    sx, sy = d2_signs(g)
    s = np.array([sx, sy])
    xy = np.asarray(xy, dtype=float) * s
    return xy, (None if vxy is None else np.asarray(vxy, dtype=float) * s)


# ---------------------------------------------------------------- canonical frame

def possession_attacks_right(frame: TFrame) -> bool:
    return bool(frame.attacking_right.get(frame.possession_team_id, True))


def to_canonical_xy(xy: np.ndarray, attacking_right: bool) -> np.ndarray:
    """Absolute pitch metres (0..105, 0..68) -> centred, possession-attacks-+x."""
    out = np.asarray(xy, dtype=float) - np.array([HALF_L, HALF_W])
    return out if attacking_right else out * np.array([-1.0, 1.0])


def from_canonical_xy(xy: np.ndarray, attacking_right: bool) -> np.ndarray:
    out = np.asarray(xy, dtype=float)
    if not attacking_right:
        out = out * np.array([-1.0, 1.0])
    return out + np.array([HALF_L, HALF_W])


def to_canonical_vxy(vxy: np.ndarray, attacking_right: bool) -> np.ndarray:
    v = np.asarray(vxy, dtype=float)
    return v if attacking_right else v * np.array([-1.0, 1.0])


from_canonical_vxy = to_canonical_vxy  # a pure mirror is its own inverse


def _transform(frame: TFrame, flip: bool, poss: str) -> TFrame:
    """Centre ``frame`` (and its history) with a fixed mirror decision."""
    right = not flip

    def pt(x, y):
        a = to_canonical_xy(np.array([x, y]), right)
        return float(a[0]), float(a[1])

    def vel(vx, vy):
        if vx is None or vy is None:
            return vx, vy
        v = to_canonical_vxy(np.array([vx, vy]), right)
        return float(v[0]), float(v[1])

    players = []
    for p in frame.players:
        x, y = pt(p.x, p.y)
        vx, vy = vel(p.vx, p.vy)
        players.append(p.model_copy(update={"x": x, "y": y, "vx": vx, "vy": vy}))
    bx, by = pt(frame.ball.x, frame.ball.y)
    bvx, bvy = vel(frame.ball.vx, frame.ball.vy)
    others = {p.team_id for p in frame.players} - {poss}
    attacking = {poss: True, **{t: False for t in others}}
    return frame.model_copy(update={
        "players": players, "ball": TBall(x=bx, y=by, vx=bvx, vy=bvy), "attacking_right": attacking,
        "history": [_transform(h, flip, poss) for h in frame.history]})


def canonicalize(frame: TFrame) -> TFrame:
    """Centre the frame and mirror x so the possession team attacks +x.

    Input must be in absolute coordinates; the result is in the centred metric frame (it is NOT
    idempotent: calling it twice shifts twice). History frames use the same transform.
    """
    return _transform(frame, not possession_attacks_right(frame), frame.possession_team_id)


# ---------------------------------------------------------------- sample

@dataclass(frozen=True)
class GraphSample:
    pos: np.ndarray  # (T, N, 2) centred metres, canonical orientation then D2 element `group`
    vel: np.ndarray  # (T, N, 2) m/s
    vel_mask: np.ndarray  # (N,) 1 where the velocity at the last step is known
    node_feat: np.ndarray  # (N, F)
    edge_feat: np.ndarray  # (N, N, E)
    node_mask: np.ndarray  # (N,) 1 for real nodes (players present, ball, goals), 0 for "pad"
    node_ids: list[str]  # player ids, "ball", "goal_own", "goal_target", "pad"
    poss_slots: np.ndarray  # (11,) node indices of the possession team
    opp_slots: np.ndarray  # (11,) node indices of the opponent
    carrier_index: int  # node index of the ball carrier, -1 if none
    profile_keys: tuple[str, ...] = ()
    group: str = "e"  # D2 element applied on top of the canonical orientation
    flipped_x: bool = False  # canonicalisation mirrored x (possession team attacked left)
    possession_team_id: str = ""
    elapsed: float = 0.0

    @property
    def n_nodes(self) -> int:
        return self.pos.shape[1]


def _profile_dim(profile_keys: Sequence[str]) -> int:
    return 2 * len(profile_keys)


def node_feat_dim(profile_keys: Sequence[str] = ()) -> int:
    return F_BASE + _profile_dim(profile_keys)


def _slot_players(players: list[TPlayer], carrier_id: str | None) -> list[TPlayer]:
    ordered = sorted(players, key=lambda p: (0 if p.is_gk else 1 if p.player_id == carrier_id else 2,
                                             p.player_id))[:N_TEAM]
    return sorted(ordered, key=lambda p: (not p.is_gk, p.player_id))


def build_sample(frame: TFrame, profile_keys: tuple[str, ...] = (),
                 observed_ids: frozenset[str] | None = None) -> GraphSample:
    """Absolute-coordinate ``TFrame`` -> canonical ``GraphSample`` (T=5, N=25).

    ``observed_ids`` optionally restricts which present players count as observed (partial 360
    frames); unobserved players keep their (imputed) position but observed=0.
    """
    flip = not possession_attacks_right(frame)
    poss = frame.possession_team_id
    canon = _transform(frame, flip, poss)
    seq = (canon.history + [canon.model_copy(update={"history": []})])[-T_STEPS:]
    n_real = len(seq)
    P = len(profile_keys)

    poss_players = [p for p in canon.players if p.team_id == poss]
    opp_players = [p for p in canon.players if p.team_id != poss]
    slots: list[TPlayer | None] = []
    for plist in (poss_players, opp_players):
        chosen = _slot_players(plist, canon.carrier_id)
        slots.extend(chosen + [None] * (N_TEAM - len(chosen)))

    node_ids = [p.player_id if p else "pad" for p in slots] + ["ball", "goal_own", "goal_target"]
    node_mask = np.array([1.0 if p else 0.0 for p in slots] + [1.0, 1.0, 1.0])

    pos = np.zeros((n_real, N_NODES, 2))
    vel = np.zeros((n_real, N_NODES, 2))
    known = np.zeros((n_real, N_NODES), dtype=bool)
    prev_xy: dict[str, tuple[float, float]] = {}
    prev_el: float | None = None
    last_seen: dict[str, tuple[float, float]] = {}
    for t, fr in enumerate(seq):
        by_id = {p.player_id: p for p in fr.players}
        dt = (fr.elapsed - prev_el) if prev_el is not None else 0.0
        for i, nid in enumerate(node_ids[:BALL]):
            if nid == "pad":
                continue
            p = by_id.get(nid)
            if p is None:  # missing in this history frame: carry nearest known position
                cur = last_seen.get(nid)
                if cur is None:  # appears later only: use the current frame's position
                    q = next(q for q in canon.players if q.player_id == nid)
                    cur = (q.x, q.y)
                pos[t, i] = cur
                continue
            pos[t, i] = (p.x, p.y)
            last_seen[nid] = (p.x, p.y)
            if p.vx is not None and p.vy is not None:
                vel[t, i] = (p.vx, p.vy)
                known[t, i] = True
            elif nid in prev_xy and dt > 0:
                vel[t, i] = ((p.x - prev_xy[nid][0]) / dt, (p.y - prev_xy[nid][1]) / dt)
                known[t, i] = True
        pos[t, BALL] = (fr.ball.x, fr.ball.y)
        vel[t, BALL] = (fr.ball.vx, fr.ball.vy)
        known[t, BALL] = n_real > 1 or bool(fr.ball.vx or fr.ball.vy)
        prev_xy = {p.player_id: (p.x, p.y) for p in fr.players}
        prev_el = fr.elapsed
    # backfill unknown earlier velocities from the next known step (constant-velocity assumption)
    for t in range(n_real - 2, -1, -1):
        gap = ~known[t] & known[t + 1]
        vel[t][gap] = vel[t + 1][gap]
    vel_mask = known[-1].astype(float)

    pos[:, GOAL_OWN] = GOAL_XY[GOAL_OWN]
    pos[:, GOAL_TARGET] = GOAL_XY[GOAL_TARGET]
    if n_real < T_STEPS:  # repeat the earliest step at the front
        reps = T_STEPS - n_real
        pos = np.concatenate([np.repeat(pos[:1], reps, axis=0), pos], axis=0)
        vel = np.concatenate([np.repeat(vel[:1], reps, axis=0), vel], axis=0)

    node_feat = np.zeros((N_NODES, F_BASE + _profile_dim(profile_keys)))
    carrier_index = -1
    for i, p in enumerate(slots):
        if p is None:
            continue
        node_feat[i, F_IS_GK] = float(p.is_gk)
        node_feat[i, F_IS_CARRIER] = float(p.player_id == canon.carrier_id)
        node_feat[i, F_POSSESSION] = float(i < N_TEAM)
        node_feat[i, F_OBSERVED] = float(observed_ids is None or p.player_id in observed_ids)
        if p.player_id == canon.carrier_id:
            carrier_index = i
        for k, key in enumerate(profile_keys):
            if p.profile and key in p.profile:
                node_feat[i, F_BASE + k] = float(p.profile[key])
                node_feat[i, F_BASE + P + k] = 1.0
    node_feat[BALL, [F_IS_BALL, F_OBSERVED]] = 1.0
    node_feat[GOAL_OWN, [F_IS_GOAL_OWN, F_OBSERVED]] = 1.0
    node_feat[GOAL_TARGET, [F_IS_GOAL_TARGET, F_OBSERVED]] = 1.0
    node_feat[GOAL_OWN, F_POSSESSION] = 1.0  # the possession team's own goal

    edge_feat = _edges(pos[-1], node_mask)
    return GraphSample(
        pos=pos, vel=vel, vel_mask=vel_mask, node_feat=node_feat, edge_feat=edge_feat,
        node_mask=node_mask, node_ids=node_ids, poss_slots=np.arange(0, N_TEAM),
        opp_slots=np.arange(N_TEAM, 2 * N_TEAM), carrier_index=carrier_index,
        profile_keys=tuple(profile_keys), group="e", flipped_x=flip, possession_team_id=poss,
        elapsed=float(frame.elapsed))


def _edges(xy: np.ndarray, node_mask: np.ndarray) -> np.ndarray:
    n = xy.shape[0]
    e = np.zeros((n, n, N_EDGE))
    idx = np.arange(n)
    is_player = idx < BALL
    team = np.where(idx < N_TEAM, 0, 1)
    both = is_player[:, None] & is_player[None, :]
    e[..., E_SAME_TEAM] = both & (team[:, None] == team[None, :])
    e[..., E_OPPONENT] = both & (team[:, None] != team[None, :])
    e[..., E_INVOLVES_BALL] = (idx == BALL)[:, None] | (idx == BALL)[None, :]
    goal = idx >= GOAL_OWN
    e[..., E_INVOLVES_GOAL] = goal[:, None] | goal[None, :]
    e[..., E_DISTANCE] = np.linalg.norm(xy[:, None] - xy[None], axis=-1) / 10.0
    e *= (node_mask[:, None] * node_mask[None, :])[..., None]
    return e


def augment(sample: GraphSample, g: str) -> GraphSample:
    """Apply D2 element ``g`` to all geometry; node identity, node and edge features are unchanged."""
    pos, vel = apply_d2(g, sample.pos, sample.vel)
    return replace(sample, pos=pos, vel=vel, group=compose(sample.group, g))


def stack(samples: Sequence[GraphSample], dtype=np.float32) -> dict[str, np.ndarray]:
    """Collate samples into a batch, zero-padding N and F (``node_mask`` marks real nodes).

    Keys: pos (B,T,N,2), vel (B,T,N,2), vel_mask (B,N), node_feat (B,N,F), edge_feat (B,N,N,E),
    node_mask (B,N), node_ids (B,N) str, carrier_index (B,), poss_slots (B,11), opp_slots (B,11).
    """
    if not samples:
        raise ValueError("stack needs at least one sample")
    B = len(samples)
    T = max(s.pos.shape[0] for s in samples)
    N = max(s.n_nodes for s in samples)
    F = max(s.node_feat.shape[1] for s in samples)
    E = max(s.edge_feat.shape[2] for s in samples)
    out = {
        "pos": np.zeros((B, T, N, 2), dtype), "vel": np.zeros((B, T, N, 2), dtype),
        "vel_mask": np.zeros((B, N), dtype), "node_feat": np.zeros((B, N, F), dtype),
        "edge_feat": np.zeros((B, N, N, E), dtype), "node_mask": np.zeros((B, N), dtype),
        "node_ids": np.full((B, N), "pad", dtype=object),
        "carrier_index": np.array([s.carrier_index for s in samples], dtype=np.int64),
        "poss_slots": np.stack([s.poss_slots for s in samples]),
        "opp_slots": np.stack([s.opp_slots for s in samples]),
    }
    for b, s in enumerate(samples):
        t, n = s.pos.shape[0], s.n_nodes
        out["pos"][b, T - t:, :n] = s.pos
        out["vel"][b, T - t:, :n] = s.vel
        out["vel_mask"][b, :n] = s.vel_mask
        out["node_feat"][b, :n, :s.node_feat.shape[1]] = s.node_feat
        out["edge_feat"][b, :n, :n, :s.edge_feat.shape[2]] = s.edge_feat
        out["node_mask"][b, :n] = s.node_mask
        out["node_ids"][b, :n] = s.node_ids
    return out

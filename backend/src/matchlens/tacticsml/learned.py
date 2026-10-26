"""The learned tactical model, run in numpy from exported weights (no torch needed at serve time).

* ``score`` / ``score_batch``: mean over the seed ensemble of the D2 frame-averaged GATv2 critic; the spread
  between members is the uncertainty (a single member falls back to the binomial spread of the baseline).
* ``propose``: samples repositionings from the cVAE decoder, caps them by speed and ``max_move_m``, ranks
  them by the critic, then drops moves that do not pay for themselves. ``both_teams`` lets the other side
  answer with its best sampled reply. Without a cVAE the baseline proposer answers.
* ``similar``: cosine retrieval of real moments (``retrieval``), ``[]`` without an index.

Weights live in ``MATCHLENS_TACTICS_WEIGHTS`` if set, else ``backend/data/tacticsml/weights`` (shippable) or
``backend/data/local/tacticsml/weights`` (``train export`` output). ``weights_available()`` checks the
manifest and the sha256 of every file, so a half-copied directory falls back to the baseline instead of
failing in the registry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH
from matchlens.tacticsml import features as F
from matchlens.tacticsml.contract import (
    Intent,
    Proposal,
    RetrievedMoment,
    ScoreResult,
    TFrame,
)
from matchlens.tacticsml.infer_numpy import (
    N_SLOTS,
    TARGETS,
    TEAMS,
    CvaeDecoder,
    Ensemble,
    GatNet,
)
from matchlens.tacticsml.retrieval import INDEX_FILE, RetrievalIndex, descriptor, load_index

log = logging.getLogger(__name__)

ENV_WEIGHTS = "MATCHLENS_TACTICS_WEIGHTS"
BACKEND_DIR = Path(__file__).resolve().parents[3]
CANDIDATE_DIRS = (BACKEND_DIR / "data" / "tacticsml" / "weights",
                  BACKEND_DIR / "data" / "local" / "tacticsml" / "weights")
MANIFEST = "manifest.json"
VERSION_PREFIX = "learned-gat-v1"

CHUNK = 32  # graphs per numpy forward pass
PITCH_MARGIN = 0.5
GK_MAX_MOVE_M = 6.0
MIN_MOVE_M = 0.5  # smaller displacements are not worth showing
PRUNE_EPS = 2e-4


def weights_dir() -> Path | None:
    """The first directory that holds a manifest (``MATCHLENS_TACTICS_WEIGHTS`` wins)."""
    env = os.getenv(ENV_WEIGHTS)
    for d in ([Path(env)] if env else []) + list(CANDIDATE_DIRS):
        if (d / MANIFEST).is_file():
            return d
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_hash(manifest: dict) -> str:
    hashes = [m["sha256"] for m in manifest["members"]] + ([manifest["cvae"]["sha256"]] if manifest.get("cvae") else [])
    return hashlib.sha256("|".join(sorted(hashes)).encode()).hexdigest()


def _check_entry(entry: Any, what: str) -> None:
    if not (isinstance(entry, dict) and isinstance(entry.get("file"), str) and isinstance(entry.get("sha256"), str)):
        raise ValueError(f"manifest {what} needs string 'file' and 'sha256'")
    if Path(entry["file"]).name != entry["file"]:
        raise ValueError(f"manifest {what} file {entry['file']!r} must be a bare file name")


def _read_manifest(d: Path, verify: bool = True) -> dict:
    """The manifest of weight directory ``d``; ``ValueError`` (or ``OSError``) when it is unusable."""
    manifest = json.loads((d / MANIFEST).read_text())
    if not isinstance(manifest, dict):
        raise ValueError("manifest is not a JSON object")
    members, cvae = manifest.get("members"), manifest.get("cvae")
    if not isinstance(members, list) or not members:
        raise ValueError("manifest lists no ensemble members")
    for m in members:
        _check_entry(m, "member")
    if cvae is not None:
        _check_entry(cvae, "cvae")
    for m in members + ([cvae] if cvae else []):
        path = d / m["file"]
        if not path.is_file():
            raise FileNotFoundError(path)
        if verify and _sha256(path) != m["sha256"]:
            raise ValueError(f"{m['file']}: sha256 differs from the manifest")
    manifest["hash"] = _manifest_hash(manifest)
    return manifest


def weights_available(path: Path | str | None = None) -> bool:
    """True when a complete weight set can be loaded: valid manifest, matching hashes, readable npz files.

    Never raises; the registry relies on that to fall back to the baseline.
    """
    try:
        d = Path(path) if path is not None else weights_dir()
        if d is None or not (d / MANIFEST).is_file():
            return False
        LearnedModel.load(d)  # cheap (a few MB); catches garbage npz files whose hash still matches
        return True
    except Exception as exc:  # noqa: BLE001 - any defect means "use the baseline"
        log.warning("tactical weights unusable: %s: %s", type(exc).__name__, exc)
        return False


# ---------------------------------------------------------------- frames

def _move_frame(frame: TFrame, moves: dict[str, tuple[float, float]]) -> TFrame:
    """Move players to new absolute positions in every history step (as the cVAE was trained), zero their
    last velocity, and carry the ball along with a moved carrier that has it at his feet."""
    by_id = {p.player_id: p for p in frame.players}
    delta: dict[str, tuple[float, float]] = {}
    for pid, (nx, ny) in moves.items():
        p = by_id.get(pid)
        if p is not None:
            delta[pid] = (float(np.clip(nx, 0.0, PITCH_LENGTH)) - p.x, float(np.clip(ny, 0.0, PITCH_WIDTH)) - p.y)
    if not delta:
        return frame
    bdx = bdy = 0.0
    c = by_id.get(frame.carrier_id) if frame.carrier_id else None
    if c is not None and c.player_id in delta and (c.x - frame.ball.x) ** 2 + (c.y - frame.ball.y) ** 2 < 4.0:
        bdx, bdy = delta[c.player_id]

    def shift(fr: TFrame, last: bool) -> TFrame:
        players = []
        for p in fr.players:
            dx, dy = delta.get(p.player_id, (0.0, 0.0))
            if p.player_id in delta:
                upd: dict[str, Any] = {"x": p.x + dx, "y": p.y + dy}
                if last:
                    upd.update(vx=0.0, vy=0.0)
                p = p.model_copy(update=upd)
            players.append(p)
        ball = fr.ball.model_copy(update={"x": fr.ball.x + bdx, "y": fr.ball.y + bdy}) if bdx or bdy else fr.ball
        return fr.model_copy(update={"players": players, "ball": ball})

    return shift(frame, True).model_copy(update={"history": [shift(h, False) for h in frame.history]})


# ---------------------------------------------------------------- model

class LearnedModel:
    """``TacticalModel`` backed by exported numpy weights."""

    def __init__(self, ensemble: Ensemble, cvae: CvaeDecoder | None, manifest: dict, directory: Path,
                 index: RetrievalIndex | None = None):
        self.ensemble = ensemble
        self.cvae = cvae
        self.manifest = manifest
        self.directory = directory
        self.profile_keys = ensemble.profile_keys
        self.n_node_feat = ensemble.members[0].cfg.n_node_feat
        self.weights_hash: str = manifest["hash"]
        self.version = f"{VERSION_PREFIX}/{self.weights_hash[:8]}"
        enc = {m["file"]: i for i, m in enumerate(manifest["members"])}.get(
            (manifest.get("cvae") or {}).get("encoder_member", ""), 0)
        self._encoder: GatNet = ensemble.members[enc]
        self.index = index if index is not None and self._index_ok(index) else None
        self._baseline: Any = None

    @classmethod
    def load(cls, path: Path | str | None = None, dtype=np.float32) -> "LearnedModel":
        d = Path(path) if path is not None else weights_dir()
        if d is None:
            raise FileNotFoundError(f"no tactical weights found (set {ENV_WEIGHTS} or run `train export`)")
        manifest = _read_manifest(d)
        ens = Ensemble.load([d / m["file"] for m in manifest["members"]], dtype)
        cvae = CvaeDecoder.load(d / manifest["cvae"]["file"], dtype) if manifest.get("cvae") else None
        return cls(ens, cvae, manifest, d, load_index(d / INDEX_FILE))

    def _index_ok(self, index: RetrievalIndex) -> bool:
        if index.kind == "critic" and index.model_hash != self.weights_hash:
            log.warning("retrieval index was built from other weights; ignoring it (re-run `train export`)")
            return False
        return True

    # ---- batches -------------------------------------------------------------------------------------------

    def _samples(self, frames: list[TFrame]) -> list[F.GraphSample]:
        return [F.build_sample(f, self.profile_keys) for f in frames]

    def _predict(self, samples: list[F.GraphSample]) -> dict[str, np.ndarray]:
        """Member-stacked probabilities for graph samples, chunked."""
        parts = [self.ensemble.predict(F.stack(samples[a:a + CHUNK]), CHUNK) for a in range(0, len(samples), CHUNK)]
        return {k: np.concatenate([p[k] for p in parts], axis=1) for k in parts[0]}

    def embed_batch(self, batch: dict[str, np.ndarray]) -> np.ndarray:
        """Critic embeddings (member 0, D2 averaged) of a ``features.stack`` / shard batch."""
        n = int(batch["pos"].shape[0])
        keys = ("pos", "vel", "vel_mask", "node_feat", "edge_feat", "node_mask")
        return np.concatenate([self.ensemble.members[0].predict({k: batch[k][a:a + CHUNK] for k in keys}, CHUNK
                                                                )["embedding"] for a in range(0, n, CHUNK)])

    # ---- scoring -------------------------------------------------------------------------------------------

    def _results(self, samples: list[F.GraphSample], pred: dict[str, np.ndarray]) -> list[ScoreResult]:
        outc = pred["outcomes"]  # (M,B,4): shot10, goal10, concede10, turn5
        recv = pred["receiver_probs"].mean(0)
        mean = outc.mean(0)
        if outc.shape[0] > 1:
            std = outc.std(0, ddof=1)
        else:  # one member: no spread to measure, use the binomial one
            std = 0.25 * np.sqrt(mean * (1.0 - mean))
        emb = pred["embedding"][0]
        out = []
        for b, s in enumerate(samples):
            probs: dict[str, float] = {}
            for i in range(N_SLOTS):
                nid = s.node_ids[i]
                if nid != "pad" and s.node_mask[i] > 0 and i != s.carrier_index:
                    probs[nid] = float(recv[b, i])
            tot = sum(probs.values())
            probs = ({k: v / tot for k, v in probs.items()} if tot > 1e-9
                     else {k: 1.0 / len(probs) for k in probs})
            shot, goal, conc, turn = (float(x) for x in mean[b])
            out.append(ScoreResult(
                receiver_probs={k: round(v, 5) for k, v in probs.items()},
                shot_prob=round(shot, 5), shot_std=round(float(std[b, 0]), 5), goal_prob=round(goal, 5),
                concede_prob=round(conc, 5), value=round(goal - conc, 5), turnover_prob=round(turn, 5),
                turnover_std=round(float(std[b, 3]), 5), embedding=[round(float(x), 4) for x in emb[b]],
                model_version=self.version))
        return out

    def _score_frames(self, frames: list[TFrame], with_control: bool = False) -> list[ScoreResult]:
        if not frames:
            return []
        samples = self._samples(frames)
        results = self._results(samples, self._predict(samples))
        if with_control:
            from matchlens.tacticsml.pitch_control import pitch_control

            results = [r.model_copy(update={"pitch_control": np.round(pitch_control(f)[2], 3).tolist()})
                       for r, f in zip(results, frames, strict=True)]
        return results

    def score(self, frame: TFrame, intent: Intent | None = None, reference: TFrame | None = None) -> ScoreResult:
        moved = _move_frame(frame, intent.moves) if intent and intent.moves else frame
        if reference is not None:
            ref = reference
        elif moved is not frame:
            ref = frame
        else:
            ref = None
        res = self._score_frames([moved] + ([ref] if ref is not None else []))
        delta = round(res[0].value - res[1].value, 5) if ref is not None else 0.0
        return res[0].model_copy(update={"xt_delta": delta, "pitch_control": self._control(moved)})

    @staticmethod
    def _control(frame: TFrame) -> list[list[float]]:
        from matchlens.tacticsml.pitch_control import pitch_control

        return np.round(pitch_control(frame)[2], 3).tolist()

    def score_batch(self, frames: list[TFrame]) -> list[ScoreResult]:
        return self._score_frames(frames)

    # ---- retrieval -----------------------------------------------------------------------------------------

    def similar(self, frame: TFrame, k: int = 5) -> list[RetrievedMoment]:
        if self.index is None:
            return []
        batch = F.stack([F.build_sample(frame, self.profile_keys)])
        if self.index.kind == "critic":
            raw = self.embed_batch(batch)[0]
        else:
            raw = descriptor(batch)[0]
        return self.index.search(raw, k)

    # ---- proposals -----------------------------------------------------------------------------------------

    def _fallback(self):
        if self._baseline is None:
            from matchlens.tacticsml.baseline import BaselineModel

            self._baseline = BaselineModel()
        return self._baseline

    @staticmethod
    def _objective(res: ScoreResult, target: str, team_is_poss: bool) -> float:
        """Value of a result for the team asking (higher is better for that team), as in the baseline."""
        sign = 1.0 if team_is_poss else -1.0
        if target == "shot":
            return sign * res.shot_prob
        if target in ("retain", "press"):
            return -sign * res.turnover_prob
        if target.startswith("receiver:"):
            p = res.receiver_probs.get(target.split(":", 1)[1], 0.0)
            return p if team_is_poss else -p
        raise ValueError(f"unknown target {target!r}")

    def _sample_moves(self, frame: TFrame, sample: F.GraphSample, runs: list[dict], team_mode: str,
                      cond_target: str, receiver_node: int, locked_ids: frozenset[str], max_move_m: float,
                      n: int, rng: np.random.Generator, only: set[str] | None = None
                      ) -> list[dict[str, tuple[float, float]]]:
        """``n`` move sets (absolute metres) for the nodes of ``team_mode`` from the cVAE decoder."""
        N = sample.n_nodes
        by_id = {p.player_id: p for p in frame.players}
        locked = np.array([[1.0 if (nid in locked_ids or (only is not None and nid not in only)) else 0.0
                            for nid in sample.node_ids]])
        speed = np.array([[by_id[nid].top_speed if nid in by_id else self.cvae.cfg.default_top_speed
                           for nid in sample.node_ids]])
        radius = self.cvae.radius(speed, (1, N), max_move_m)
        for i, nid in enumerate(sample.node_ids[:F.BALL]):  # goalkeepers move less
            if nid in by_id and by_id[nid].is_gk:
                radius[0, i] = min(radius[0, i], GK_MAX_MOVE_M)
        z = rng.standard_normal((n, N, self.cvae.cfg.z_dim)).astype(np.float32)
        disp = self.cvae.decode(runs, sample.pos[-1][None], sample.node_mask[None], np.array([TARGETS.index(cond_target)]),
                                np.array([TEAMS.index(team_mode)]), np.array([receiver_node]), locked, z, radius)
        if sample.flipped_x:  # canonical -> absolute orientation
            disp = disp * np.array([-1.0, 1.0])
        out: list[dict[str, tuple[float, float]]] = []
        for j in range(n):
            moves: dict[str, tuple[float, float]] = {}
            for i, nid in enumerate(sample.node_ids[:F.BALL]):
                p = by_id.get(nid)
                if p is None or float(np.hypot(*disp[j, i])) < MIN_MOVE_M:
                    continue
                moves[nid] = (float(np.clip(p.x + disp[j, i, 0], PITCH_MARGIN, PITCH_LENGTH - PITCH_MARGIN)),
                              float(np.clip(p.y + disp[j, i, 1], PITCH_MARGIN, PITCH_WIDTH - PITCH_MARGIN)))
            out.append(moves)
        return out

    def propose(self, frame: TFrame, team_id: str, target: str, k: int = 3, max_move_m: float = 8.0,
                locked: frozenset[str] = frozenset(), both_teams: bool = False) -> list[Proposal]:
        """Up to ``k`` distinct repositionings of ``team_id`` that raise ``target`` (read from ``team_id``'s
        side, as in the baseline). Falls back to the baseline proposer when no cVAE was exported."""
        if self.cvae is None:
            return self._fallback().propose(frame, team_id, target, k, max_move_m, locked, both_teams)
        team_is_poss = team_id == frame.possession_team_id
        self._objective(ScoreResult(receiver_probs={}, shot_prob=0, model_version=""), target, team_is_poss)  # validate
        sample = F.build_sample(frame, self.profile_keys)
        ids = sample.node_ids
        slots = range(0, F.N_TEAM) if team_is_poss else range(F.N_TEAM, 2 * F.N_TEAM)
        own = {ids[i] for i in slots if ids[i] != "pad"}
        if not (own - set(locked)):
            return []
        rng = np.random.default_rng(zlib.crc32(f"{team_id}|{target}|{frame.elapsed:.3f}".encode()))
        batch = F.stack([sample])
        runs = self._encoder.run_d2(batch)
        by_id = {p.player_id: p for p in frame.players}
        receiver_node = -1
        if team_is_poss and target.startswith("receiver:") and target.split(":", 1)[1] in ids[:F.N_TEAM]:
            receiver_node = ids.index(target.split(":", 1)[1])
        if team_is_poss:
            cond = "shot" if target == "shot" else "receiver" if receiver_node >= 0 else "retain"
        else:
            cond = "press"
        mode = "attack" if team_is_poss else "defence"

        base = self._score_frames([frame])[0]
        base_obj = self._objective(base, target, team_is_poss)
        n = min(64, max(32, 8 * k))
        cands = [m for m in self._sample_moves(frame, sample, runs, mode, cond, receiver_node, locked, max_move_m,
                                               n, rng) if m]
        if not cands:
            return []
        scored = self._score_frames([_move_frame(frame, m) for m in cands])
        objs = [self._objective(r, target, team_is_poss) for r in scored]
        order = [i for i in np.argsort(objs)[::-1] if objs[i] - base_obj > 1e-4][: 2 * k + 2]

        # drop the moves that do not pay for themselves, keep the five that matter most
        pruned: list[dict[str, tuple[float, float]]] = []
        for i in order:
            m = cands[i]
            loo = [{p: xy for p, xy in m.items() if p != q} for q in m]
            rs = self._score_frames([_move_frame(frame, x) for x in loo])
            contrib = {q: objs[i] - self._objective(r, target, team_is_poss) for q, r in zip(m, rs, strict=True)}
            keep = sorted((q for q in m if contrib[q] > PRUNE_EPS), key=lambda q: -contrib[q])[:5]
            if keep:
                pruned.append({q: m[q] for q in keep})
        if not pruned:
            return []
        fin = self._score_frames([_move_frame(frame, m) for m in pruned])
        fin_obj = [self._objective(r, target, team_is_poss) for r in fin]

        replies: list[dict[str, tuple[float, float]]] = [{} for _ in pruned]
        if both_teams:
            replies = self._best_replies(frame, sample, runs, pruned, team_is_poss, target, locked, max_move_m, rng,
                                         fin_obj)

        full = [{**m, **r} for m, r in zip(pruned, replies, strict=True)]
        res = self._score_frames([_move_frame(frame, m) for m in full], with_control=True)
        ranked = sorted(range(len(full)), key=lambda i: -self._objective(res[i], target, team_is_poss))
        chosen: list[int] = []
        for i in ranked:
            if self._objective(res[i], target, team_is_poss) - base_obj < 1e-4:
                continue
            if any(self._moves_distance(full[i], full[j], by_id) < 2.0 for j in chosen):
                continue
            chosen.append(i)
            if len(chosen) == k:
                break
        proposals = []
        for i in chosen:
            r = res[i]
            gain = {name: round(getattr(r, name) - getattr(base, name), 5)
                    for name in ("shot_prob", "goal_prob", "concede_prob", "turnover_prob", "value")}
            gain["objective"] = round(self._objective(r, target, team_is_poss) - base_obj, 5)
            proposals.append(Proposal(moves={p: (round(x, 2), round(y, 2)) for p, (x, y) in full[i].items()},
                                      score=r.model_copy(update={"xt_delta": round(r.value - base.value, 5)}),
                                      gain=gain))
        return proposals

    def _best_replies(self, frame: TFrame, sample: F.GraphSample, runs: list[dict], moves: list[dict],
                      team_is_poss: bool, target: str, locked: frozenset[str], max_move_m: float,
                      rng: np.random.Generator, own_obj: list[float]) -> list[dict[str, tuple[float, float]]]:
        """For each candidate the sampled reply (two players near the ball) that hurts ``target`` most."""
        opp_mode = "defence" if team_is_poss else "attack"
        replies = self._sample_moves(frame, sample, runs, opp_mode, "press" if team_is_poss else "shot", -1, locked,
                                    max_move_m, 12, rng)
        ball = np.array([frame.ball.x, frame.ball.y])
        by_id = {p.player_id: p for p in frame.players}
        trimmed = []
        for rp in replies:  # at most two movers, the ones closest to the ball
            near = sorted(rp, key=lambda q: float(np.hypot(by_id[q].x - ball[0], by_id[q].y - ball[1])))[:2]
            trimmed.append({q: rp[q] for q in near})
        trimmed = [r for r in trimmed if r]
        if not trimmed:
            return [{} for _ in moves]
        frames = [_move_frame(frame, {**m, **r}) for m in moves for r in trimmed]
        objs = np.array([self._objective(x, target, team_is_poss) for x in self._score_frames(frames)]
                        ).reshape(len(moves), len(trimmed))
        out = []
        for i in range(len(moves)):
            j = int(np.argmin(objs[i]))
            out.append(trimmed[j] if objs[i, j] < own_obj[i] else {})
        return out

    @staticmethod
    def _moves_distance(a: dict[str, tuple[float, float]], b: dict[str, tuple[float, float]],
                        by_id: dict[str, Any]) -> float:
        ids = set(a) | set(b)
        total = 0.0
        for pid in ids:
            p = by_id[pid]
            pa, pb = a.get(pid, (p.x, p.y)), b.get(pid, (p.x, p.y))
            total += float(np.hypot(pa[0] - pb[0], pa[1] - pb[1]))
        return total / max(len(ids), 1)

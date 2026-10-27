"""Rule-based tactic ideas built from the players actually on the pitch.

``recipes`` turns a frozen ``Situation`` into ``TacticIntent`` cards a coach would recognise: a fullback
overlap and cross, a third-man run, a switch to the weak side, a high-press trap, a near-post corner and
so on. Each recipe says when it applies (possession, phase, where the ball is), picks real players by
role and position, and fills the same intent a model would write. They serve as instant cards, as the
offline template when no model is reachable, and as baseline variety to compare model ideas against.

Every recipe cites ``playbook:<recipe_id>``; ``PLAYBOOK_KEYS`` lists them so a context can offer them.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from matchlens.domain.models import MatchMeta
from matchlens.domain.pitch import to_relative
from matchlens.tactics.intent import (
    Action,
    BallAction,
    BallStep,
    Mechanism,
    PressPlan,
    PressTrigger,
    RoleDirective,
    StyleChange,
    TacticIntent,
)
from matchlens.tactics.models import PlayerSpot, Situation

MAX_PASS_M = 40.0  # recipes keep ground passes inside the validator's 45 m limit with room to spare

_FULLBACK = {"LB", "RB", "LWB", "RWB"}
_WING = {"LW", "RW", "LM", "RM"}
_STRIKER = {"ST", "LST", "RST", "CF"}
_DM = {"LDM", "RDM", "CDM", "DM"}
_CM = {"LCM", "RCM", "CM"}
_AM = {"LAM", "RAM", "CAM", "AM"}


@dataclass(frozen=True)
class _P:
    """A player in the coached team's frame of reference: x towards the opposition goal, y=68 on the left."""

    pid: str
    name: str
    kind: str  # gk | cb | fb | dm | cm | am | wing | st
    x: float
    y: float

    @property
    def side(self) -> str:
        return "left" if self.y > 40 else "right" if self.y < 28 else "centre"


def _kind(spot: PlayerSpot, x: float) -> str:
    role = spot.role.upper()
    if role == "GK":
        return "gk"
    if role in _FULLBACK:
        return "fb"
    if "CB" in role:
        return "cb"
    if role in _WING:
        return "wing"
    if role in _STRIKER:
        return "st"
    if role in _DM:
        return "dm"
    if role in _CM:
        return "cm"
    if role in _AM:
        return "am"
    return "cb" if x < 25 else "cm" if x < 50 else "am" if x < 65 else "st"


def _lane(y: float) -> str:
    return "left" if y >= 54 else "left_half" if y >= 40 else "centre" if y >= 28 else "right_half" if y >= 14 else "right"


def _third(x: float) -> str:
    return "def" if x < 28 else "mid" if x < 56 else "att" if x < 80 else "final"


def _dist(a: _P, b: _P) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


@dataclass
class _Ctx:
    team: str
    horizon: float
    possession: bool
    phase: str
    mates: list[_P]
    opps: list[_P]
    carrier: _P | None  # the coached team's ball carrier (in possession), else the opposition's
    ball: tuple[float, float]  # team-relative
    side: str  # the side the ball is on: left | right | centre
    attack_side: str  # left | right: where to attack (the ball side, or the emptier flank when central)
    used: set[str] = field(default_factory=set)

    @property
    def outfield(self) -> list[_P]:
        return [p for p in self.mates if p.kind != "gk"]

    def pick(self, kinds: tuple[str, ...], side: str | None = None, near: _P | None = None,
             min_x: float | None = None, exclude: tuple[str, ...] = (),
             within: _P | None = None) -> _P | None:
        """The nearest unused team-mate of the given kinds (to ``near``, default the ball), optionally one a
        ground pass from ``within`` can reach."""
        anchor = (near.x, near.y) if near else self.ball
        pool = [p for p in self.mates if p.kind in kinds and p.pid not in self.used and p.pid not in exclude
                and (side is None or p.side == side) and (min_x is None or p.x >= min_x)
                and (within is None or _dist(within, p) <= MAX_PASS_M)]
        if not pool:
            return None
        return min(pool, key=lambda p: (math.hypot(p.x - anchor[0], p.y - anchor[1]), p.pid))

    def take(self, p: _P | None) -> _P | None:
        if p is not None:
            self.used.add(p.pid)
        return p

    def opp_near(self, p: _P | tuple[float, float], n: int = 1, exclude: tuple[str, ...] = (),
                 skip_gk: bool = True) -> list[_P]:
        x, y = (p.x, p.y) if isinstance(p, _P) else p
        pool = [o for o in self.opps if o.pid not in exclude and not (skip_gk and o.kind == "gk")]
        return sorted(pool, key=lambda o: (math.hypot(o.x - x, o.y - y), o.pid))[:n]

    def d(self, p: _P, action: Action, zone: str | None = None, target: _P | None = None,
          start: float = 0.0, end: float = 5.0) -> RoleDirective:
        self.used.add(p.pid)
        return RoleDirective(player_id=p.pid, action=action, zone=zone,
                             target_player_id=target.pid if target else None,
                             start_s=min(start, max(0.0, self.horizon - 1.0)), end_s=min(end, self.horizon))


def _step(action: BallAction, frm: _P | None, to: _P | None = None, zone: str | None = None) -> BallStep:
    return BallStep(action=action, from_player_id=frm.pid if frm else None,
                    to_player_id=to.pid if to else None, to_zone=zone)


def _pass_action(a: _P, b: _P) -> BallAction:
    return BallAction.LONG_BALL if _dist(a, b) > 30.0 else BallAction.PASS


def _intent(c: _Ctx, rid: str, title: str, idea: str, mechanism: Mechanism, directives: list[RoleDirective],
            ball_plan: list[BallStep], *, press: PressPlan | None = None,
            style: list[tuple[str, float]] | None = None, rest: int = 2) -> TacticIntent:
    return TacticIntent(
        team_id=c.team, title=title[:60], idea=idea[:280], mechanism=mechanism, directives=directives[:6],
        ball_plan=ball_plan[:4], press=press,
        style_changes=[StyleChange(knob=k, delta=v) for k, v in (style or [])][:4],
        rest_defence=rest, cites=[f"playbook:{rid}"],
    )


def _reach(a: _P, b: _P) -> bool:
    return _dist(a, b) <= MAX_PASS_M


# ============================================================== in possession


def _overlap_cross(c: _Ctx) -> TacticIntent | None:
    if c.carrier is None or not 30 <= c.ball[0] <= 92:
        return None
    s = c.attack_side
    fb = c.pick(("fb", "wing"), side=s, exclude=(c.carrier.pid,))
    if fb is None or not _reach(c.carrier, fb) or c.carrier.kind == "fb" and c.carrier.side == s:
        return None
    st = c.pick(("st", "am"), near=fb, exclude=(fb.pid, c.carrier.pid), within=fb)
    if st is None:
        return None
    c.take(fb)
    c.take(c.carrier)
    ds = [c.d(fb, Action.OVERLAP, f"{s}:final", start=0.5, end=4.5),
          c.d(st, Action.ATTACK_BOX, "near_post", start=1.0, end=6.0)]
    far = c.pick(("wing", "am", "cm"), side="left" if s == "right" else "right", exclude=(st.pid,))
    if far is not None:
        ds.append(c.d(far, Action.ATTACK_BOX, "far_post", start=1.5, end=6.0))
    arriving = c.pick(("cm", "am", "dm"), exclude=(st.pid,))
    if arriving is not None:
        ds.append(c.d(arriving, Action.ATTACK_BOX, "edge_of_box", start=2.0, end=6.0))
    plan = [_step(BallAction.PASS, c.carrier, fb), _step(BallAction.CROSS, fb, st, "box")]
    return _intent(c, "overlap_cross", "Fullback overlap and cross",
                   f"{fb.name} runs around the outside of the ball carrier, takes the pass and crosses for "
                   f"{st.name} attacking the near post, with a late runner at the far post.",
                   Mechanism.OVERLOAD, ds, plan, style=[("crossing", 0.4)])


def _third_man(c: _Ctx) -> TacticIntent | None:
    car = c.carrier
    if car is None or car.kind == "gk" and car.x > 20 or car.x > 85:
        return None
    a_pool = [p for p in c.outfield if p.pid != car.pid and 6 <= _dist(car, p) <= 28 and p.x >= car.x - 6
              and p.kind in ("cm", "am", "st", "wing", "dm")]
    if not a_pool:
        return None
    a = max(a_pool, key=lambda p: (p.x - 0.3 * _dist(car, p), p.pid))
    runners = [p for p in c.outfield if p.pid not in (car.pid, a.pid) and p.x >= a.x + 8 and _dist(a, p) <= MAX_PASS_M
               and p.kind in ("st", "am", "wing", "cm")]
    if not runners:
        return None
    run = min(runners, key=lambda p: (_dist(a, p), p.pid))
    b_pool = [p for p in c.outfield if p.pid not in (car.pid, a.pid, run.pid) and _dist(a, p) <= 22
              and _dist(p, run) <= MAX_PASS_M]
    b = min(b_pool, key=lambda p: (_dist(a, p), p.pid)) if b_pool else None
    c.take(car)
    c.take(a)
    if b is not None:
        c.take(b)
    ds = [c.d(run, Action.RUN_TO, f"{_lane(run.y)}:final", start=0.5, end=6.0)]
    if a.kind in ("st", "am"):
        ds.append(c.d(a, Action.DROP_DEEP, start=0.0, end=3.0))
    plan = [_step(_pass_action(car, a), car, a)]
    if b is not None:
        plan += [_step(BallAction.PASS, a, b), _step(BallAction.PASS, b, run, f"{_lane(run.y)}:final")]
    else:
        plan.append(_step(BallAction.PASS, a, run, f"{_lane(run.y)}:final"))
    via = f", who lays it off to {b.name}," if b is not None else ""
    return _intent(c, "third_man", "Third-man run",
                   f"{car.name} plays to {a.name}{via} and the ball goes beyond the first line to {run.name}, "
                   "who has already started his run.", Mechanism.THIRD_MAN, ds, plan,
                   style=[("verticality", 0.2)])


def _switch(c: _Ctx) -> TacticIntent | None:
    car = c.carrier
    if car is None or c.side == "centre" or not 25 <= c.ball[0] <= 85:
        return None
    crowd = sum(math.hypot(o.x - c.ball[0], o.y - c.ball[1]) <= 18 for o in c.opps)
    if crowd < 3:
        return None
    far_side = "right" if c.side == "left" else "left"
    far = c.pick(("wing", "fb"), side=far_side, exclude=(car.pid,))
    if far is None or _dist(car, far) < 20:
        return None
    c.take(car)
    c.take(far)
    zone = f"{far_side}:att"
    ds = [c.d(far, Action.HOLD_WIDTH, zone, start=0.0, end=6.0)]
    fb = c.pick(("fb",), side=far_side)
    if fb is not None and far.kind == "wing":
        ds.append(c.d(fb, Action.OVERLAP, f"{far_side}:final", start=2.0, end=6.0))
    st = c.pick(("st", "am"), exclude=(far.pid,), within=far)
    plan = [_step(BallAction.SWITCH, car, far, zone)]
    if st is not None:
        ds.append(c.d(st, Action.ATTACK_BOX, "near_post", start=1.5, end=6.0))
        plan.append(_step(BallAction.CROSS, far, st, "box"))
    return _intent(c, "switch_weak_side", "Switch to the weak side",
                   f"The ball side is crowded, so {car.name} switches to {far.name}, who is holding the width "
                   "on the far flank with space to attack.", Mechanism.SWITCH, ds, plan,
                   style=[("width_in_poss", 0.1)])


def _run_in_behind(c: _Ctx) -> TacticIntent | None:
    car = c.carrier
    if car is None or not 30 <= c.ball[0] <= 80:
        return None
    st = c.pick(("st",), exclude=(car.pid,))
    wing = c.pick(("wing", "am", "fb"), side=c.attack_side, near=st, exclude=(car.pid,)) if st else None
    if st is None or wing is None or not _reach(car, wing) and _dist(car, wing) > 55:
        return None
    pin = c.opp_near(st, 1, skip_gk=True)
    if not pin:
        return None
    c.take(car)
    ds = [c.d(st, Action.PIN_DEFENDER, target=pin[0], start=0.0, end=5.0),
          c.d(wing, Action.RUN_TO, f"{_lane(wing.y) if wing.side != 'centre' else c.attack_side}:final",
              start=0.5, end=5.0)]
    mid = c.pick(("cm", "am", "dm"), min_x=c.ball[0] - 5, exclude=(st.pid, wing.pid), within=wing)
    plan = [_step(_pass_action(car, wing), car, wing, f"{c.attack_side}:final")]
    if mid is not None:
        ds.append(c.d(mid, Action.ATTACK_BOX, "cutback_spot", start=2.0, end=6.0))
        plan.append(_step(BallAction.CUTBACK, wing, mid, "cutback_spot"))
    else:
        plan.append(_step(BallAction.SHOOT, wing))
    return _intent(c, "pin_and_run", "Striker pins, winger runs in behind",
                   f"{st.name} pins {pin[0].name} to keep the back line deep while {wing.name} runs into the "
                   "space behind and looks for the cutback.", Mechanism.RUN_IN_BEHIND, ds, plan,
                   style=[("directness", 0.2)])


def _cutback_overload(c: _Ctx) -> TacticIntent | None:
    car = c.carrier
    if car is None or c.ball[0] < 55:
        return None
    s = c.attack_side
    w = c.pick(("wing", "fb", "am"), side=s) or None
    if w is None or c.ball[0] < 55 and w.pid != car.pid:
        return None
    if w.pid != car.pid and not _reach(car, w):
        return None
    m = c.pick(("cm", "am", "dm"), near=w, exclude=(w.pid, car.pid), within=w)
    if m is None:
        return None
    c.take(w)
    c.take(m)
    plan: list[BallStep] = []
    if w.pid != car.pid:
        plan.append(_step(BallAction.PASS, car, w))
    plan += [_step(BallAction.CARRY, w, zone=f"{s}:final"), _step(BallAction.CUTBACK, w, m, "cutback_spot")]
    if len(plan) < 4:
        plan.append(_step(BallAction.SHOOT, m))
    ds = [c.d(w, Action.RUN_TO, f"{s}:final", start=0.5, end=3.5),
          c.d(m, Action.ATTACK_BOX, "cutback_spot", start=2.0, end=6.0)]
    st = c.pick(("st", "am"), exclude=(car.pid,))
    if st is not None:
        ds.append(c.d(st, Action.ATTACK_BOX, "near_post", start=1.0, end=6.0))
    far = c.pick(("wing", "am", "cm"), side="left" if s == "right" else "right")
    if far is not None:
        ds.append(c.d(far, Action.ATTACK_BOX, "far_post", start=2.0, end=6.0))
    return _intent(c, "cutback_overload", "Byline cutback overload",
                   f"{w.name} drives to the byline and pulls the ball back for {m.name} arriving at the "
                   "penalty spot, with runners at both posts.", Mechanism.CUTBACK, ds, plan,
                   style=[("shot_appetite", 0.2)])


def _isolation(c: _Ctx) -> TacticIntent | None:
    car = c.carrier
    if car is None or c.ball[0] < 40:
        return None
    s = c.attack_side
    w = c.pick(("wing",), side=s)
    if w is None or (w.pid != car.pid and not _reach(car, w)):
        return None
    c.take(w)
    c.take(car)
    half = "left_half" if s == "left" else "right_half"
    ds = [c.d(w, Action.HOLD_WIDTH, f"{s}:att", start=0.0, end=3.0)]
    fb = c.pick(("fb",), side=s)
    if fb is not None:
        ds.append(c.d(fb, Action.UNDERLAP, f"{half}:att", start=0.5, end=5.0))
    st = c.pick(("st", "am"), within=w)
    plan: list[BallStep] = []
    if w.pid != car.pid:
        plan.append(_step(BallAction.PASS, car, w))
    plan.append(_step(BallAction.CARRY, w, zone=f"{s}:final"))
    if st is not None:
        ds.append(c.d(st, Action.ATTACK_BOX, "far_post", start=2.0, end=6.0))
        plan.append(_step(BallAction.CROSS, w, st, "box"))
    return _intent(c, "wing_isolation", "Isolate the winger one-on-one",
                   f"{w.name} stays wide to take on his full-back one-on-one while the fullback runs inside to "
                   "pull defenders away, then crosses to the far post.", Mechanism.ISOLATION, ds, plan,
                   style=[("dribbling", 0.4)])


def _target_man(c: _Ctx) -> TacticIntent | None:
    car = c.carrier
    if car is None or c.ball[0] > 55:
        return None
    st = c.pick(("st",), exclude=(car.pid,))
    if st is None:
        return None
    c.take(car)
    c.take(st)
    ds = []
    cm = c.pick(("cm", "am", "dm"), near=st, exclude=(car.pid,), within=st)
    if cm is not None:
        ds.append(c.d(cm, Action.RUN_TO, "centre:att", start=1.0, end=6.0))
    for side in ("left", "right"):
        w = c.pick(("wing", "am"), side=side)
        if w is not None:
            ds.append(c.d(w, Action.RUN_TO, f"{side}:att", start=1.0, end=6.0))
    plan = [_step(BallAction.LONG_BALL, car, st)]
    if cm is not None:
        plan += [_step(BallAction.PASS, st, cm), _step(BallAction.SHOOT, cm)]
    return _intent(c, "direct_target_man", "Go direct to the striker",
                   f"{car.name} bypasses the press with a long ball to {st.name}, who holds it up for the "
                   "midfielders and wingers running on to the second ball.", Mechanism.ISOLATION, ds, plan,
                   style=[("directness", 0.5)], rest=2)


def _counter(c: _Ctx) -> TacticIntent | None:
    car = c.carrier
    if car is None or c.ball[0] > 60 or not (c.phase == "transition" or c.ball[0] < 45):
        return None
    mid_pool = [p for p in c.outfield if p.pid != car.pid and p.x >= car.x + 8 and _dist(car, p) <= 45
                and p.kind in ("cm", "am", "wing", "st")]
    if not mid_pool:
        return None
    m1 = max(mid_pool, key=lambda p: (p.x - 0.2 * _dist(car, p), p.pid))
    st = c.pick(("st",), exclude=(car.pid, m1.pid))
    if st is None or not _reach(m1, st):
        return None
    c.take(car)
    c.take(m1)
    c.take(st)
    ds = [c.d(st, Action.RUN_TO, f"{_lane(st.y)}:final", start=0.0, end=5.0)]
    for side in ("left", "right"):
        w = c.pick(("wing", "am"), side=side)
        if w is not None:
            ds.append(c.d(w, Action.RUN_TO, f"{side}:final", start=0.5, end=5.5))
    box = c.pick(("cm", "am"), exclude=(m1.pid,))
    if box is not None:
        ds.append(c.d(box, Action.ATTACK_BOX, "edge_of_box", start=2.0, end=6.0))
    plan = [_step(_pass_action(car, m1), car, m1), _step(BallAction.PASS, m1, st), _step(BallAction.SHOOT, st)]
    return _intent(c, "counter_after_regain", "Counter-attack after the regain",
                   f"{car.name} goes forward at once to {m1.name}, who finds {st.name} running at the "
                   "stretched defence before it can reset.", Mechanism.COUNTER, ds, plan,
                   style=[("verticality", 0.3), ("counter_boost", 0.3), ("tempo", 0.1)], rest=2)


def _corner(c: _Ctx, near: bool) -> TacticIntent | None:
    car = c.carrier
    if car is None or c.ball[0] < 99 or not (c.ball[1] <= 5 or c.ball[1] >= 63):
        return None
    left = c.ball[1] > 34
    near_zone, far_zone = ("far_post", "near_post") if left else ("near_post", "far_post")
    st = c.pick(("st",), exclude=(car.pid,), within=car) or c.pick(("am", "cm"), exclude=(car.pid,), within=car)
    cb = c.pick(("cb",), near=_P("", "", "cb", 100.0, 34.0), within=car)
    if st is None or cb is None:
        return None
    c.take(car)
    target, other = (st, cb) if near else (cb, st)
    ds = [c.d(target, Action.ATTACK_BOX, near_zone if near else far_zone, start=0.5, end=5.0),
          c.d(other, Action.ATTACK_BOX, far_zone if near else near_zone, start=0.5, end=5.0)]
    cm = c.pick(("cm", "am", "wing"))
    if cm is not None:
        ds.append(c.d(cm, Action.ATTACK_BOX, "penalty_spot", start=1.0, end=5.0))
    dm = c.pick(("dm", "cm"))
    if dm is not None:
        ds.append(c.d(dm, Action.RUN_TO, "edge_of_box", start=1.0, end=5.0))
    plan = [_step(BallAction.CROSS, car, target, near_zone if near else far_zone), _step(BallAction.SHOOT, target)]
    rid = "near_post_corner" if near else "far_post_corner"
    return _intent(c, rid, "Near-post corner routine" if near else "Far-post corner routine",
                   f"{car.name} whips the corner to the {'near' if near else 'far'} post where {target.name} "
                   f"attacks it, with {other.name} at the {'far' if near else 'near'} post and a runner "
                   "at the edge of the box for the second ball.", Mechanism.SET_PIECE, ds, plan,
                   style=[("shot_appetite", 0.3)], rest=2)


# ============================================================ out of possession


def _high_press_trap(c: _Ctx) -> TacticIntent | None:
    if c.carrier is None or c.ball[0] < 50:
        return None
    oc = c.carrier
    outlets = c.opp_near((oc.x, oc.y), 3, exclude=(oc.pid,))
    st = c.pick(("st", "am"))
    if not outlets or st is None:
        return None
    pivot = outlets[0]
    c.take(st)
    ds = [c.d(st, Action.PRESS_PLAYER, target=oc, start=0.0, end=4.0)]
    mid = c.pick(("cm", "am", "dm"), near=pivot)
    if mid is not None:
        ds.append(c.d(mid, Action.MAN_MARK, target=pivot, start=0.0, end=6.0))
    wing = c.pick(("wing", "fb", "am"), side=oc.side if oc.side != "centre" else None, near=oc)
    if wing is not None:
        ds.append(c.d(wing, Action.SCREEN_LANE, f"{_lane(oc.y)}:att", start=0.0, end=5.0))
    far = c.pick(("wing", "am"), near=outlets[-1])
    if far is not None and len(outlets) > 1:
        ds.append(c.d(far, Action.MAN_MARK, target=outlets[-1], start=0.5, end=6.0))
    press = PressPlan(trigger=PressTrigger.BACK_PASS, cover_shadow_on=pivot.pid, intensity=1.5)
    return _intent(c, "high_press_trap", "High press trap on the back pass",
                   f"{st.name} presses the ball and shades the pass to {pivot.name}, inviting the back pass "
                   "that triggers the whole line to jump.", Mechanism.PRESS_TRAP, ds, [], press=press,
                   style=[("press_intensity", 0.3), ("block_height", 4.0), ("line_height", 3.0)], rest=3)


def _gegenpress(c: _Ctx) -> TacticIntent | None:
    if c.carrier is None or not (c.phase == "transition" or 35 <= c.ball[0] <= 80):
        return None
    oc = c.carrier
    near = sorted(c.outfield, key=lambda p: (math.hypot(p.x - oc.x, p.y - oc.y), p.pid))[:3]
    if len(near) < 2:
        return None
    outlets = c.opp_near((oc.x, oc.y), 2, exclude=(oc.pid,))
    if not outlets:
        return None
    ds = [c.d(near[0], Action.PRESS_PLAYER, target=oc, start=0.0, end=3.5)]
    if len(outlets) > 0:
        ds.append(c.d(near[1], Action.MAN_MARK, target=outlets[0], start=0.0, end=5.0))
    if len(near) > 2 and len(outlets) > 1:
        ds.append(c.d(near[2], Action.MAN_MARK, target=outlets[1], start=0.0, end=5.0))
    press = PressPlan(trigger=PressTrigger.IMMEDIATE, cover_shadow_on=outlets[0].pid, intensity=1.8)
    return _intent(c, "gegenpress", "Counter-press the moment it's lost",
                   f"{near[0].name} closes the ball at once while {near[1].name} takes away the nearest outlet, "
                   "so the opponent has no safe pass in the first three seconds.", Mechanism.PRESS_TRAP, ds,
                   [], press=press, style=[("press_intensity", 0.4)], rest=3)


def _low_block(c: _Ctx) -> TacticIntent | None:
    if c.carrier is None or c.ball[0] > 62:
        return None
    third = "def" if c.ball[0] < 40 else "mid"
    half = "left_half" if c.side == "left" else "right_half"
    m1 = c.pick(("dm", "cm"), near=_P("", "", "cm", c.ball[0] * 0.6, 34.0))
    if m1 is None:
        return None
    c.take(m1)
    ds = [c.d(m1, Action.SCREEN_LANE, f"centre:{third}", start=0.0, end=8.0)]
    m2 = c.pick(("cm", "dm", "am"), near=m1)
    if m2 is not None:
        ds.append(c.d(m2, Action.SCREEN_LANE, f"{half if c.side != 'centre' else 'left_half'}:{third}",
                      start=0.0, end=8.0))
    fb = c.pick(("fb",), side="right" if c.side == "left" else "left")
    if fb is not None:
        ds.append(c.d(fb, Action.HOLD_WIDTH, f"{fb.side}:def" if fb.side != "centre" else "left:def",
                      start=0.0, end=8.0))
    wing = c.pick(("wing",), side=c.side if c.side != "centre" else None)
    if wing is not None:
        ds.append(c.d(wing, Action.DROP_DEEP, start=0.0, end=8.0))
    press = PressPlan(trigger=PressTrigger.NONE, cover_shadow_on=None, intensity=0.7)
    return _intent(c, "low_block_screen", "Compact low block, screen the middle",
                   "The team drops into a compact block: two midfielders screen the lanes into the centre "
                   "and the wide players tuck in, inviting crosses the centre-backs can defend.",
                   Mechanism.LOW_BLOCK, ds, [], press=press,
                   style=[("block_height", -6.0), ("line_height", -3.0), ("compactness_out", 0.15)], rest=4)


def _touchline_trap(c: _Ctx) -> TacticIntent | None:
    if c.carrier is None or not (c.ball[1] <= 12 or c.ball[1] >= 56) or not 20 <= c.ball[0] <= 90:
        return None
    oc = c.carrier
    inner = "left_half" if oc.y > 34 else "right_half"
    third = _third(oc.x)
    first = c.pick(("wing", "fb", "cm", "am"), near=oc)
    if first is None:
        return None
    c.take(first)
    ds = [c.d(first, Action.PRESS_PLAYER, target=oc, start=0.0, end=4.0)]
    second = c.pick(("cm", "dm", "am"), near=oc)
    if second is not None:
        ds.append(c.d(second, Action.SCREEN_LANE, f"{inner}:{third}", start=0.0, end=5.0))
    third_p = c.pick(("cm", "dm", "fb"), near=oc)
    if third_p is not None:
        ds.append(c.d(third_p, Action.SCREEN_LANE, f"centre:{third}", start=0.0, end=5.0))
    outlets = c.opp_near((oc.x, oc.y), 1, exclude=(oc.pid,))
    press = PressPlan(trigger=PressTrigger.TOUCHLINE, cover_shadow_on=outlets[0].pid if outlets else None,
                      intensity=1.4)
    return _intent(c, "touchline_trap", "Trap him on the touchline",
                   f"{first.name} closes down the wide carrier and the midfield closes the inside lanes, so "
                   "the only way out is back or into touch.", Mechanism.PRESS_TRAP, ds, [], press=press,
                   style=[("press_intensity", 0.2), ("compactness_out", 0.1)], rest=3)


_IN_POSSESSION: tuple[Callable[[_Ctx], TacticIntent | None], ...] = (
    _overlap_cross, _third_man, _switch, _run_in_behind, _cutback_overload, _isolation, _target_man, _counter,
    lambda c: _corner(c, near=True), lambda c: _corner(c, near=False),
)
_OUT_OF_POSSESSION: tuple[Callable[[_Ctx], TacticIntent | None], ...] = (
    _high_press_trap, _gegenpress, _low_block, _touchline_trap,
)

RECIPE_IDS: tuple[str, ...] = (
    "overlap_cross", "third_man", "switch_weak_side", "pin_and_run", "cutback_overload", "wing_isolation",
    "direct_target_man", "counter_after_regain", "near_post_corner", "far_post_corner",
    "high_press_trap", "gegenpress", "low_block_screen", "touchline_trap",
)
PLAYBOOK_KEYS: frozenset[str] = frozenset(f"playbook:{r}" for r in RECIPE_IDS)


# ================================================================== entry point


def _attacking_right(situation: Situation, team_id: str) -> bool:
    gk = next((p for p in situation.players if p.team_id == team_id and p.role.upper() == "GK"), None)
    if gk is not None:
        return gk.x < 52.5
    mine = [p.x for p in situation.players if p.team_id == team_id]
    theirs = [p.x for p in situation.players if p.team_id != team_id]
    return (sum(mine) / max(1, len(mine))) < (sum(theirs) / max(1, len(theirs)))


def _context(situation: Situation, team_id: str, horizon_s: float) -> _Ctx | None:
    right = _attacking_right(situation, team_id)

    def rel(p: PlayerSpot) -> _P:
        x, y = to_relative(p.x, p.y, right)
        return _P(p.player_id, p.name.split()[-1] if p.name else p.player_id, _kind(p, x), x, y)

    mates = [rel(p) for p in situation.players if p.team_id == team_id]
    opps = [rel(p) for p in situation.players if p.team_id != team_id]
    if not mates or not opps:
        return None
    possession = situation.possession_team_id == team_id
    bx, by = to_relative(*situation.ball, right)
    pool = mates if possession else opps
    by_id = {p.pid: p for p in pool}
    carrier = by_id.get(situation.carrier_id or "") or min(pool, key=lambda p: (math.hypot(p.x - bx, p.y - by), p.pid))
    side = "left" if by > 46 else "right" if by < 22 else "centre"
    if side == "centre":
        def crowd(s: str) -> int:
            return sum(o.side == s and o.x >= 35 for o in opps)
        attack_side = "left" if crowd("left") < crowd("right") else "right"
    else:
        attack_side = side
    return _Ctx(team=team_id, horizon=horizon_s, possession=possession, phase=situation.phase, mates=mates,
                opps=opps, carrier=carrier, ball=(bx, by), side=side, attack_side=attack_side)


def recipes(situation: Situation, team_id: str, meta: MatchMeta | None = None,
            horizon_s: float = 10.0) -> list[TacticIntent]:
    """Rule-based ideas for ``team_id`` at this moment, built from the players on the pitch.

    With ``meta`` each idea is also run through ``check_intent`` and dropped if it fails. Ideas that need
    something the situation does not have (a striker, a far-side winger, a corner) are left out.
    """
    from matchlens.tactics.validate import check_intent

    base = _context(situation, team_id, horizon_s)
    if base is None:
        return []
    out: list[TacticIntent] = []
    for build in (_IN_POSSESSION if base.possession else _OUT_OF_POSSESSION):
        ctx = _Ctx(team=base.team, horizon=base.horizon, possession=base.possession, phase=base.phase,
                   mates=base.mates, opps=base.opps, carrier=base.carrier, ball=base.ball, side=base.side,
                   attack_side=base.attack_side)
        try:
            intent = build(ctx)
        except (ValueError, KeyError):
            continue
        if intent is None:
            continue
        if meta is not None and check_intent(meta, situation, intent, horizon_s, set(PLAYBOOK_KEYS)):
            continue
        out.append(intent)
    return out

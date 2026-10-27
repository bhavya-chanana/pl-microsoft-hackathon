/**
 * Offline sample data for the Tactics Board (`/tactics?fixture=1`), shaped exactly like the backend's
 * TacticsBoard and RolloutView. Fictional clubs and players. Loaded lazily, never on the live path.
 */
import type {
  BoardCreated,
  BoardRequest,
  Frame,
  OutcomeStats,
  PlayerSpot,
  RolloutEvent,
  RolloutVariant,
  RolloutView,
  Situation,
  TacticDrawing,
  TacticResult,
  TacticsBoard,
} from "@/lib/contract";
import { BASELINE_ID } from "@/lib/contract";

import { FIXTURE_BOARD_PREFIX } from "../constants";

const HOME = "riv";
const AWAY = "har";

const SQUAD: PlayerSpot[] = [
  ["riv-1", "Jonas Weber", "GK", 4, 34],
  ["riv-2", "Luca Moretti", "RB", 50, 10],
  ["riv-3", "Tomas Hale", "CB", 36, 26],
  ["riv-4", "Ravi Okoro", "CB", 36, 42],
  ["riv-5", "Mateo Duarte", "LB", 46, 58],
  ["riv-6", "Idris Bello", "DM", 46, 34],
  ["riv-8", "Samir Haddad", "CM", 58, 24],
  ["riv-10", "Niko Varga", "CM", 58, 44],
  ["riv-7", "Kofi Mensah", "RW", 64, 14],
  ["riv-9", "Dario Vance", "ST", 78, 34],
  ["riv-11", "Elias Strand", "LW", 72, 54],
  ["har-1", "Pavel Novak", "GK", 101, 34],
  ["har-2", "Anders Kvist", "RB", 84, 12],
  ["har-4", "Felix Brandt", "CB", 86, 26],
  ["har-5", "Omar Rahimi", "CB", 86, 42],
  ["har-3", "Jules Laurent", "LB", 84, 56],
  ["har-7", "Hugo Santos", "RM", 72, 10],
  ["har-6", "Yusuf Demir", "CM", 72, 28],
  ["har-8", "Callum Reid", "CM", 72, 42],
  ["har-11", "Stefan Ilic", "LM", 72, 58],
  ["har-9", "Marek Sowa", "ST", 60, 28],
  ["har-10", "Ivo Petrov", "ST", 60, 40],
].map(([player_id, name, role, x, y]) => ({
  player_id: String(player_id),
  name: String(name),
  team_id: String(player_id).startsWith("riv") ? HOME : AWAY,
  role: String(role),
  x: Number(x),
  y: Number(y),
  tags: player_id === "riv-9" ? ["aerial threat"] : player_id === "riv-2" ? ["overlapping full-back"] : [],
}));

const BALL_START: [number, number] = [63, 15];

function entitiesOf(positions: Map<string, [number, number]>, previous: Map<string, [number, number]> | null, dt: number) {
  return [...positions.entries()].map(([entity_id, [x, y]]) => {
    const p = previous?.get(entity_id);
    const speed = p && dt > 0 ? Math.hypot(x - p[0], y - p[1]) / dt : 0;
    return { entity_id, x, y, speed: Math.round(speed * 10) / 10 };
  });
}

const START = new Map<string, [number, number]>([...SQUAD.map((p): [string, [number, number]] => [p.player_id, [p.x, p.y]]), ["ball", BALL_START]]);

export const FIXTURE_SITUATION: Situation = {
  match_id: "fixture-match",
  elapsed: 4260,
  minute_label: "71'",
  period: 2,
  score: { [HOME]: 0, [AWAY]: 1 },
  possession_team_id: HOME,
  carrier_id: "riv-7",
  ball: BALL_START,
  ball_zone: "right:att",
  phase: "progression",
  coaching_team_id: HOME,
  players: SQUAD,
  frame: { elapsed: 4260, period: 2, entities: entitiesOf(START, null, 0) },
};

function interval(value: number, spread: number): { value: number; low: number; high: number } {
  return { value, low: Math.max(0, value - spread), high: Math.min(1, value + spread) };
}

function stats(p: { shot: number; xg: number; goal: number; box: number; turnover: number; concede: number; xt: number; tts: number | null; terminals: Record<string, number> }): OutcomeStats {
  return {
    rollouts: 60,
    horizon_s: 10,
    p_shot: interval(p.shot, 0.1),
    mean_xg: p.xg,
    p_goal: interval(p.goal, 0.04),
    p_box_entry: interval(p.box, 0.1),
    p_turnover: interval(p.turnover, 0.09),
    p_concede_shot: interval(p.concede, 0.05),
    xt_delta: p.xt,
    time_to_shot_s: p.tts,
    terminals: p.terminals,
  };
}

const BASELINE = stats({ shot: 0.17, xg: 0.02, goal: 0.02, box: 0.22, turnover: 0.38, concede: 0.1, xt: 0.004, tts: 8.1, terminals: { retained: 28, turnover: 23, box_entry: 6, shot: 3 } });

type Template = Omit<TacticResult, "status" | "problems"> & { finalStatus: TacticResult["status"]; finalProblems: string[] };

function directive(player_id: string, action: string, zone: string | null, start_s: number, end_s: number, target_player_id: string | null = null) {
  return { player_id, action, zone, target_player_id, start_s, end_s };
}

const TEMPLATES: Template[] = [
  {
    tactic_id: "t1",
    persona: "recipe",
    intent: {
      team_id: HOME,
      title: "Overlap, then pull the ball back",
      idea: "Mensah draws the full-back, Moretti overlaps, and the cutback finds Vance arriving at the penalty spot.",
      mechanism: "cutback",
      directives: [
        directive("riv-2", "overlap", "right:final", 0.5, 4.5),
        directive("riv-9", "attack_box", "penalty_spot", 2, 7),
        directive("riv-8", "run_to", "edge_of_box", 3, 7),
        directive("riv-6", "rest_defence", null, 0, 10),
      ],
      ball_plan: [
        { action: "pass", from_player_id: "riv-7", to_player_id: "riv-2", to_zone: "right:final" },
        { action: "cutback", from_player_id: "riv-2", to_player_id: "riv-9", to_zone: "cutback_spot" },
      ],
      press: null,
      style_changes: [],
      rest_defence: 3,
      cites: ["overlapping full-back", "playbook: overlap cutback"],
    },
    outcome: stats({ shot: 0.41, xg: 0.11, goal: 0.09, box: 0.52, turnover: 0.3, concede: 0.09, xt: 0.031, tts: 6.4, terminals: { shot: 25, box_entry: 14, turnover: 16, retained: 5 } }),
    lift: { p_shot: 0.24, mean_xg: 0.09, p_box_entry: 0.3, p_turnover: -0.08, p_concede_shot: -0.01 },
    utility: 0.64,
    rank: 1,
    finalStatus: "ready",
    finalProblems: [],
    confidence: "high",
  },
  {
    tactic_id: "t2",
    persona: "positional",
    intent: {
      team_id: HOME,
      title: "Switch to the weak side",
      idea: "Draw Harbour across, then a diagonal to Strand, who is isolated one-on-one against their right-back.",
      mechanism: "switch",
      directives: [
        directive("riv-11", "hold_width", "left:att", 0, 6),
        directive("riv-10", "drop_deep", "centre:mid", 0.5, 3),
        directive("riv-5", "underlap", "left_half:att", 2, 7),
      ],
      ball_plan: [
        { action: "pass", from_player_id: "riv-7", to_player_id: "riv-10", to_zone: "centre:mid" },
        { action: "switch", from_player_id: "riv-10", to_player_id: "riv-11", to_zone: "left:att" },
      ],
      press: null,
      style_changes: [{ knob: "tempo", delta: -0.1 }],
      rest_defence: 3,
      cites: ["Harbour block shifts toward the ball"],
    },
    outcome: stats({ shot: 0.29, xg: 0.06, goal: 0.05, box: 0.4, turnover: 0.26, concede: 0.07, xt: 0.02, tts: 7.2, terminals: { shot: 17, box_entry: 14, retained: 14, turnover: 15 } }),
    lift: { p_shot: 0.12, mean_xg: 0.04, p_box_entry: 0.18, p_turnover: -0.12, p_concede_shot: -0.03 },
    utility: 0.41,
    rank: 2,
    finalStatus: "ready",
    finalProblems: [],
    confidence: "medium",
  },
  {
    tactic_id: "t3",
    persona: "gegenpress",
    intent: {
      team_id: HOME,
      title: "Commit numbers, counter-press on loss",
      idea: "Push both midfielders into the box line while Bello and the centre-backs hold a tight rest-defence.",
      mechanism: "overload",
      directives: [
        directive("riv-8", "attack_box", "box", 1.5, 7),
        directive("riv-10", "run_to", "edge_of_box", 2, 7),
        directive("riv-6", "screen_lane", "centre:mid", 0, 10),
      ],
      ball_plan: [
        { action: "carry", from_player_id: "riv-7", to_player_id: null, to_zone: "right:final" },
        { action: "cross", from_player_id: "riv-7", to_player_id: "riv-9", to_zone: "box" },
      ],
      press: { trigger: "immediate", cover_shadow_on: "har-6", intensity: 1.5 },
      style_changes: [{ knob: "press_intensity", delta: 0.2 }],
      rest_defence: 3,
      cites: ["Harbour counter on turnovers"],
    },
    outcome: stats({ shot: 0.36, xg: 0.08, goal: 0.07, box: 0.47, turnover: 0.34, concede: 0.14, xt: 0.024, tts: 6.8, terminals: { shot: 22, box_entry: 13, turnover: 20, retained: 5 } }),
    lift: { p_shot: 0.19, mean_xg: 0.06, p_box_entry: 0.25, p_turnover: -0.04, p_concede_shot: 0.04 },
    utility: 0.33,
    rank: 3,
    finalStatus: "ready",
    finalProblems: [],
    confidence: "medium",
  },
  {
    tactic_id: "t4",
    persona: "direct",
    intent: {
      team_id: HOME,
      title: "Long ball to the near channel",
      idea: "Bypass the block with a ball over the top for Vance to chase into the channel.",
      mechanism: "run_in_behind",
      directives: [directive("riv-9", "run_to", "left_half:final", 0, 4), directive("riv-99", "overlap", "right:final", 0, 4)],
      ball_plan: [{ action: "long_ball", from_player_id: "riv-7", to_player_id: "riv-9", to_zone: "left_half:final" }],
      press: null,
      style_changes: [],
      rest_defence: 2,
      cites: [],
    },
    outcome: null,
    lift: {},
    utility: null,
    rank: null,
    finalStatus: "rejected",
    finalProblems: ["Player riv-99 is not on the pitch.", "A long ball needs a receiver within 40 m of the carrier."],
    confidence: null,
  },
];

const VERDICT = {
  headline: "Overlap and cut it back",
  text: "Mensah and Moretti outnumber Harbour's left side, and the cutback reaches the penalty spot before their midfield recovers. It lifts shot chance by 24 points and costs almost nothing in cover.",
  recommended_tactic_id: "t1",
  cited_metrics: ["p_shot", "mean_xg", "p_concede_shot"],
};

type Session = { request: BoardRequest; startedAt: number };
const sessions = new Map<string, Session>();
let counter = 0;

export function createFixtureBoard(request: BoardRequest): BoardCreated {
  counter += 1;
  const board_id = `${FIXTURE_BOARD_PREFIX}${counter}`;
  sessions.set(board_id, { request, startedAt: Date.now() });
  return { board_id, situation: FIXTURE_SITUATION };
}

const THINK_MS = 1_400;
const STAGGER_MS = 900;
const EVALUATE_MS = 2_200;

/** The sample board as it would look `ageMs` after it was requested: tactics arrive one by one. */
export function fixtureBoard(boardId: string): TacticsBoard {
  const session = sessions.get(boardId);
  const age = session ? Date.now() - session.startedAt : Number.POSITIVE_INFINITY;
  const risk = session?.request.risk ?? "balanced";
  let lastDone = 0;
  const tactics: TacticResult[] = TEMPLATES.map((t, i) => {
    const { finalStatus, finalProblems, ...rest } = t;
    const thinkEnd = THINK_MS + i * STAGGER_MS;
    const evalEnd = thinkEnd + EVALUATE_MS;
    lastDone = Math.max(lastDone, evalEnd);
    if (age < thinkEnd) return { ...rest, outcome: null, lift: {}, utility: null, rank: null, confidence: null, status: "thinking", problems: [] };
    if (finalStatus === "rejected") return { ...rest, outcome: null, status: "rejected", problems: finalProblems };
    if (age < evalEnd) return { ...rest, outcome: null, lift: {}, utility: null, rank: null, confidence: null, status: "evaluating", problems: [] };
    return { ...rest, status: finalStatus, problems: finalProblems };
  });
  const done = age >= lastDone;
  const anyEvaluating = tactics.some((t) => t.status === "evaluating") || tactics.every((t) => t.status !== "thinking");
  return {
    board_id: boardId,
    status: done ? "done" : anyEvaluating ? "evaluating" : "thinking",
    situation: FIXTURE_SITUATION,
    prompt: session?.request.prompt ?? null,
    risk,
    horizon_s: session?.request.horizon_s ?? 10,
    baseline: age > 600 ? BASELINE : null,
    tactics,
    verdict: done ? VERDICT : null,
    evaluator: "fixture-xt-v1",
    model_label: "Sample data",
    recorded: true,
    errors: [],
    prompt_matched: [],
    prompt_note: null,
  };
}

/* ------------------------------ rollouts ------------------------------ */

type Leg = { t: number; from: string; to: string; at: [number, number]; kind?: string };

/** Ball route per branch: who passes to whom, where, and when. */
const LEGS: Record<string, Leg[]> = {
  [BASELINE_ID]: [
    { t: 1.6, from: "riv-7", to: "riv-8", at: [58, 24] },
    { t: 3.4, from: "riv-8", to: "riv-6", at: [48, 34] },
    { t: 5.4, from: "riv-6", to: "riv-4", at: [37, 42], kind: "pass" },
  ],
  t1: [
    { t: 1.4, from: "riv-7", to: "riv-2", at: [76, 8] },
    { t: 3.4, from: "riv-2", to: "riv-9", at: [90, 34], kind: "cutback" },
  ],
  t2: [
    { t: 1.5, from: "riv-7", to: "riv-10", at: [58, 44] },
    { t: 3.5, from: "riv-10", to: "riv-11", at: [78, 58], kind: "switch" },
    { t: 5.2, from: "riv-11", to: "riv-9", at: [88, 38] },
  ],
  t3: [
    { t: 2.2, from: "riv-7", to: "riv-7", at: [80, 10], kind: "carry" },
    { t: 3.8, from: "riv-7", to: "riv-9", at: [95, 33], kind: "cross" },
  ],
};

/** Where each runner ends up (absolute metres), by branch. */
const TARGETS: Record<string, Record<string, [number, number]>> = {
  [BASELINE_ID]: { "riv-6": [48, 36], "riv-8": [56, 28], "riv-9": [74, 34] },
  t1: { "riv-2": [78, 7], "riv-9": [93, 34], "riv-8": [86, 38], "riv-7": [74, 12] },
  t2: { "riv-11": [78, 60], "riv-10": [56, 36], "riv-5": [74, 50], "riv-9": [84, 36] },
  t3: { "riv-8": [94, 30], "riv-10": [86, 40], "riv-7": [80, 10], "riv-9": [92, 34] },
};

const DRAWINGS: Record<string, TacticDrawing[]> = {
  [BASELINE_ID]: [],
  t1: [
    { kind: "path", team_id: HOME, points: [[50, 10], [66, 6], [78, 7]], label: "Overlap" },
    { kind: "lane", team_id: HOME, points: [[63, 15], [76, 8]], label: null },
    { kind: "arrow", team_id: HOME, points: [[78, 7], [90, 34]], label: "Cutback" },
    { kind: "zone", team_id: HOME, points: [[88, 28], [98, 28], [98, 40], [88, 40]], label: "Penalty spot" },
  ],
  t2: [
    { kind: "lane", team_id: HOME, points: [[58, 44], [78, 58]], label: "Switch" },
    { kind: "path", team_id: HOME, points: [[46, 58], [60, 54], [74, 50]], label: "Underlap" },
    { kind: "zone", team_id: HOME, points: [[70, 50], [86, 50], [86, 66], [70, 66]], label: "Weak side" },
  ],
  t3: [
    { kind: "path", team_id: HOME, points: [[64, 14], [80, 10]], label: "Carry" },
    { kind: "arrow", team_id: HOME, points: [[80, 10], [95, 33]], label: "Cross" },
    { kind: "zone", team_id: HOME, points: [[88, 22], [105, 22], [105, 46], [88, 46]], label: "Box" },
  ],
};

const smooth = (x: number) => {
  const c = Math.min(1, Math.max(0, x));
  return c * c * (3 - 2 * c);
};

function ballAt(legs: Leg[], t: number): [number, number] {
  let from: [number, number] = BALL_START;
  let start = 0;
  for (const leg of legs) {
    if (t <= leg.t) {
      const k = smooth((t - start) / Math.max(0.1, leg.t - start));
      return [from[0] + (leg.at[0] - from[0]) * k, from[1] + (leg.at[1] - from[1]) * k];
    }
    from = leg.at;
    start = leg.t;
  }
  return from;
}

/** Deterministic wobble so the 5 Hz frames do not look rail-mounted. */
const wobble = (seed: number, t: number) => Math.sin(seed * 12.9898 + t * 1.7) * 0.35;

export function fixtureRollout(tacticId: string, variant: RolloutVariant): RolloutView {
  const key = LEGS[tacticId] ? tacticId : BASELINE_ID;
  const base = LEGS[key];
  const end = base[base.length - 1];
  let legs = base;
  let terminal = key === BASELINE_ID ? "retained" : "box_entry";
  const extra: RolloutEvent[] = [];
  if (key !== BASELINE_ID && variant === "best") {
    legs = [...base, { t: end.t + 1.2, from: end.to, to: "goal", at: [105, 34] as [number, number], kind: "shot" }];
    terminal = "shot";
  } else if (variant === "worst") {
    const cut = base.slice(0, Math.max(1, base.length - 1));
    const last = cut[cut.length - 1];
    legs = cut;
    terminal = "turnover";
    extra.push({ event_id: `${key}-lost`, elapsed: last.t + 0.8, type: "interception", team_id: AWAY, player_id: "har-6", related_player_id: last.to, outcome: "success" });
  }
  const targets = TARGETS[key];
  const frames: Frame[] = [];
  let previous: Map<string, [number, number]> | null = null;
  const t0 = FIXTURE_SITUATION.elapsed;
  const steps = 50;
  for (let i = 0; i <= steps; i += 1) {
    const t = i * 0.2;
    const positions = new Map<string, [number, number]>();
    const ball = ballAt(legs, t);
    SQUAD.forEach((p, idx) => {
      const target = targets[p.player_id];
      let x = p.x;
      let y = p.y;
      if (target) {
        const k = smooth((t - 0.4) / 5.5);
        x += (target[0] - p.x) * k;
        y += (target[1] - p.y) * k;
      } else if (p.team_id === HOME) {
        // The rest of the side shuffles up with the ball.
        const k = smooth(t / 8) * 0.25;
        x += (ball[0] - 14 - p.x) * k * (p.role === "GK" ? 0 : 1);
        y += (ball[1] - p.y) * k * 0.2;
      } else {
        // Harbour's block slides toward the ball and drops off as it comes forward.
        const k = smooth(t / 6) * (p.role === "GK" ? 0.02 : 0.3);
        x += (ball[0] + 14 - p.x) * k * 0.4;
        y += (ball[1] - p.y) * k;
      }
      positions.set(p.player_id, [Math.round((x + wobble(idx, t)) * 100) / 100, Math.round((y + wobble(idx + 40, t)) * 100) / 100]);
    });
    positions.set("ball", [Math.round(ball[0] * 100) / 100, Math.round(ball[1] * 100) / 100]);
    frames.push({ elapsed: Math.round((t0 + t) * 10) / 10, period: 2, entities: entitiesOf(positions, previous, 0.2) });
    previous = positions;
  }
  const events: RolloutEvent[] = [
    ...legs.map((leg, i): RolloutEvent => ({
      event_id: `${key}-${variant}-${i}`,
      elapsed: t0 + leg.t,
      type: leg.kind === "shot" ? "shot" : leg.kind === "carry" ? "carry" : "pass",
      team_id: HOME,
      player_id: leg.from,
      related_player_id: leg.to === "goal" ? null : leg.to,
      outcome: "success",
    })),
    ...extra,
  ].sort((a, b) => a.elapsed - b.elapsed);
  return {
    tactic_id: tacticId,
    variant,
    seed: 11 + (variant === "best" ? 1 : variant === "worst" ? 2 : 0),
    frames,
    events,
    drawings: DRAWINGS[key] ?? [],
    terminal,
  };
}

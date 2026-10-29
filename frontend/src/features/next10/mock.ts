import type { Moment, Next10Match, Outcome, Prediction, ScoreDoc, Side, TrackFrame, HeadScore, Reliability, Entity } from "./contract";
import { PITCH_X, PITCH_Y } from "./config";

/**
 * Fictional fixtures in the backend's shapes (dev only, behind NEXT_PUBLIC_NEXT10_MOCK=1). Everything is a pure
 * function of the match id and the clock, so every reload shows the same match.
 */

const KICKOFF = Date.UTC(2026, 9, 28, 19, 0, 0);
const LIVE_CLOCK_S = 63 * 60 + 20;

const club = (name: string, fotmob_id: number) => ({ name, fotmob_id });
const FIXTURES: Array<Omit<Next10Match, "id"> & { key: string }> = [
  { key: "live", match_id: "mock:live", source: "mock", home: club("Northgate Rovers", 9001), away: club("Harbourside United", 9002), competition: "Mock League", kickoff_utc: new Date(Date.now() - LIVE_CLOCK_S * 1000 - 900_000).toISOString(), status: "live", score: { home: 1, away: 0 } },
  { key: "ft1", match_id: "mock:ft1", source: "mock", home: club("Eastbrook City", 9003), away: club("Northgate Rovers", 9001), competition: "Mock League", kickoff_utc: new Date(KICKOFF - 3 * 86400_000).toISOString(), status: "finished", score: { home: 2, away: 1 } },
  { key: "ft2", match_id: "mock:ft2", source: "mock", home: club("Harbourside United", 9002), away: club("Westmere Athletic", 9004), competition: "Mock Cup", kickoff_utc: new Date(KICKOFF - 8 * 86400_000).toISOString(), status: "finished", score: { home: 0, away: 0 } },
  { key: "up1", match_id: "mock:up1", source: "mock", home: club("Westmere Athletic", 9004), away: club("Eastbrook City", 9003), competition: "Mock League", kickoff_utc: new Date(Date.now() + 2 * 86400_000).toISOString(), status: "upcoming", score: null },
  { key: "up2", match_id: "mock:up2", source: "mock", home: club("Northgate Rovers", 9001), away: club("Westmere Athletic", 9004), competition: "Mock Cup", kickoff_utc: new Date(Date.now() + 6 * 86400_000).toISOString(), status: "upcoming", score: null },
];

export function mockMatches(): Next10Match[] {
  return FIXTURES.map(({ key, ...m }) => ({ ...m, id: `match:${key}` }));
}

export function mockMatch(matchId: string): { match: Next10Match; score: ScoreDoc | null } | null {
  const match = mockMatches().find((m) => m.match_id === matchId);
  if (!match) return null;
  return { match, score: match.status === "finished" ? mockScore(matchId) : null };
}

/* ------------------------------ tracking ------------------------------ */

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function ballAt(t: number): { x: number; y: number } {
  return {
    x: clamp(26 * Math.sin(t / 23) + 9 * Math.sin(t / 7.3), -50, 50),
    y: clamp(18 * Math.sin(t / 17 + 1) + 4 * Math.sin(t / 3.1), -32, 32),
  };
}

/** 4-4-2 for the side attacking +x; the away side is the mirror image. */
const SLOTS: Array<[number, number]> = [
  [-48, 0], [-32, -24], [-34, -8], [-34, 8], [-32, 24],
  [-14, -22], [-16, -7], [-16, 7], [-14, 22], [8, -9], [8, 9],
];

export function mockFrame(t: number): TrackFrame {
  const ball = ballAt(t);
  const entities: Entity[] = [];
  for (const side of ["home", "away"] as const) {
    const dir = side === "home" ? 1 : -1;
    SLOTS.forEach(([sx, sy], i) => {
      const wobble = (k: number) => Math.sin(t / (9 + i) + k * 1.7 + i);
      const x = clamp(dir * sx + 0.45 * ball.x * (i === 0 ? 0.15 : 1) + 3 * wobble(0), -52, 52);
      const y = clamp(sy * 0.92 + 0.25 * ball.y + 2.4 * wobble(1), -33, 33);
      // The broadcast camera sees roughly 60 m of pitch around the ball; the rest is imputed.
      const observed = Math.abs(x - ball.x) < 30;
      entities.push({ id: `${side}-${i + 1}`, team: side, x, y, observed, conf: observed ? 0.9 : 0.4 });
    });
  }
  entities.push({ id: "ref-1", team: "ref", x: clamp(ball.x * 0.8 - 6, -50, 50), y: clamp(ball.y * 0.5 + 10, -33, 33), observed: true, conf: 0.9 });
  entities.push({ id: "ball", team: "ball", x: ball.x, y: ball.y, observed: true, conf: 0.8 });
  // A few seconds in every minute are interpolated, so the lower-confidence note can be seen.
  const interpolated = Math.floor(t) % 60 >= 20 && Math.floor(t) % 60 < 26;
  return { t_s: t, match_clock_s: t, entities, calibrated: true, calib_status: interpolated ? "interpolated" : "fitted" };
}

/** Match clock of the mock live match, advancing in real time from where the page loaded. */
const LOADED = Date.now();
export const mockNow = () => LIVE_CLOCK_S + (Date.now() - LOADED) / 1000;

export function mockFrames(from: number, to: number, last: number | null = null): TrackFrame[] {
  if (last) return [mockFrame(Math.round(mockNow() * 2) / 2)];
  const out: TrackFrame[] = [];
  const start = Math.max(0, Math.floor(from));
  for (let t = start; t <= Math.min(to, start + 400); t += 0.5) out.push(mockFrame(t));
  return out;
}

/* ------------------------------ predictions ------------------------------ */

export function mockOutcomes(): Outcome[] {
  return [
    { team_side: "home", minute: 6, type: "shot", xg: 0.04 },
    { team_side: "away", minute: 14, type: "shot", xg: 0.07 },
    { team_side: "home", minute: 22, type: "shot", xg: 0.31 },
    { team_side: "home", minute: 23, type: "goal", xg: 0.31 },
    { team_side: "away", minute: 37, type: "shot", xg: 0.18 },
    { team_side: "home", minute: 41, type: "shot", xg: 0.06 },
    { team_side: "away", minute: 55, type: "shot", xg: 0.22 },
    { team_side: "home", minute: 60, type: "shot", xg: 0.09 },
  ].map((o) => ({ ...o, team_side: o.team_side as Side, t_s: o.minute * 60 + 20 }));
}

function shadowProbs(side: Side, minute: number): Record<string, number> {
  const push = 0.5 + 0.5 * Math.sin(minute / 9 + (side === "home" ? 0 : 2.4));
  const shot1 = clamp(0.035 + 0.07 * push + (side === "home" ? 0.01 : 0), 0.01, 0.2);
  const shot5 = clamp(1 - (1 - shot1) ** 4.2, 0, 0.7);
  return { shot_1m: shot1, shot_5m: shot5, goal_5m: shot5 * 0.11 };
}

/** Where the ball is likely to be in 10 s: a smeared bump around its drift, normalised to sum 1. */
function ballZone(t: number) {
  const cols = 16;
  const rows = 12;
  const now = ballAt(t);
  const later = ballAt(t + 10);
  const cx = (now.x + later.x * 2) / 3;
  const cy = (now.y + later.y * 2) / 3;
  const p: number[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = -PITCH_X + ((c + 0.5) * 2 * PITCH_X) / cols;
      const y = -PITCH_Y + ((r + 0.5) * 2 * PITCH_Y) / rows;
      p.push(Math.exp(-(((x - cx) / 14) ** 2 + ((y - cy) / 11) ** 2)));
    }
  }
  const total = p.reduce((a, b) => a + b, 0);
  return { cols, rows, p: p.map((v) => v / total) };
}

function next10Doc(matchId: string, t: number): Prediction {
  const frame = mockFrame(t);
  const ball = frame.entities.find((e) => e.team === "ball")!;
  // The side whose half the ball is not in is in possession, to keep the story simple.
  const side: Side = ball.x >= -4 ? "home" : "away";
  const sign = side === "home" ? 1 : -1;
  const mates = frame.entities.filter((e) => e.team === side && !e.id.endsWith("-1"));
  const nearest = mates.slice().sort((a, b) => Math.hypot(a.x - ball.x, a.y - ball.y) - Math.hypot(b.x - ball.x, b.y - ball.y)).slice(0, 3);
  const weights = [0.46, 0.27, 0.14];
  const jitter = [[0, 0], [3.5, -4], [-4, 5]];
  const pOf = [0.55, 0.28, 0.17];
  const ballModes = jitter.map(([dx, dy], k) => ({
    p: pOf[k],
    path: Array.from({ length: 10 }, (_, s) => {
      const b = ballAt(t + s + 1);
      return [clamp(b.x + (dx * (s + 1)) / 10, -52, 52), clamp(b.y + (dy * (s + 1)) / 10, -33, 33)] as [number, number];
    }),
  }));
  const movers = nearest.map((e) => {
    const end = mockFrame(t + 10).entities.find((x) => x.id === e.id)!;
    return {
      entity_id: e.id,
      modes: jitter.map(([dx, dy], k) => ({
        p: pOf[k],
        path: Array.from({ length: 10 }, (_, s) => [e.x + ((end.x - e.x) * (s + 1)) / 10 + (dx * (s + 1)) / 14, e.y + ((end.y - e.y) * (s + 1)) / 10 + (dy * (s + 1)) / 14] as [number, number]),
      })),
    };
  });
  const attack = clamp(0.5 + (sign * ball.x) / 100, 0.05, 0.95);
  const poss = clamp(0.55 + 0.1 * Math.sin(t / 6), 0, 1);
  return {
    id: `next10:${matchId}:${Math.round(t)}`,
    match_id: matchId,
    kind: "next10",
    minute: t / 60,
    t_s: t,
    possession_team_id: side,
    probs: { shot10: clamp(0.02 + 0.2 * attack ** 2, 0, 1), goal10: clamp(0.002 + 0.03 * attack ** 3, 0, 1), concede10: 0.004, turn5: clamp(0.3 + 0.1 * Math.sin(t / 5), 0, 1), poss10: poss },
    prob_std: { shot10: 0.02, turn5: 0.04, poss10: 0.05 },
    poss10_by_team: { [side]: poss, [side === "home" ? "away" : "home"]: 1 - poss },
    model_version: "mock-next10",
    ball_zone10: { ...ballZone(t), x0: -PITCH_X, y0: -PITCH_Y },
    receivers: nearest.map((e, i) => ({ player_id: e.id, p: weights[i], std: 0.05 + 0.03 * i })),
    receiver_lost: 0.13,
    trajectories: { dt_s: 1, horizon_s: 10, entities: [{ entity_id: "ball", modes: ballModes }, ...movers] },
  };
}

export function mockPredictions(matchId: string, kind: string | null, team: string | null, fromS: number | null, toS: number | null, latest: number | null = null): Prediction[] {
  const fixture = mockMatches().find((m) => m.match_id === matchId);
  const live = fixture?.status === "live";
  const last = live ? LIVE_CLOCK_S : 96 * 60;
  if (latest && kind === "next10") return [next10Doc(matchId, Math.round(mockNow() / 5) * 5)];
  const sides: Side[] = team === "home" || team === "away" ? [team] : ["home", "away"];
  const out: Prediction[] = [];
  if (!kind || kind === "shadow_event") {
    for (let m = 1; m * 60 <= last; m++) {
      for (const side of sides) {
        out.push({ id: `shadow:${matchId}:${side}:${m}`, match_id: matchId, kind: "shadow_event", team_side: side, team: fixture?.[side].name, minute: m, t_s: m * 60, probs: shadowProbs(side, m), model_version: "mock-hazard-1" });
      }
    }
  }
  if (!kind || kind === "next10") {
    const hi = toS ?? last;
    const lo = fromS ?? Math.max(0, last - 120);
    for (let t = Math.ceil(lo / 5) * 5; t <= Math.min(hi, last); t += 5) out.push(next10Doc(matchId, t));
  }
  return out;
}

export function mockJobs(matchId: string): unknown[] {
  if (matchId !== "mock:live") return [];
  return [{ id: "job:mock", match_id: matchId, live_ready: true, stats: { live_ready: true, calibrated_share: 0.8, on_pitch_rate: 0.96, fps: 5, dropped_frames: 0 } }];
}

/* ------------------------------ moments ------------------------------ */

export function mockMoments(matchId: string): Moment[] {
  const fixture = mockMatches().find((m) => m.match_id === matchId);
  if (!fixture || fixture.status === "upcoming") return [];
  const home = fixture.home.name;
  const away = fixture.away.name;
  const all: Moment[] = [
    { id: "m1", match_id: matchId, kind: "chance", team_side: "home", team: home, minute: 22, t_s: 22 * 60 + 20, xg: 0.31, player: "Okafor", label: "Big chance, saved" },
    { id: "m2", match_id: matchId, kind: "goal_conceded", team_side: "away", team: away, minute: 23, t_s: 23 * 60 + 20, xg: 0.31, player: "Okafor", label: "Goal conceded" },
    { id: "m3", match_id: matchId, kind: "chance", team_side: "away", team: away, minute: 37, t_s: 37 * 60 + 20, xg: 0.18, player: "Dalli", label: "Shot, blocked" },
    { id: "m4", match_id: matchId, kind: "chance", team_side: "away", team: away, minute: 55, t_s: 55 * 60 + 20, xg: 0.22, player: "Reyes", label: "Shot, wide" },
  ];
  if (matchId === "mock:ft2") {
    // A video-only match: moments are peaks of the model's own shot chance, not recorded events.
    return all.filter((m) => m.kind !== "goal_conceded").map((m, i) => ({ ...m, source: "model", xg: null, p_shot10: [0.34, 0.27, 0.22][i] ?? 0.2, label: "Model shot-chance peak" }));
  }
  return all.map((m) => ({ ...m, source: "fotmob" }));
}

/* ------------------------------ verification ------------------------------ */

function head(n: number, base: number, skill: number, persist: number): HeadScore {
  const bBase = base * (1 - base);
  const llBase = -(base * Math.log(base) + (1 - base) * Math.log(1 - base));
  const reliability: Reliability[] = Array.from({ length: 10 }, (_, i) => {
    const lo = i / 10;
    const mean = lo + 0.05;
    const mass = Math.max(1, Math.round(n * Math.exp(-i * 0.75) * 0.3));
    const scaled = clamp(mean * (base / 0.25) ** 0.2, 0, 1);
    return { lo, hi: lo + 0.1, n: mass, mean_p: mean * Math.min(1, base * 4), freq: clamp(scaled * Math.min(1, base * 4) * (1 + (skill - 1) * 0.5 * (i % 2 ? 1 : -1)), 0, 1) };
  });
  return { n, base_rate: base, brier: bBase * skill, brier_baseline: bBase, brier_persistence: bBase * persist, logloss: llBase * skill, logloss_baseline: llBase, logloss_persistence: llBase * persist, reliability };
}

export function mockScore(matchId: string): ScoreDoc {
  const k = matchId === "mock:ft1" ? 0 : matchId === "mock:ft2" ? 1 : 2;
  return {
    id: `score:${matchId}`,
    match_id: matchId,
    kind: "score",
    scored_at: new Date(KICKOFF).toISOString(),
    heads: {
      shot_1m: head(180 - 6 * k, 0.07, [0.93, 0.98, 1.04][k], 1.12),
      shot_5m: head(176 - 6 * k, 0.28, [0.9, 0.96, 1.01][k], 1.2),
      goal_5m: head(176 - 6 * k, 0.026, [0.97, 1.03, 1.0][k], 1.4),
    },
    outcomes: mockOutcomes(),
  };
}

export function mockVerify(): ScoreDoc[] {
  return ["mock:ft1", "mock:ft2"].map(mockScore);
}

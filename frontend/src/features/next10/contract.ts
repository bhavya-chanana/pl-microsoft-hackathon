import { z } from "zod";

/**
 * Next-10s backend contract (docs/NEXT10.md). Every field the backend may still change is optional or defaulted,
 * so a shape mismatch renders an honest empty state instead of a crash.
 *
 * Pitch convention: metres, origin at the centre spot, x in [-52.5, 52.5], y in [-34, 34]; home attack towards +x
 * for the whole match; y grows downwards on screen.
 */

export const Side = z.enum(["home", "away"]);
export type Side = z.infer<typeof Side>;

const Club = z.object({ name: z.string(), fotmob_id: z.union([z.string(), z.number()]).nullish() });

export const Next10Match = z.object({
  id: z.string(),
  match_id: z.string(),
  source: z.string().nullish(),
  home: Club,
  away: Club,
  competition: z.string().nullish(),
  kickoff_utc: z.string().nullish(),
  status: z.string().nullish(),
  score: z.object({ home: z.number().nullish(), away: z.number().nullish() }).nullish(),
  fotmob_id: z.number().nullish(),
  /** A video clip: the span of match-clock seconds the footage covers. */
  clock_from_s: z.number().nullish(),
  clock_to_s: z.number().nullish(),
});
export type Next10Match = z.infer<typeof Next10Match>;

export const MatchDetail = z.object({ match: Next10Match, score: z.unknown().nullish() });

const Probs = z.record(z.string(), z.number());

export const BallZone = z.object({
  cols: z.number().int().positive(),
  rows: z.number().int().positive(),
  x0: z.number().default(-52.5),
  y0: z.number().default(-34),
  /** Flat, row-major (index = row * cols + col), row 0 at y = y0, summing to 1. Absolute pitch frame. */
  p: z.array(z.number()),
});
export type BallZone = z.infer<typeof BallZone>;

export const Receiver = z.object({ player_id: z.string(), p: z.number(), std: z.number().nullish() });
export type Receiver = z.infer<typeof Receiver>;

/** A path[i] is the position at t = (i + 1) * dt_s; mode k is the same joint scenario for every entity. */
export const TrajectoryMode = z.object({ p: z.number(), path: z.array(z.tuple([z.number(), z.number()])) });
export const Trajectory = z.object({ entity_id: z.string(), modes: z.array(TrajectoryMode) });
export type Trajectory = z.infer<typeof Trajectory>;
export const TrajectorySet = z.object({ dt_s: z.number().default(1), horizon_s: z.number().nullish(), entities: z.array(Trajectory) });
export type TrajectorySet = z.infer<typeof TrajectorySet>;

export const Prediction = z.object({
  id: z.string(),
  match_id: z.string(),
  kind: z.string(),
  /** Shadow-event docs are per team; Next10 docs cover both teams and leave this empty. */
  team_side: Side.nullish(),
  team: z.string().nullish(),
  minute: z.number(),
  /** Match-clock seconds the prediction is for, when the backend sends it; minute * 60 otherwise. */
  t_s: z.number().nullish(),
  t_issued: z.string().nullish(),
  /** Shadow: shot_1m, shot_5m, goal_5m. Next10: shot10, goal10, concede10, turn5, poss10 (for the possession team). */
  probs: Probs.default({}),
  prob_std: Probs.nullish(),
  model_version: z.string().nullish(),
  features: z.record(z.string(), z.unknown()).nullish(),
  possession_team_id: z.string().nullish(),
  poss10_by_team: Probs.nullish(),
  ball_zone10: BallZone.nullish(),
  receivers: z.array(Receiver).nullish(),
  receiver_lost: z.number().nullish(),
  action_next: Probs.nullish(),
  trajectories: z
    .union([TrajectorySet, z.array(Trajectory).transform((entities) => ({ dt_s: 1, entities }))])
    .nullish(),
});
export type Prediction = z.infer<typeof Prediction>;

export const Entity = z.object({
  id: z.string(),
  team: z.enum(["home", "away", "ref", "ball", "unk"]).catch("unk"),
  x: z.number(),
  y: z.number(),
  observed: z.boolean().default(true),
  conf: z.number().nullish(),
});
export type Entity = z.infer<typeof Entity>;

/** How the frame's pitch mapping was obtained. Interpolated is a guess between two fitted frames, so less certain. */
export const CalibStatus = z.enum(["fitted", "interpolated", "none", "frame"]);
export type CalibStatus = z.infer<typeof CalibStatus>;

export const TrackFrame = z.object({
  id: z.string().optional(),
  match_id: z.string().optional(),
  t_s: z.number(),
  match_clock_s: z.number().nullish(),
  entities: z.array(Entity),
  calibrated: z.boolean().nullish(),
  calib_status: CalibStatus.nullish().catch(null),
});
export type TrackFrame = z.infer<typeof TrackFrame>;

export const Reliability = z.object({ lo: z.number(), hi: z.number(), n: z.number(), mean_p: z.number(), freq: z.number() });
export type Reliability = z.infer<typeof Reliability>;

export const HeadScore = z.object({
  n: z.number(),
  base_rate: z.number().nullish(),
  brier: z.number().nullish(),
  logloss: z.number().nullish(),
  brier_baseline: z.number().nullish(),
  brier_persistence: z.number().nullish(),
  logloss_baseline: z.number().nullish(),
  logloss_persistence: z.number().nullish(),
  reliability: z.array(Reliability).nullish(),
});
export type HeadScore = z.infer<typeof HeadScore>;

export const Outcome = z.object({
  team_side: Side,
  minute: z.number(),
  t_s: z.number().nullish(),
  type: z.string(),
  xg: z.number().nullish(),
});
export type Outcome = z.infer<typeof Outcome>;

export const ScoreDoc = z.object({
  id: z.string(),
  match_id: z.string(),
  kind: z.string().nullish(),
  scored_at: z.string().nullish(),
  heads: z.record(z.string(), HeadScore).default({}),
  outcomes: z.array(Outcome).nullish(),
});
export type ScoreDoc = z.infer<typeof ScoreDoc>;

export const Moment = z.object({
  id: z.string(),
  match_id: z.string(),
  kind: z.string(),
  team_side: Side.nullish(),
  team: z.string().nullish(),
  minute: z.number(),
  t_s: z.number(),
  match_clock_s: z.number().nullish(),
  xg: z.number().nullish(),
  player: z.string().nullish(),
  label: z.string().nullish(),
  /** Where the moment came from: "fotmob" (a recorded shot or goal) or "model" (a peak of the model's shot chance). */
  source: z.string().nullish(),
  p_shot10: z.number().nullish(),
  /** "observed": the goal second was found in the footage (within t_s_error_s), not estimated from the minute. */
  placed: z.string().nullish(),
  t_s_error_s: z.number().nullish(),
  scored_by: Side.nullish().catch(null),
  own_goal: z.boolean().nullish(),
});
export type Moment = z.infer<typeof Moment>;

/** One message on the live bridge; "stats" and "hello" are ignored by the pages. */
export const LiveMessage = z.discriminatedUnion("type", [
  z.object({ type: z.literal("prediction"), doc: Prediction }),
  z.object({ type: z.literal("frame"), doc: TrackFrame }),
]);
export type LiveMessage = z.infer<typeof LiveMessage>;

/** Live video-pipeline statistics (the socket's "stats" messages, and the `stats` of a video job). */
export const LiveStats = z.object({
  live_ready: z.boolean().nullish(),
  calibrated_share: z.number().nullish(),
  on_pitch_rate: z.number().nullish(),
  fps: z.number().nullish(),
  dropped_frames: z.number().nullish(),
});
export type LiveStats = z.infer<typeof LiveStats>;

/** Seconds on the match clock a prediction refers to. */
export function predictionT(p: Prediction): number {
  return p.t_s ?? p.minute * 60;
}

/** The shot-and-goal heads of the event-level shadow model, and the open-play heads of the Next10 model. */
export const SHADOW_HEADS = ["shot_1m", "shot_5m", "goal_5m"] as const;
export const NEXT10_HEADS = ["shot10", "goal10", "turn5", "poss10"] as const;
export type MatchPhase = "live" | "upcoming" | "finished";

/** The backend's status vocabulary is not fixed yet: classify defensively, falling back to the kickoff time. */
export function phaseOf(match: Next10Match, now = Date.now()): MatchPhase {
  const s = (match.status ?? "").toLowerCase();
  if (/(^|[^a-z])(live|inplay|in_play|in-play|progress|started|ht|half)/.test(s) || s === "1h" || s === "2h") return "live";
  if (/(finish|final|full|ft$|^ft|ended|complete|aet|pen)/.test(s)) return "finished";
  if (/(upcoming|sched|not_started|notstarted|ns$|^ns|fixture|pre|postpone)/.test(s)) return "upcoming";
  const kickoff = match.kickoff_utc ? Date.parse(match.kickoff_utc) : NaN;
  if (Number.isNaN(kickoff)) return "upcoming";
  if (now < kickoff) return "upcoming";
  return now - kickoff < 2.5 * 3600_000 ? "live" : "finished";
}

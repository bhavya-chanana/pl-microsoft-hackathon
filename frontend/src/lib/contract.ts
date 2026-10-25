/**
 * Backend contract (Matchlens FastAPI). Every payload is validated here, once,
 * at the boundary; components only see these inferred types.
 */
import { z } from "zod";

export const MODES = ["casual", "analyst", "player_focus"] as const;
export const LANGUAGES = ["en", "hi", "es"] as const;
export const CATCHUP_WINDOWS = [5, 15, 30] as const;
/** Headline metrics a fan can pin (BRD FR-031); keys match the stat fields the agents see. */
export const METRICS = ["pass_accuracy", "distance_km", "momentum", "shots", "top_speed_kmh"] as const;

export const Mode = z.enum(MODES);
export const Language = z.enum(LANGUAGES);
export type Mode = z.infer<typeof Mode>;
export type Language = z.infer<typeof Language>;
export type CatchupWindow = (typeof CATCHUP_WINDOWS)[number];
export type Metric = (typeof METRICS)[number];

const Point = z.object({ x: z.number(), y: z.number() });

export const Team = z.object({
  team_id: z.string(),
  name: z.string(),
  short_name: z.string(),
  side: z.enum(["home", "away"]),
  colour: z.string(),
  formation: z.string(),
  starting_xi: z.array(z.string()),
  bench: z.array(z.string()),
});
export type Team = z.infer<typeof Team>;

export const Player = z.object({
  player_id: z.string(),
  name: z.string(),
  short_name: z.string(),
  shirt: z.number(),
  position: z.string(),
  role: z.string().optional(),
  nationality: z.string().optional(),
  career_appearances: z.number().optional(),
  career_goals: z.number().optional(),
});
export type Player = z.infer<typeof Player>;

export const MatchMeta = z.object({
  match_id: z.string(),
  competition: z.string(),
  venue: z.string(),
  kickoff_utc: z.string(),
  home: Team,
  away: Team,
  players: z.record(z.string(), Player),
});
export type MatchMeta = z.infer<typeof MatchMeta>;

export const MatchState = z.object({
  match_id: z.string(),
  status: z.string(),
  period: z.number(),
  clock: z.number(),
  elapsed: z.number(),
  minute_label: z.string(),
  score: z.record(z.string(), z.number()),
  possession_team: z.string().nullable(),
  momentum_index: z.number(),
  rhythm: z.string().nullable().optional(),
  controlling_team: z.string().nullable().optional(),
  players_on_pitch: z.record(z.string(), z.number()),
});
export type MatchState = z.infer<typeof MatchState>;

export const MatchEvent = z.object({
  event_id: z.string(),
  sequence: z.number(),
  period: z.number(),
  elapsed: z.number(),
  type: z.string(),
  team_id: z.string().nullable(),
  player_id: z.string().nullable(),
  related_player_id: z.string().nullable(),
  start: Point.nullable(),
  end: Point.nullable(),
  outcome: z.string(),
  attrs: z.record(z.string(), z.unknown()),
  corrects_event_id: z.string().nullable(),
  minute_label: z.string(),
});
export type MatchEvent = z.infer<typeof MatchEvent>;

export const Signal = z.object({
  signal_id: z.string(),
  type: z.string(),
  elapsed: z.number(),
  minute_label: z.string(),
  team_id: z.string().nullable(),
  player_ids: z.array(z.string()),
  priority: z.number(),
  revision: z.number(),
});
export type Signal = z.infer<typeof Signal>;

const Text = z.object({ headline: z.string(), body: z.string() });
export type LocalisedText = z.infer<typeof Text>;

export const KeyNumber = z.object({
  label: z.string(),
  value: z.string(),
  unit: z.string().nullable(),
});
export type KeyNumber = z.infer<typeof KeyNumber>;

export const FactCheckStatus = z.enum(["verified", "rewritten", "blocked", "data_only"]);
export type FactCheckStatus = z.infer<typeof FactCheckStatus>;

export const ClaimResult = z.object({
  claim: z.record(z.string(), z.unknown()),
  verdict: z.string(),
  actual: z.union([z.string(), z.number()]).nullable(),
  note: z.string().nullable(),
});
export type ClaimResult = z.infer<typeof ClaimResult>;

export const FactCheck = z.object({
  status: FactCheckStatus,
  results: z.array(ClaimResult),
  unaccounted_numbers: z.array(z.string()),
  actions: z.array(z.string()),
});
export type FactCheck = z.infer<typeof FactCheck>;

export const Insight = z.object({
  insight_id: z.string(),
  signal_type: z.string(),
  revision: z.number(),
  stage: z.enum(["data", "agent", "retracted"]),
  elapsed: z.number(),
  minute_label: z.string(),
  team_id: z.string().nullable(),
  player_ids: z.array(z.string()),
  priority: z.number(),
  headline: z.string(),
  explanation: z.string().nullable(),
  why_it_matters: z.string().nullable(),
  narrative: z.string().nullable(),
  key_numbers: z.array(KeyNumber),
  evidence_event_ids: z.array(z.string()),
  variants: z.record(z.string(), z.record(z.string(), Text)),
  club_angles: z.record(z.string(), z.record(z.string(), z.string())),
  fact_check: FactCheck,
  agents: z.array(z.string()),
  latency_ms: z.number().nullable(),
});
export type Insight = z.infer<typeof Insight>;

export const OverlaySlot = z.enum(["lower", "upper_right", "banner"]);
export type OverlaySlot = z.infer<typeof OverlaySlot>;

export const Overlay = z.object({
  overlay_id: z.string(),
  insight_id: z.string(),
  revision: z.number(),
  kind: z.string(),
  title: z.string(),
  subtitle: z.string().nullable(),
  stats: z.array(KeyNumber),
  team_id: z.string().nullable(),
  focus_player_id: z.string().nullable(),
  start_elapsed: z.number(),
  duration_s: z.number(),
  priority: z.number(),
  slot: OverlaySlot,
  localized: z.record(z.string(), Text).default({}),
  stage: z.enum(["data", "agent", "retracted"]),
});
export type Overlay = z.infer<typeof Overlay>;

export const MomentumPoint = z.object({ elapsed: z.number(), index: z.number() });
export type MomentumPoint = z.infer<typeof MomentumPoint>;

export const Entity = z.object({ entity_id: z.string(), x: z.number(), y: z.number(), speed: z.number() });
export const Frame = z.object({ elapsed: z.number(), period: z.number(), entities: z.array(Entity) });
export type Frame = z.infer<typeof Frame>;
export type Entity = z.infer<typeof Entity>;

/** Text by fan language; the backend localises match content. */
export const Localized = z.record(z.string(), z.string());

export const PollOption = z.object({ option_id: z.string(), label: Localized, votes: z.number() });
export const Poll = z.object({
  poll_id: z.string(),
  kind: z.enum(["result", "motm"]),
  question: Localized,
  context: Localized,
  minute_label: z.string(),
  closed: z.boolean(),
  /** Resolved at full-time: the final result, or the data's pick for player polls. */
  answer: z.string().nullable(),
  total_votes: z.number(),
  options: z.array(PollOption),
});
export type Poll = z.infer<typeof Poll>;

export const RatingTally = z.object({ fan_rating: z.number().nullable(), fan_votes: z.number() });

export const PlayerRating = z.object({
  player_id: z.string(),
  team_id: z.string(),
  name: z.string(),
  short_name: z.string(),
  position: z.string(),
  on_pitch: z.boolean(),
  data_rating: z.number(),
  parts: z.array(z.object({ key: z.string(), delta: z.number() })),
  fan_rating: z.number().nullable(),
  fan_votes: z.number(),
  gap: z.number().nullable(),
});
export type PlayerRating = z.infer<typeof PlayerRating>;

export const Snapshot = z.object({
  type: z.literal("hello"),
  meta: MatchMeta,
  state: MatchState,
  timeline: z.array(MatchEvent),
  signals: z.array(Signal),
  insights: z.array(Insight),
  overlays: z.array(Overlay),
  momentum: z.array(MomentumPoint),
  frame: Frame.nullable(),
  finished: z.boolean(),
  run_id: z.string().optional(),
  polls: z.array(Poll).default([]),
});
export type Snapshot = z.infer<typeof Snapshot>;

/** Messages on the live stream (the server route relays the backend WebSocket). */
export const StreamMessage = z.discriminatedUnion("type", [
  Snapshot,
  z.object({ type: z.literal("frame"), frame: Frame }),
  z.object({ type: z.literal("event"), event: MatchEvent }),
  z.object({ type: z.literal("correction"), event: MatchEvent }),
  z.object({ type: z.literal("state"), state: MatchState }),
  z.object({ type: z.literal("signal"), signal: Signal }),
  z.object({ type: z.literal("insight"), insight: Insight }),
  z.object({ type: z.literal("overlay"), overlay: Overlay }),
  z.object({ type: z.literal("poll"), poll: Poll }),
  z.object({ type: z.literal("fan_tally"), polls: z.array(Poll), ratings: z.record(z.string(), RatingTally) }),
  z.object({ type: z.literal("feed_status"), status: z.string() }),
  z.object({ type: z.literal("ping") }),
]);
export type StreamMessage = z.infer<typeof StreamMessage>;

export const FeedItem = z.object({
  insight_id: z.string(),
  signal_type: z.string(),
  minute_label: z.string(),
  elapsed: z.number(),
  team_id: z.string().nullable(),
  player_ids: z.array(z.string()),
  headline: z.string(),
  body: z.string(),
  club_angle: z.string().nullable(),
  key_numbers: z.array(KeyNumber),
  stage: z.enum(["data", "agent", "retracted"]),
  fact_check: FactCheckStatus,
  favourite_player: z.boolean(),
  relevance: z.number(),
});
export type FeedItem = z.infer<typeof FeedItem>;

export const Catchup = z.object({
  window_minutes: z.number(),
  from_minute: z.string(),
  to_minute: z.string(),
  score_before: z.record(z.string(), z.number()),
  score_now: z.record(z.string(), z.number()),
  headline: z.string(),
  bullets: z.array(z.string()),
  momentum_summary: z.string().nullable(),
  spotlight_player_id: z.string().nullable(),
  fact_check: FactCheck,
  source: z.string(),
});
export type Catchup = z.infer<typeof Catchup>;

export const Recap = z.object({
  phase: z.string(),
  title: z.string(),
  summary: z.string(),
  key_moments: z.array(z.string()),
  turning_point: z.string().nullable(),
  player_of_the_match_id: z.string().nullable(),
  fact_check: FactCheck,
  source: z.string(),
});
export type Recap = z.infer<typeof Recap>;

export const Confidence = z.enum(["high", "medium", "low"]);
export type Confidence = z.infer<typeof Confidence>;

export const CoachItem = z.object({
  rank: z.number(),
  issue_id: z.string(),
  title: z.string(),
  evidence: z.string(),
  recommendation: z.string(),
  trade_off: z.string(),
  confidence: Confidence,
  key_numbers: z.array(KeyNumber),
  evidence_event_ids: z.array(z.string()),
  player_ids: z.array(z.string()),
});
export type CoachItem = z.infer<typeof CoachItem>;

export const CoachBrief = z.object({
  team_id: z.string(),
  minute: z.string(),
  language: z.string(),
  items: z.array(CoachItem),
  fact_check: FactCheck,
  source: z.string(),
  agents: z.array(z.string()),
});
export type CoachBrief = z.infer<typeof CoachBrief>;

export const PlayerStats = z.object({
  player_id: z.string(),
  team_id: z.string(),
  name: z.string(),
  on_pitch: z.boolean(),
  touches: z.number(),
  passes: z.number(),
  pass_accuracy: z.number().nullable(),
  key_passes: z.number(),
  shots: z.number(),
  shots_on_target: z.number(),
  goals: z.number(),
  assists: z.number(),
  xg: z.number(),
  tackles: z.number(),
  interceptions: z.number(),
  saves: z.number(),
  // Tracking stats are null when the player has no tracking data (unavailable is not zero).
  distance_km: z.number().nullable(),
  top_speed_kmh: z.number().nullable(),
  sprints: z.number().nullable(),
  max_shot_speed_kmh: z.number().nullable(),
  yellow_cards: z.number(),
  red_card: z.boolean(),
});
export type PlayerStats = z.infer<typeof PlayerStats>;

export const Shot = z.object({
  event_id: z.string(),
  player_id: z.string().nullable(),
  minute_label: z.string(),
  x: z.number(),
  y: z.number(),
  xg: z.number(),
  outcome: z.string(),
});
export type Shot = z.infer<typeof Shot>;

export const TeamStats = z.object({
  team_id: z.string(),
  goals: z.number(),
  possession_pct: z.number().nullable(),
  passes: z.number(),
  pass_accuracy: z.number().nullable(),
  shots: z.number(),
  shots_on_target: z.number(),
  xg: z.number(),
  corners: z.number(),
  fouls: z.number(),
  saves: z.number(),
  shot_map: z.array(Shot),
});
export type TeamStats = z.infer<typeof TeamStats>;

export const Decision = z.object({
  signal_id: z.string(),
  revision: z.number(),
  plan: z.string(),
  reason: z.string(),
});
export type Decision = z.infer<typeof Decision>;

export const Health = z.object({
  status: z.string(),
  match_active: z.boolean(),
  llm: z.string().nullable(),
  processing_ms_p95: z.number().nullable(),
  agent_insight_ms_p95: z.number().nullable(),
  /** "azure" when the backend can read insights aloud; otherwise the browser's voices are used. */
  speech: z.string().nullable().optional(),
});
export type Health = z.infer<typeof Health>;

export const ReplayStatus = z.object({ speed: z.number(), paused: z.boolean() });
export type ReplayStatus = z.infer<typeof ReplayStatus>;

export type FanProfile = {
  mode: Mode;
  language: Language;
  clubId: string | null;
  playerId: string | null;
  metric: Metric | null;
};

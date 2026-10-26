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
  overall: z.number().nullable(),
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
  archived: z.boolean().optional(),
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

/** FIFA-style card from this match's own data (never a game's ratings). */
export const MatchCard = z.object({
  kind: z.enum(["outfield", "goalkeeper"]),
  overall: z.number(),
  attributes: z.array(z.object({ key: z.string(), value: z.number().nullable() })),
});
export type MatchCard = z.infer<typeof MatchCard>;

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
  card: MatchCard.optional(),
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

/** A stored replay, as the match archive lists it. */
export const RunSummary = z.object({
  run_id: z.string(),
  match_id: z.string(),
  status: z.string(),
  match_status: z.string().optional(),
  minute_label: z.string().default("0'"),
  score: z.record(z.string(), z.number()).default({}),
  created_at: z.string(),
  source: z.string().optional(),
  teams: z.array(z.object({ team_id: z.string(), short_name: z.string() })).optional(),
});
export type RunSummary = z.infer<typeof RunSummary>;

export type FanProfile = {
  mode: Mode;
  language: Language;
  clubId: string | null;
  playerId: string | null;
  metric: Metric | null;
};

/** One telestrator stroke, in pitch metres; drawn `at_s` seconds into the replay. */
export const Drawing = z.object({
  kind: z.enum(["lane", "carry", "shot", "ring", "zone", "path", "marker"]),
  points: z.array(Point),
  at_s: z.number(),
  team_id: z.string().nullable(),
  player_id: z.string().nullable(),
  label: z.string().nullable(),
  radius: z.number().nullable(),
  emphasis: z.enum(["primary", "secondary"]),
  event_id: z.string().nullable(),
});
export type Drawing = z.infer<typeof Drawing>;

export const Moment = z.object({
  insight_id: z.string(),
  signal_type: z.string(),
  minute_label: z.string(),
  team_id: z.string().nullable(),
  player_ids: z.array(z.string()),
  start_elapsed: z.number(),
  end_elapsed: z.number(),
  focus_elapsed: z.number(),
  frames: z.array(Frame),
  drawings: z.array(Drawing),
});
export type Moment = z.infer<typeof Moment>;

export const RhythmState = z.enum(["control", "balanced", "chaos"]);
export type RhythmState = z.infer<typeof RhythmState>;

export const PulseRow = z.object({
  elapsed: z.number(),
  minute_label: z.string(),
  period: z.number(),
  momentum: z.number(),
  state: RhythmState,
  controlling_team: z.string().nullable(),
  turnovers_per_min: z.number(),
  fouls_per_min: z.number(),
  pass_share: z.record(z.string(), z.number()),
  pressure: z.record(z.string(), z.number()),
  passes: z.number(),
});
export type PulseRow = z.infer<typeof PulseRow>;

export const PulsePin = z.object({
  insight_id: z.string(),
  type: z.string(),
  elapsed: z.number(),
  minute_label: z.string(),
  team_id: z.string().nullable(),
  player_ids: z.array(z.string()),
  priority: z.number(),
  card: z.string().nullable(),
});
export type PulsePin = z.infer<typeof PulsePin>;

export const Pulse = z.object({
  home_team_id: z.string(),
  away_team_id: z.string(),
  now: z.object({
    elapsed: z.number(),
    minute_label: z.string(),
    momentum: z.number(),
    state: RhythmState.nullable(),
    controlling_team: z.string().nullable(),
  }),
  rows: z.array(PulseRow),
  pins: z.array(PulsePin),
  spells: z.array(
    z.object({
      state: RhythmState,
      team_id: z.string().nullable(),
      from_elapsed: z.number(),
      to_elapsed: z.number(),
      minutes: z.number(),
    }),
  ),
});
export type Pulse = z.infer<typeof Pulse>;

export const PunditAnswer = z.object({
  question: z.string(),
  intent: z.string(),
  answer: z.string(),
  points: z.array(z.string()),
  key_numbers: z.array(KeyNumber),
  cited_moments: z.array(
    z.object({ insight_id: z.string(), minute_label: z.string(), type: z.string(), team_id: z.string().nullable() }),
  ),
  minute_label: z.string(),
  fact_check: FactCheck,
  source: z.enum(["agent", "data"]),
  agents: z.array(z.string()),
  latency_ms: z.number().nullable(),
});
export type PunditAnswer = z.infer<typeof PunditAnswer>;

export const BEAT_TYPES = ["goal", "big_chance", "yellow_card", "red_card", "substitution"] as const;
export const Beat = z.object({
  minute: z.number(),
  type: z.enum(BEAT_TYPES),
  team_id: z.string(),
  player_id: z.string(),
  related_player_id: z.string().nullable().optional(),
  initially_credited_to: z.string().nullable().optional(),
  note: z.string().nullable().optional(),
});
export type Beat = z.infer<typeof Beat>;

export const Storyline = z.object({
  title: z.string(),
  summary: z.string(),
  phases: z.array(
    z.object({
      start_minute: z.number(),
      end_minute: z.number(),
      home_dominance: z.number(),
      label: z.string().nullable().optional(),
    }),
  ),
  beats: z.array(Beat),
  scripted_goals_only: z.boolean().optional(),
});
export type Storyline = z.infer<typeof Storyline>;

export const ScenarioPreset = z.object({ preset_id: z.string(), seed: z.number(), storyline: Storyline });
export type ScenarioPreset = z.infer<typeof ScenarioPreset>;

const RosterPlayer = z.object({
  player_id: z.string(),
  name: z.string(),
  position: z.string(),
  role: z.string(),
  starting: z.boolean(),
});
const RosterTeam = z.object({ team_id: z.string(), name: z.string(), side: z.string(), players: z.array(RosterPlayer) });
export const Scenarios = z.object({
  presets: z.array(ScenarioPreset),
  roster: z.object({ home: RosterTeam, away: RosterTeam }),
});
export type Scenarios = z.infer<typeof Scenarios>;
export type RosterTeam = z.infer<typeof RosterTeam>;

export const ScriptCheck = z.object({
  title: z.string(),
  summary: z.string(),
  finished: z.boolean(),
  beats: z.array(
    z.object({
      minute: z.number(),
      type: z.string(),
      team_id: z.string(),
      player_id: z.string(),
      related_player_id: z.string().nullable(),
      status: z.enum(["delivered", "missed", "pending"]),
      event_id: z.string().nullable(),
      minute_label: z.string().nullable(),
    }),
  ),
  delivered: z.number(),
  total: z.number(),
});
export type ScriptCheck = z.infer<typeof ScriptCheck>;

export const Kickoff = z.object({ match_id: z.string(), run_id: z.string(), title: z.string(), speed: z.number() });

/* ------------------------------------------------------------------ *
 * Tactics board. Mirrors backend/src/matchlens/tactics/{models,intent}.py.
 * Coordinates are absolute pitch metres (105 x 68), as in tracking frames.
 * ------------------------------------------------------------------ */

export const BOARD_RISKS = ["safe", "balanced", "bold"] as const;
export const BOARD_SOURCES = ["live", "moment", "elapsed"] as const;
export const ROLLOUT_VARIANTS = ["typical", "best", "worst"] as const;
export const TACTIC_STATUSES = ["thinking", "evaluating", "ready", "rejected"] as const;

export const BoardRisk = z.enum(BOARD_RISKS);
export const RolloutVariant = z.enum(ROLLOUT_VARIANTS);
export type BoardRisk = z.infer<typeof BoardRisk>;
export type RolloutVariant = z.infer<typeof RolloutVariant>;

/** What a board is built from: the live moment, a key moment from the feed, or any match time. */
export const BoardSource = z.object({
  kind: z.enum(BOARD_SOURCES),
  insight_id: z.string().optional(),
  elapsed: z.number().optional(),
});
export type BoardSource = z.infer<typeof BoardSource>;

export const BoardRequest = z.object({
  source: BoardSource,
  team_id: z.string(),
  prompt: z.string().max(400).optional(),
  n: z.number().int().min(3).max(5),
  horizon_s: z.number().min(5).max(10),
  risk: BoardRisk,
  language: z.string(),
});
export type BoardRequest = z.infer<typeof BoardRequest>;

export const PlayerSpot = z.object({
  player_id: z.string(),
  name: z.string(),
  team_id: z.string(),
  role: z.string(),
  x: z.number(),
  y: z.number(),
  tags: z.array(z.string()).default([]),
});
export type PlayerSpot = z.infer<typeof PlayerSpot>;

export const Situation = z.object({
  match_id: z.string(),
  elapsed: z.number(),
  minute_label: z.string(),
  period: z.number(),
  score: z.record(z.string(), z.number()),
  possession_team_id: z.string(),
  carrier_id: z.string().nullable(),
  ball: z.tuple([z.number(), z.number()]),
  ball_zone: z.string(),
  phase: z.string(),
  coaching_team_id: z.string(),
  players: z.array(PlayerSpot),
  frame: Frame,
});
export type Situation = z.infer<typeof Situation>;

export const Interval = z.object({ value: z.number(), low: z.number(), high: z.number() });
export type Interval = z.infer<typeof Interval>;

/** Monte-Carlo summary of the rollouts of one tactic (or the baseline). Probabilities are 0..1. */
export const OutcomeStats = z.object({
  rollouts: z.number(),
  horizon_s: z.number(),
  p_shot: Interval,
  mean_xg: z.number(),
  p_goal: Interval,
  p_box_entry: Interval,
  p_turnover: Interval,
  p_concede_shot: Interval,
  xt_delta: z.number(),
  time_to_shot_s: z.number().nullable(),
  terminals: z.record(z.string(), z.number()),
});
export type OutcomeStats = z.infer<typeof OutcomeStats>;

export const RoleDirective = z.object({
  player_id: z.string(),
  action: z.string(),
  zone: z.string().nullable(),
  target_player_id: z.string().nullable(),
  start_s: z.number(),
  end_s: z.number(),
});
export type RoleDirective = z.infer<typeof RoleDirective>;

export const BallStep = z.object({
  action: z.string(),
  from_player_id: z.string().nullable(),
  to_player_id: z.string().nullable(),
  to_zone: z.string().nullable(),
});

export const TacticIntent = z.object({
  team_id: z.string(),
  title: z.string(),
  idea: z.string(),
  mechanism: z.string(),
  directives: z.array(RoleDirective),
  ball_plan: z.array(BallStep),
  press: z
    .object({ trigger: z.string(), cover_shadow_on: z.string().nullable(), intensity: z.number() })
    .nullable(),
  style_changes: z.array(z.object({ knob: z.string(), delta: z.number() })),
  rest_defence: z.number(),
  cites: z.array(z.string()),
});
export type TacticIntent = z.infer<typeof TacticIntent>;

export const TacticStatus = z.enum(TACTIC_STATUSES);
export type TacticStatus = z.infer<typeof TacticStatus>;

export const TacticResult = z.object({
  tactic_id: z.string(),
  persona: z.string(),
  intent: TacticIntent,
  outcome: OutcomeStats.nullable().default(null),
  /** Metric (an OutcomeStats field name) to change against the baseline; probabilities as 0..1 differences. */
  lift: z.record(z.string(), z.number()).default({}),
  utility: z.number().nullable().default(null),
  rank: z.number().nullable().default(null),
  status: TacticStatus,
  problems: z.array(z.string()).default([]),
  confidence: z.enum(["low", "medium", "high"]).nullable().default(null),
});
export type TacticResult = z.infer<typeof TacticResult>;

export const Verdict = z.object({
  headline: z.string(),
  text: z.string(),
  recommended_tactic_id: z.string().nullable(),
  cited_metrics: z.array(z.string()),
});
export type Verdict = z.infer<typeof Verdict>;

export const TacticsBoard = z.object({
  board_id: z.string(),
  status: z.enum(["thinking", "evaluating", "done", "failed"]),
  situation: Situation,
  prompt: z.string().nullable(),
  risk: BoardRisk,
  horizon_s: z.number(),
  baseline: OutcomeStats.nullable().default(null),
  tactics: z.array(TacticResult).default([]),
  verdict: Verdict.nullable().default(null),
  evaluator: z.string(),
  model_label: z.string(),
  recorded: z.boolean().default(false),
  errors: z.array(z.string()).default([]),
});
export type TacticsBoard = z.infer<typeof TacticsBoard>;

export const BoardCreated = z.object({ board_id: z.string(), situation: Situation });
export type BoardCreated = z.infer<typeof BoardCreated>;

/** An intent stroke over a rollout (named apart from the telestrator `Drawing`). */
export const TacticDrawing = z.object({
  kind: z.enum(["path", "zone", "lane", "shot", "arrow"]),
  team_id: z.string().nullable(),
  points: z.array(z.tuple([z.number(), z.number()])),
  label: z.string().nullable().default(null),
});
export type TacticDrawing = z.infer<typeof TacticDrawing>;

export const RolloutEvent = z.object({
  event_id: z.string(),
  elapsed: z.number(),
  type: z.string(),
  team_id: z.string().nullable().default(null),
  player_id: z.string().nullable().default(null),
  related_player_id: z.string().nullable().default(null),
  outcome: z.string().default(""),
});
export type RolloutEvent = z.infer<typeof RolloutEvent>;

export const RolloutView = z.object({
  tactic_id: z.string(),
  variant: RolloutVariant,
  seed: z.number(),
  frames: z.array(Frame),
  events: z.array(RolloutEvent),
  drawings: z.array(TacticDrawing),
  terminal: z.string(),
});
export type RolloutView = z.infer<typeof RolloutView>;

/** The do-nothing branch every tactic is compared with. */
export const BASELINE_ID = "baseline";

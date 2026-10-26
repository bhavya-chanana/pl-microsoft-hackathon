export type Persona = "diehard" | "casual" | "tactical" | "fantasy";
export type Side = "home" | "away";
export type Source = "vest" | "ball" | "fusion" | "llm_vision" | "operator" | "model";
export type EventStatus = "provisional" | "confirmed" | "overturned";

export type Team = { id: Side; name: string; short: string };
export type Player = {
  id: string;
  name: string;
  shirt: number;
  team: Side;
  position: "GK" | "DF" | "MF" | "FW";
  /** Nominal pitch position, 0–100 along length (own goal → opponent goal), 0–100 across. */
  base: { x: number; y: number };
  startsOn: boolean;
  /** Match ms the player enters or leaves (substitutions). */
  onFromMs?: number;
  offAtMs?: number;
};

export type EventType =
  | "kickoff" | "shot" | "save" | "goal" | "big_chance" | "foul" | "yellow" | "red"
  | "sub" | "corner" | "offside" | "var" | "press_win" | "sprint" | "half_time" | "full_time";

export type Revision = { availableMs: number; status: EventStatus; note?: string };

export type MatchEvent = {
  id: string;
  type: EventType;
  /** When it happened on the video timeline (ms from kickoff). */
  atMs: number;
  team?: Side;
  playerIds: string[];
  source: Source;
  critical: boolean;
  revisions: Revision[];
  headline: string;
  copy: Record<Persona, string>;
  fantasyPoints?: Record<string, number>;
  /** Field position (same 0–100 frame as Player.base, from the attacking team's view). */
  at?: { x: number; y: number };
  xg?: number;
};

export type MatchStatus = "live" | "scheduled" | "completed";
export type MatchSummary = {
  id: string;
  competition: string;
  venue: string;
  kickoffLabel: string;
  status: MatchStatus;
  home: Team;
  away: Team;
  /** For live matches: the live edge when the demo opened. */
  liveEdgeStartMs?: number;
  durationMs: number;
};

// --- API shapes (backend /api/fi) -----------------------------------------------------------------------------

export type MatchListItem = MatchSummary & { score: Score | null };
export type Score = Record<Side, number> & { provisional: boolean };
export type PublicPlayer = Omit<Player, "onFromMs" | "offAtMs">;
export type MatchDetail = MatchSummary & { players: PublicPlayer[] };

/** A canonical event as of the viewer's playhead: latest known status, and analysis once it is ready. */
export type VisibleEvent = Omit<MatchEvent, "copy"> & {
  status: EventStatus;
  note?: string | null;
  copy: Record<Persona, string> | null;
  analysis: "ready" | "pending";
};

export type TeamStat = { home: number | null; away: number | null; unit?: string | null; source: Source };
export type MatchView = {
  matchId: string; playheadMs: number; score: Score; events: VisibleEvent[];
  pulse: Array<{ minute: number; value: number }>; chips: Record<string, TeamStat>; statsThroughMs: number;
};

export type Period = "all" | "h1" | "h2";
export type ShotOutcome = "goal" | "saved" | "off" | "blocked" | "woodwork";
export type Shot = {
  id: string; atMs: number; availableMs: number; team: Side; playerId: string;
  /** 0–100 towards the opponent goal, 0–100 across. */
  x: number; y: number;
  xg: number; xgot: number | null; outcome: ShotOutcome;
  body: "Right foot" | "Left foot" | "Header"; situation: "Open play" | "Set piece" | "Counter";
  speedKmh: number;
};
export type Row = { label: string; home: number | null; away: number | null; fmt?: "pct" | "km" | "dec" | "kmh" | "bpm" | "ofTotal";
  homeTotal?: number; awayTotal?: number; lowerIsBetter?: boolean };
export type Section = { id: string; title: string; source: Source; rows: Row[] };
export type PlayerRow = {
  id: string; name: string; shirt: number; team: Side; position: string; rating: number | null;
  values: Record<string, number | null>;
};
export type MatchStats = {
  playheadMs: number; throughMs: number; physicalThroughMs: number; period: Period; refreshedAt: string;
  possession: [number, number]; sections: Section[]; zones: Record<Side, [number, number, number]>;
  shots: Shot[]; players: PlayerRow[];
};

export type StatValue = { value: number | null; unit?: string | null; source: Source };
export type PlayerDetail = {
  player: PublicPlayer; throughMs: number; physicalThroughMs: number; refreshedAt: string;
  stats: Record<string, StatValue>; heatmap: number[][]; fantasy: number; moments: VisibleEvent[];
};

export type Preferences = {
  persona: Persona; favoriteTeam: string | null; followedPlayers: string[]; spoilersVisible: boolean; language: string; onboarded: boolean;
};

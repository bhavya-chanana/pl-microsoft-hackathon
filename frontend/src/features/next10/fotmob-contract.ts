import { z } from "zod";

/**
 * The FotMob match page, as the backend normalises it (GET /api/next10/matches/{id}/fotmob). Every block is optional
 * and parsed leniently: a block that is missing or malformed is hidden, never faked.
 */
const num = z.number().nullish();
const str = z.string().nullish();

const Team = z.object({
  id: num,
  name: z.string(),
  score: num,
  colour: str,
  colour_dark: str,
  text_colour: str,
  red_cards: num,
});

export const FotmobHeader = z.object({
  home: Team,
  away: Team,
  score_str: str,
  status: z.object({ state: str, label: str, minute: num, added: num, clock: str, period: num }).nullish(),
  competition: str,
  round: str,
  kickoff_utc: str,
  venue: z.object({ name: str, city: str, country: str, capacity: num, surface: str }).nullish(),
  referee: z.object({ name: str, country: str }).nullish(),
  penalties: z.object({ home: num, away: num }).nullish().catch(null),
  attendance: num,
  weather: z
    .object({ temperature_c: num, description: str, wind_kph: num, wind_dir: str, humidity: num, rain_mm: num, cloud_cover: num, icon_code: num })
    .nullish(),
});

const Side = z.enum(["home", "away"]);

const Person = z.object({ id: num, name: str }).nullish();

export const FotmobEvent = z.object({
  minute: z.number(),
  added: num,
  team: Side.nullish().catch(null),
  type: z.string(),
  player: Person,
  assist: Person,
  card: str,
  reason: str,
  player_in: Person,
  player_out: Person,
  injury: z.boolean().nullish(),
  penalty: z.boolean().nullish(),
  own_goal: z.boolean().nullish(),
  shootout: z.boolean().nullish(),
  score: z.array(z.number()).nullish(),
  xg: num,
  label: str,
});
export type FotmobEvent = z.infer<typeof FotmobEvent>;

export const StatRow = z.object({
  title: z.string(),
  key: str,
  home: z.union([z.number(), z.string()]).nullish(),
  away: z.union([z.number(), z.string()]).nullish(),
  home_text: str,
  away_text: str,
  home_total: num,
  away_total: num,
  format: str,
  highlighted: str,
});
export type StatRow = z.infer<typeof StatRow>;

export const StatGroup = z.object({ title: z.string(), key: str, rows: z.array(z.unknown()) });

export const FotmobShot = z.object({
  id: z.union([z.number(), z.string()]),
  team: Side,
  player_id: num,
  player: str,
  minute: z.number(),
  added: num,
  period: str,
  x: z.number(),
  y: z.number(),
  xg: num,
  xgot: num,
  outcome: str,
  event_type: str,
  situation: str,
  shot_type: str,
  on_target: z.boolean().nullish(),
  blocked: z.boolean().nullish(),
  own_goal: z.boolean().nullish(),
});
export type FotmobShot = z.infer<typeof FotmobShot>;

export const Player = z.object({
  id: num,
  name: z.string(),
  shirt: z.union([z.number(), z.string()]).nullish(),
  position: str,
  rating: num,
  goals: num,
  assists: num,
  yellow_cards: num,
  red_cards: num,
  own_goals: num,
  subbed: z.unknown().nullish(),
  layout: z.object({ x: z.number(), y: z.number() }).nullish(),
});
export type Player = z.infer<typeof Player>;

export const Lineup = z.object({
  id: num,
  name: str,
  formation: str,
  rating: num,
  coach: z.object({ id: num, name: str, country: str }).nullish(),
  starters: z.array(z.unknown()),
  subs: z.array(z.unknown()).nullish(),
});

const Thirds = z.object({ left: z.number(), center: z.number(), right: z.number() });
const Zones = z.object({ total: Thirds.nullish(), first_half: Thirds.nullish(), second_half: Thirds.nullish() });

const FormEntry = z.object({
  result: z.string(),
  date: str,
  opponent: z.object({ id: num, name: str }).nullish(),
  venue: str,
  goals_for: num,
  goals_against: num,
  score: str,
});
export type FormEntry = z.infer<typeof FormEntry>;

const H2HMatch = z.object({
  date: str,
  competition: str,
  home: z.object({ id: num, name: str }).nullish(),
  away: z.object({ id: num, name: str }).nullish(),
  home_goals: num,
  away_goals: num,
  score: str,
  result_for_home: str,
});

/** Parse each item of a list, dropping the ones that do not match. */
function items<S extends z.ZodType>(schema: S) {
  return z.array(z.unknown()).transform((list) => list.flatMap((x) => {
    const r = schema.safeParse(x);
    return r.success ? [r.data as z.infer<S>] : [];
  }));
}

export const ShootoutKick = z.object({
  order: z.number(),
  team_side: Side,
  player: Person,
  scored: z.boolean(),
  score: z.array(z.number()).nullish(),
});
export type ShootoutKick = z.infer<typeof ShootoutKick>;

export const FotmobDoc = z.object({
  match_id: str,
  fetched_at: str,
  header: FotmobHeader.nullish().catch(null),
  events: items(FotmobEvent).nullish().catch(null),
  stats: z
    .object({
      all: z.array(StatGroup).nullish(),
      first_half: z.array(StatGroup).nullish(),
      second_half: z.array(StatGroup).nullish(),
    })
    .nullish()
    .catch(null),
  momentum: items(z.object({ minute: z.number(), value: num })).nullish().catch(null),
  shotmap: items(FotmobShot).nullish().catch(null),
  lineups: z.object({ home: Lineup.nullish(), away: Lineup.nullish() }).nullish().catch(null),
  attacking_zones: z.object({ home: Zones.nullish(), away: Zones.nullish() }).nullish().catch(null),
  h2h: z
    .object({ summary: z.object({ home_wins: z.number(), draws: z.number(), away_wins: z.number() }).nullish(), last: items(H2HMatch).nullish() })
    .nullish()
    .catch(null),
  shootout: items(ShootoutKick).nullish().catch(null),
  team_form: z.object({ home: items(FormEntry).nullish(), away: items(FormEntry).nullish() }).nullish().catch(null),
});
export type FotmobDoc = z.infer<typeof FotmobDoc>;

export function statRowsOf(group: z.infer<typeof StatGroup>): StatRow[] {
  return group.rows.flatMap((r) => {
    const p = StatRow.safeParse(r);
    return p.success ? [p.data] : [];
  });
}

export function playersOf(list: unknown[] | null | undefined): Player[] {
  return (list ?? []).flatMap((p) => {
    const r = Player.safeParse(p);
    return r.success ? [r.data] : [];
  });
}

export const regularShots = (doc: FotmobDoc | null | undefined): FotmobShot[] => (doc?.shotmap ?? []).filter((s) => s.period !== "PenaltyShootout");

/** The shootout in kick order, with the tally (from the header, else counted). Null when the match had none. */
export function shootoutOf(doc: FotmobDoc | null | undefined): { kicks: ShootoutKick[]; home: number; away: number } | null {
  const kicks = (doc?.shootout ?? []).slice().sort((a, b) => a.order - b.order);
  if (kicks.length === 0) return null;
  const count = (side: "home" | "away") => kicks.filter((k) => k.team_side === side && k.scored).length;
  return { kicks, home: doc?.header?.penalties?.home ?? count("home"), away: doc?.header?.penalties?.away ?? count("away") };
}

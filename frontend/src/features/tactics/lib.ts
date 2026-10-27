import type { OutcomeStats, PlayerSpot, RoleDirective, Situation, TacticResult } from "@/lib/contract";
import { copy } from "@/lib/copy";

const t = copy.tactics;

export type Roster = Map<string, PlayerSpot>;

export function rosterOf(situation: Situation): Roster {
  return new Map(situation.players.map((p) => [p.player_id, p]));
}

export function surname(name: string): string {
  const parts = name.trim().split(/\s+/);
  return parts[parts.length - 1] ?? name;
}

export function nameOf(roster: Roster, id: string | null | undefined, short = false): string {
  if (!id) return "";
  const p = roster.get(id);
  if (!p) return id === "goal" ? "" : t.events.unknownPlayer;
  return short ? surname(p.name) : p.name;
}

/** "snake_case" to "Snake case", for keys the copy does not know yet. */
export function prettify(key: string): string {
  const s = key.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function labelOf(map: Record<string, string>, key: string): string {
  return map[key] ?? prettify(key);
}

/** "right_half:att" becomes "right half-space, attacking third"; named spots keep their own label. */
export function zoneLabel(zone: string): string {
  const spot = t.zones.spots[zone as keyof typeof t.zones.spots];
  if (spot) return spot;
  const [lane, third] = zone.split(":");
  const l = t.zones.lanes[lane];
  const th = t.zones.thirds[third];
  return l && th ? `${l}, ${th}` : prettify(zone);
}

/** Actions that read better without "to" before the zone (an empty string drops the word). */
const ZONE_PREPOSITION: Record<string, string> = { attack_box: "", screen_lane: "", rest_defence: "", hold_width: "at", overlap: "into", underlap: "into", drop_deep: "into" };

export function directiveText(d: RoleDirective, roster: Roster): { who: string; what: string; when: string } {
  const verb = labelOf(t.actions, d.action);
  const parts = [verb];
  if (d.target_player_id) parts.push(`${t.cards.on} ${nameOf(roster, d.target_player_id)}`);
  if (d.zone) parts.push(`${ZONE_PREPOSITION[d.action] ?? t.cards.toZone} ${zoneLabel(d.zone)}`.trim());
  return { who: nameOf(roster, d.player_id), what: parts.join(" "), when: t.cards.seconds(d.start_s, d.end_s) };
}

export const pct = (n: number): string => `${Math.round(n * 100)}%`;

/** Points of probability, signed, for a lift value. */
export function liftText(metric: string, value: number): string {
  return metric === "mean_xg" || metric === "xt_delta" ? t.outcome.xgDelta(value) : t.outcome.points(value * 100);
}

export type Tone = "good" | "bad" | "flat";

export function liftTone(metric: string, value: number): Tone {
  const cut = metric === "mean_xg" || metric === "xt_delta" ? 0.005 : 0.01;
  if (Math.abs(value) < cut) return "flat";
  const good = t.outcome.goodWhen[metric] ?? "up";
  return (value > 0) === (good === "up") ? "good" : "bad";
}

export const TONE_CLASS: Record<Tone, string> = {
  good: "text-verified",
  bad: "text-blocked",
  flat: "text-muted-foreground",
};

/** Probability metrics and their stored interval, in display order. */
export const PROB_METRICS = ["p_shot", "p_box_entry", "p_goal", "p_turnover", "p_concede_shot"] as const;
export type ProbMetric = (typeof PROB_METRICS)[number];

export function metricValue(stats: OutcomeStats, metric: string): number {
  switch (metric) {
    case "mean_xg":
      return stats.mean_xg;
    case "xt_delta":
      return stats.xt_delta;
    default:
      return (stats as unknown as Record<string, { value: number } | undefined>)[metric]?.value ?? 0;
  }
}

export function isReady(tactic: TacticResult): boolean {
  return tactic.status === "ready";
}

export function formatMetric(metric: string, value: number): string {
  return metric === "mean_xg" || metric === "xt_delta" ? value.toFixed(2) : pct(value);
}

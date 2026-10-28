import type { Entity, Next10Match, Prediction, Side, TrackFrame } from "./contract";
import { predictionT } from "./contract";

export const pct = (p: number | null | undefined, digits = 0) => (p === null || p === undefined || Number.isNaN(p) ? "–" : `${(p * 100).toFixed(digits)}%`);

export function minuteLabel(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = Math.floor(totalSeconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** The document closest in time to t (seconds), or null if none lies within `within` seconds. */
export function nearestByTime<T>(items: readonly T[], t: number, time: (item: T) => number, within = Infinity): T | null {
  let best: T | null = null;
  let bestD = Infinity;
  for (const item of items) {
    const d = Math.abs(time(item) - t);
    if (d < bestD) [best, bestD] = [item, d];
  }
  return best && bestD <= within ? best : null;
}

export const nearestFrame = (frames: readonly TrackFrame[], t: number, within = 3) => nearestByTime(frames, t, (f) => f.t_s, within);
export const nearestPrediction = (docs: readonly Prediction[], t: number, within = 8) => nearestByTime(docs, t, predictionT, within);

/** The side with the ball in a Next10 forecast: "home"/"away" directly, or a club id/name matched against the match. */
export function possessionSide(doc: Prediction | null, match: Next10Match | null): Side | null {
  const id = doc?.possession_team_id;
  if (!id) return null;
  if (id === "home" || id === "away") return id;
  if (!match) return null;
  for (const side of ["home", "away"] as const) {
    const club = match[side];
    if (id === String(club.fotmob_id ?? "") || id === club.name || id === `fotmob:${club.fotmob_id}`) return side;
  }
  return null;
}

export function entitiesOf(frame: TrackFrame | null): Entity[] {
  return frame?.entities ?? [];
}

export function shortTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function encodeId(matchId: string): string {
  return encodeURIComponent(matchId);
}

import { cadence } from "./polling";
import type { SnapshotTiming } from "./types";

export const formatClock = (ms: number) => {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m >= 90) return `90+${m - 89}'`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
};

/** Latest stats snapshot boundary at or before the playhead (mirrors the backend). */
export function snapshotThrough(t: number, timing?: SnapshotTiming): number {
  const lag = timing?.lagMs ?? cadence.snapshotLagMs;
  const every = timing?.fullMs ?? cadence.fullSnapshotMs;
  return Math.floor(Math.max(0, t - lag) / every) * every;
}

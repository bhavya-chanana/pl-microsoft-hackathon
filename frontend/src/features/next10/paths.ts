import type { Entity, Prediction } from "./contract";
import type { PathLine } from "./pitch";

/**
 * Predicted paths from a Next10 forecast. path[i] is the position at (i + 1) * dt_s; the current position is
 * prepended so each line starts where the entity is. Mode k is one joint scenario for every entity.
 */
export function pathsFrom(doc: Prediction | null, entities: Entity[], opts: { modes: "best" | "all" | "alternatives"; prefix?: string }): PathLine[] {
  const set = doc?.trajectories;
  if (!set) return [];
  const byId = new Map(entities.map((e) => [e.id, e]));
  const lines: PathLine[] = [];
  for (const t of set.entities) {
    const start = byId.get(t.entity_id);
    // Tracker ids churn from frame to frame: a line for an entity that is not drawn would start from nowhere.
    if (!start) continue;
    const tone = t.entity_id === "ball" ? "ball" : start.team === "away" ? "away" : "home";
    t.modes.forEach((mode, k) => {
      if (opts.modes === "best" && k > 0) return;
      if (opts.modes === "alternatives" && k === 0) return;
      if (mode.path.length === 0) return;
      const points: Array<[number, number]> = [[start.x, start.y], ...mode.path];
      lines.push({ key: `${opts.prefix ?? "p"}-${t.entity_id}-${k}`, points, weight: opts.modes === "best" ? 0.85 : Math.min(1, Math.max(0.1, mode.p * 1.4)), tone, dashed: k > 0 });
    });
  }
  return lines;
}

export function hasAlternatives(doc: Prediction | null): boolean {
  return Boolean(doc?.trajectories?.entities.some((t) => t.modes.length > 1));
}

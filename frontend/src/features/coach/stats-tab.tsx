"use client";

import { ShotMapPanel } from "@/features/stats/shot-map";
import { TeamStatRows } from "@/features/stats/stat-rows";

/** Team comparison with a period filter, then the shot map. */
export function StatsTab() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 py-4">
      <TeamStatRows coach />
      <ShotMapPanel />
    </div>
  );
}

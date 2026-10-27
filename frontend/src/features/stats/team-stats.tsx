"use client";

import { useAnalystWording } from "@/features/coach/use-analyst";
import { RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { usePlayerStats } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import { copy } from "@/lib/copy";
import { formatNumber, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

import { TeamStatRows } from "./stat-rows";

const L = copy.stats.labels;

function Failed({ retry }: { retry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-3">
      <p className="text-sm">{copy.stats.error}</p>
      <Button variant="outline" className="min-h-11 px-4" onClick={retry}>
        <RotateCw aria-hidden />
        {copy.connection.retry}
      </Button>
    </div>
  );
}

export function TeamComparison() {
  return <TeamStatRows />;
}

export function PlayerCard() {
  const analyst = useAnalystWording();
  const meta = useLive((s) => s.meta);
  const playerId = useProfile((s) => s.playerId);
  const query = usePlayerStats(playerId);
  if (!playerId) {
    return null;
  }
  const info = meta?.players[playerId];
  if (query.isPending) return <Skeleton className="h-48 w-full" aria-hidden />;
  if (!query.data) return <Failed retry={() => void query.refetch()} />;
  const p = query.data;
  const side = meta && p.team_id === meta.home.team_id ? "home" : "away";
  const rows: [string, string][] = [
    [L.goals, String(p.goals)],
    [L.assists, String(p.assists)],
    [L.shots, `${p.shots} (${p.shots_on_target})`],
    [analyst ? "xG" : L.xg, p.xg.toFixed(2)],
    [L.touches, String(p.touches)],
    [L.passAccuracy, p.pass_accuracy === null ? "–" : `${formatNumber(p.pass_accuracy)}%`],
    [L.keyPasses, String(p.key_passes)],
    [L.distance, p.distance_km === null ? "–" : `${p.distance_km.toFixed(1)} km`],
    [L.topSpeed, p.top_speed_kmh === null ? "–" : `${formatNumber(p.top_speed_kmh, 1)} km/h`],
    [L.sprints, p.sprints === null ? "–" : String(p.sprints)],
  ];
  if (info?.career_goals !== undefined) rows.push([L.careerGoals, String(info.career_goals + p.goals)]);
  return (
    <section aria-labelledby="player-card" className="relative overflow-hidden rounded-lg border bg-card p-4 shadow-raised">
      <span aria-hidden className={cn("absolute inset-x-0 top-0 h-1", sideTone[side].bg)} />
      <div className="flex items-baseline gap-3">
        <span className={cn("font-display text-score font-bold tabular-nums", sideTone[side].text)}>{info?.shirt}</span>
        <div className="flex flex-col">
          <h3 id="player-card" className="font-display text-xl font-semibold">
            {p.name}
          </h3>
          <span className="text-xs text-muted-foreground">
            {info?.position} {meta && (side === "home" ? meta.home.name : meta.away.name)}
          </span>
        </div>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex flex-col">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="font-display text-lg font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

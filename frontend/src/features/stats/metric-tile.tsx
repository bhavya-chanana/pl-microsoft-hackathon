"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { usePlayerStats, useTeamStats } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import type { MatchMeta, Metric, PlayerStats, TeamStats } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { formatNumber, sideOf, sideTone, type Side } from "@/lib/match";
import { cn } from "@/lib/utils";

const T = copy.metricTile;

type Reading = { value: string; unit?: string };

function playerReading(metric: Metric, p: PlayerStats): Reading | null {
  switch (metric) {
    case "pass_accuracy":
      return p.pass_accuracy === null ? { value: "–" } : { value: formatNumber(p.pass_accuracy), unit: "%" };
    case "distance_km":
      return { value: p.distance_km.toFixed(1), unit: "km" };
    case "top_speed_kmh":
      return p.top_speed_kmh === null ? { value: "–" } : { value: formatNumber(p.top_speed_kmh, 1), unit: "km/h" };
    case "shots":
      return { value: String(p.shots) };
    case "momentum":
      return null;
  }
}

function teamReading(metric: Metric, t: TeamStats): Reading | null {
  if (metric === "pass_accuracy") {
    return t.pass_accuracy === null ? { value: "–" } : { value: formatNumber(t.pass_accuracy), unit: "%" };
  }
  if (metric === "shots") return { value: String(t.shots) };
  return null;
}

/** Team colour sits in a swatch beside the label, so the number keeps full contrast in both themes. */
function Value({ reading, side, label }: { reading: Reading; side?: Side; label: string }) {
  return (
    <div className="flex flex-col">
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        {side && <span aria-hidden className={cn("h-3 w-1 rounded-full", sideTone[side].bg)} />}
        {label}
      </span>
      <span className="font-display text-score font-bold tabular-nums">
        {reading.value}
        {reading.unit && <span className="ml-1 text-base font-medium text-muted-foreground">{reading.unit}</span>}
      </span>
    </div>
  );
}

function Momentum({ meta }: { meta: MatchMeta }) {
  const index = useLive((s) => s.state?.momentum_index ?? 0);
  const clamped = Math.max(-100, Math.min(100, index));
  const leader = Math.abs(clamped) < 10 ? null : clamped > 0 ? meta.home : meta.away;
  const side = leader ? sideOf(meta, leader.team_id) ?? undefined : undefined;
  return (
    <Value
      label={leader ? T.onTop(leader.short_name) : T.level}
      reading={{ value: formatNumber(Math.abs(clamped)), unit: "/ 100" }}
      side={side}
    />
  );
}

function Teams({ metric, meta }: { metric: Metric; meta: MatchMeta }) {
  const home = useTeamStats(meta.home.team_id);
  const away = useTeamStats(meta.away.team_id);
  if (!home.data || !away.data) return <Skeleton className="h-14 w-40" aria-hidden />;
  const h = teamReading(metric, home.data);
  const a = teamReading(metric, away.data);
  if (!h || !a) return null;
  return (
    <div className="flex items-end gap-6">
      <Value label={meta.home.short_name} reading={h} side="home" />
      <Value label={meta.away.short_name} reading={a} side="away" />
    </div>
  );
}

function Player({ metric, playerId, meta }: { metric: Metric; playerId: string; meta: MatchMeta }) {
  const stats = usePlayerStats(playerId);
  if (!stats.data) return <Skeleton className="h-14 w-28" aria-hidden />;
  const reading = playerReading(metric, stats.data);
  if (!reading) return null;
  const player = meta.players[playerId];
  return <Value label={player?.short_name ?? T.player} reading={reading} side={sideOf(meta, stats.data.team_id) ?? undefined} />;
}

/** The fan's chosen headline metric (BRD FR-031), always in view in the match centre. */
export function MetricTile({ className }: { className?: string }) {
  const metric = useProfile((s) => s.metric);
  const playerId = useProfile((s) => s.playerId);
  const meta = useLive((s) => s.meta);
  if (!metric || !meta) return null;
  const label = copy.metrics[metric];
  const teamLevel = metric === "pass_accuracy" || metric === "shots";
  const playerLevel = metric !== "momentum";
  return (
    <section
      aria-labelledby="metric-heading"
      className={cn("flex flex-wrap items-end justify-between gap-x-8 gap-y-3 rounded-lg border bg-card px-4 py-3 shadow-raised", className)}
    >
      <div className="flex flex-col gap-0.5">
        <span className="text-xs text-muted-foreground">{T.heading}</span>
        <h2 id="metric-heading" className="font-display text-xl font-semibold">
          {label}
        </h2>
      </div>
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        {metric === "momentum" && <Momentum meta={meta} />}
        {teamLevel && <Teams metric={metric} meta={meta} />}
        {playerLevel && playerId && (
          <div className={cn(teamLevel && "border-l pl-8")}>
            <Player metric={metric} playerId={playerId} meta={meta} />
          </div>
        )}
        {playerLevel && !teamLevel && !playerId && (
          <p className="max-w-xs text-sm text-muted-foreground">{T.pickPlayer(label)}</p>
        )}
      </div>
    </section>
  );
}

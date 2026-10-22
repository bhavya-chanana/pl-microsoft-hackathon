"use client";

import { RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { usePlayerStats, useTeamStats } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import type { MatchMeta, Shot, TeamStats } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { formatNumber, playerName, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

const L = copy.stats.labels;

function StatRow({ label, home, away, digits = 0, unit = "" }: { label: string; home: number; away: number; digits?: number; unit?: string }) {
  const total = home + away || 1;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-display text-lg font-semibold tabular-nums">
          {formatNumber(home, digits)}
          {unit}
        </span>
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="font-display text-lg font-semibold tabular-nums">
          {formatNumber(away, digits)}
          {unit}
        </span>
      </div>
      <div aria-hidden className="flex h-1.5 gap-0.5 overflow-hidden rounded-full">
        <span className="bg-home" style={{ flexGrow: home / total || 0.0001 }} />
        <span className="bg-away" style={{ flexGrow: away / total || 0.0001 }} />
      </div>
    </div>
  );
}

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
  const meta = useLive((s) => s.meta);
  const home = useTeamStats(meta?.home.team_id ?? null);
  const away = useTeamStats(meta?.away.team_id ?? null);
  if (!meta || home.isPending || away.isPending) {
    return (
      <div className="flex flex-col gap-4" aria-hidden>
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-9 w-full" />
        ))}
      </div>
    );
  }
  if (!home.data || !away.data) return <Failed retry={() => void Promise.all([home.refetch(), away.refetch()])} />;
  const h = home.data;
  const a = away.data;
  return (
    <section aria-labelledby="team-stats" className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className={cn("font-display text-lg font-semibold", sideTone.home.text)}>{meta.home.short_name}</span>
        <h3 id="team-stats" className="text-sm font-medium">
          {copy.stats.team}
        </h3>
        <span className={cn("font-display text-lg font-semibold", sideTone.away.text)}>{meta.away.short_name}</span>
      </div>
      <StatRow label={L.possession} home={h.possession_pct} away={a.possession_pct} unit="%" />
      <StatRow label={L.shots} home={h.shots} away={a.shots} />
      <StatRow label={L.onTarget} home={h.shots_on_target} away={a.shots_on_target} />
      <StatRow label={L.xg} home={h.xg} away={a.xg} digits={2} />
      <StatRow label={L.passes} home={h.passes} away={a.passes} />
      <StatRow label={L.corners} home={h.corners} away={a.corners} />
      <StatRow label={L.fouls} home={h.fouls} away={a.fouls} />
      <ShotMap meta={meta} home={h} away={a} />
    </section>
  );
}

function ShotMap({ meta, home, away }: { meta: MatchMeta; home: TeamStats; away: TeamStats }) {
  const shots: { shot: Shot; side: "home" | "away" }[] = [
    ...home.shot_map.map((shot) => ({ shot, side: "home" as const })),
    ...away.shot_map.map((shot) => ({ shot, side: "away" as const })),
  ];
  const goals = shots.filter((s) => s.shot.outcome === "goal").length;
  return (
    <figure className="mt-2 flex flex-col gap-2">
      <figcaption className="flex flex-col gap-0.5">
        <span className="text-sm font-medium">{copy.stats.shotMap}</span>
        <span className="text-xs text-muted-foreground">
          {copy.stats.shotMapHelp} {shots.length} shots, {goals} goals.
        </span>
      </figcaption>
      <svg viewBox="-2 -2 109 72" className="w-full rounded-md" role="img" aria-label={`${copy.stats.shotMap}: ${meta.home.name} ${home.shots} shots, ${meta.away.name} ${away.shots} shots`}>
        <rect x={-2} y={-2} width={109} height={72} rx={1} className="fill-turf" />
        <g className="fill-none stroke-chalk" strokeWidth={0.3}>
          <rect width={105} height={68} />
          <line x1={52.5} y1={0} x2={52.5} y2={68} />
          <rect x={0} y={13.84} width={16.5} height={40.32} />
          <rect x={88.5} y={13.84} width={16.5} height={40.32} />
        </g>
        {shots.map(({ shot, side }) => {
          const goal = shot.outcome === "goal";
          const r = 0.9 + Math.sqrt(shot.xg) * 3;
          return (
            <circle
              key={shot.event_id}
              cx={shot.x}
              cy={shot.y}
              r={r}
              className={cn(goal ? sideTone[side].fill : "fill-none", sideTone[side].stroke)}
              strokeWidth={goal ? 0.3 : 0.5}
            >
              <title>
                {`${shot.minute_label} ${playerName(meta, shot.player_id)}, xG ${shot.xg.toFixed(2)}, ${goal ? copy.stats.goal : shot.outcome.replaceAll("_", " ")}`}
              </title>
            </circle>
          );
        })}
      </svg>
      <p className="flex gap-4 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-3 rounded-full bg-foreground" /> {copy.stats.goal}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-3 rounded-full border-2 border-foreground" /> {copy.stats.otherShot}
        </span>
      </p>
    </figure>
  );
}

export function PlayerCard() {
  const meta = useLive((s) => s.meta);
  const playerId = useProfile((s) => s.playerId);
  const query = usePlayerStats(playerId);
  if (!playerId) {
    return <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{copy.stats.pickPlayer}</p>;
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
    [L.xg, p.xg.toFixed(2)],
    [L.touches, String(p.touches)],
    [L.passAccuracy, p.pass_accuracy === null ? "–" : `${formatNumber(p.pass_accuracy)}%`],
    [L.keyPasses, String(p.key_passes)],
    [L.distance, `${p.distance_km.toFixed(1)} km`],
    [L.topSpeed, `${formatNumber(p.top_speed_kmh, 1)} km/h`],
    [L.sprints, String(p.sprints)],
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

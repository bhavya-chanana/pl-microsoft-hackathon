"use client";

import { RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { usePlayerStats, useTeamStats } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import type { MatchMeta, TeamStats } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { formatNumber, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

const L = copy.stats.labels;

function StatRow({ label, home, away, digits = 0, unit = "" }: { label: string; home: number; away: number; digits?: number; unit?: string }) {
  const total = home + away || 1;
  const lead = home === away ? null : home > away ? "home" : "away";
  const value = (side: "home" | "away", n: number) => (
    <span className={cn("font-display text-2xl tabular-nums", lead === side ? cn("font-semibold", sideTone[side].text) : "font-medium text-muted-foreground")}>
      {formatNumber(n, digits)}
      {unit}
    </span>
  );
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-[13px] font-medium text-muted-foreground">{label}</p>
      <div className="flex items-baseline justify-between">
        {value("home", home)}
        {value("away", away)}
      </div>
      <div aria-hidden className="flex h-1.5 gap-0.5 overflow-hidden rounded-full">
        <span className="bg-home" style={{ flexGrow: home / total || 0.0001 }} />
        <span className="bg-away" style={{ flexGrow: away / total || 0.0001 }} />
      </div>
    </div>
  );
}

/** One plain sentence on who is on top, from chances created, shots and possession. */
function whoIsOnTop(meta: MatchMeta, h: TeamStats, a: TeamStats, possession: { home: number; away: number }): string {
  const checks: [string, number, number][] = [
    [copy.stats.labels.xg.toLowerCase(), h.xg, a.xg],
    [copy.stats.labels.shots.toLowerCase(), h.shots, a.shots],
    [copy.stats.labels.possession.toLowerCase(), possession.home, possession.away],
  ];
  const homeWins = checks.filter(([, x, y]) => x > y);
  const awayWins = checks.filter(([, x, y]) => y > x);
  if (homeWins.length === awayWins.length) return copy.stats.evenSummary;
  const [team, wins] = homeWins.length > awayWins.length ? [meta.home.name, homeWins] : [meta.away.name, awayWins];
  return copy.stats.onTop(team, wins.map(([name]) => name));
}

/** Whole-number possession that adds up to 100: rounded on its own, 54.5 and 45.5 would show as 55 and 46. */
function possessionSplit(home: number | null, away: number | null): { home: number; away: number } {
  if (home == null || away == null || home + away <= 0) return { home: 50, away: 50 };
  const share = Math.round((home / (home + away)) * 100);
  return { home: share, away: 100 - share };
}

/** A big-number tile: home value left, away right, the leader in its team colour. */
function Kpi({ label, home, away, digits = 0, unit = "" }: { label: string; home: number; away: number; digits?: number; unit?: string }) {
  const lead = home === away ? null : home > away ? "home" : "away";
  const num = (side: "home" | "away", n: number) => (
    <span className={cn("font-display text-[40px] leading-10 font-bold tnum", lead === side ? sideTone[side].text : "text-muted-foreground")}>
      {formatNumber(n, digits)}
      {unit && <span className="text-xl">{unit}</span>}
    </span>
  );
  return (
    <li className="flex min-w-[78%] snap-start flex-col gap-2 rounded-2xl border bg-card p-4 shadow-raised sm:min-w-0">
      <span className="text-[13px] font-medium text-muted-foreground">{label}</span>
      <span className="flex items-baseline justify-between gap-2">
        {num("home", home)}
        {num("away", away)}
      </span>
    </li>
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
  const possession = possessionSplit(h.possession_pct, a.possession_pct);
  return (
    <section aria-labelledby="team-stats" className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className={cn("font-display text-lg font-semibold", sideTone.home.text)}>{meta.home.short_name}</span>
        <h3 id="team-stats" className="text-sm font-medium">
          {copy.stats.team}
        </h3>
        <span className={cn("font-display text-lg font-semibold", sideTone.away.text)}>{meta.away.short_name}</span>
      </div>
      <p className="text-lg font-medium">{whoIsOnTop(meta, h, a, possession)}</p>
      <ul className="-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto overscroll-x-contain px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-3 sm:overflow-visible sm:px-0">
        <Kpi label={L.possession} home={possession.home} away={possession.away} unit="%" />
        <Kpi label={L.shots} home={h.shots} away={a.shots} />
        <Kpi label={L.xg} home={h.xg} away={a.xg} digits={2} />
      </ul>
      <div className="mt-2 flex flex-col gap-5">
        <StatRow label={L.possession} home={possession.home} away={possession.away} unit="%" />
        <StatRow label={L.shots} home={h.shots} away={a.shots} />
        <StatRow label={L.onTarget} home={h.shots_on_target} away={a.shots_on_target} />
        <StatRow label={L.xg} home={h.xg} away={a.xg} digits={2} />
      </div>
      <details className="group mt-1 rounded-2xl border px-4 py-1 open:pb-4">
        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between text-sm font-medium [&::-webkit-details-marker]:hidden">
          {copy.stats.moreStats}
          <span aria-hidden className="text-muted-foreground transition-transform group-open:rotate-180">⌄</span>
        </summary>
        <div className="mt-3 flex flex-col gap-5">
          <StatRow label={L.passes} home={h.passes} away={a.passes} />
          <StatRow label={L.corners} home={h.corners} away={a.corners} />
          <StatRow label={L.fouls} home={h.fouls} away={a.fouls} />
        </div>
      </details>
    </section>
  );
}

export function PlayerCard() {
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
    [L.xg, p.xg.toFixed(2)],
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

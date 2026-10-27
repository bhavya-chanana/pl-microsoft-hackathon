"use client";

import { m } from "motion/react";
import { useMemo, useState } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalystWording } from "@/features/coach/use-analyst";
import { useLive } from "@/features/live/live-store";
import { usePlayersStats, useRatings, useTeamStats } from "@/features/live/queries";
import type { MatchEvent, MatchMeta, TeamStats } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { formatNumber, sideOf } from "@/lib/match";
import { cn } from "@/lib/utils";

const L = copy.stats.labels;
export type Period = "all" | "1" | "2";

type Row = { label: string; home: number; away: number; digits?: number; unit?: string; lower?: boolean };
type Group = { title: string; rows: Row[]; bar?: { label: string; home: number; away: number }; help?: React.ReactNode };

/** Whole-number split that adds up to 100. */
function split(home: number | null | undefined, away: number | null | undefined): { home: number; away: number } | null {
  if (home == null || away == null || home + away <= 0) return null;
  const share = Math.round((home / (home + away)) * 100);
  return { home: share, away: 100 - share };
}

function fromTimeline(meta: MatchMeta, timeline: MatchEvent[], period: Period) {
  const pick = (side: "home" | "away") => {
    const shots = timeline.filter((e) => e.type === "shot" && sideOf(meta, e.team_id) === side && (period === "all" || String(e.period) === period));
    return {
      shots: shots.length,
      onTarget: shots.filter((e) => ["goal", "saved", "on_target"].includes(e.outcome)).length,
      xg: shots.reduce((sum, e) => sum + (typeof e.attrs.xg === "number" ? e.attrs.xg : 0), 0),
    };
  };
  return { home: pick("home"), away: pick("away") };
}

function cards(meta: MatchMeta, timeline: MatchEvent[], side: "home" | "away") {
  const own = timeline.filter((e) => e.type === "card" && sideOf(meta, e.team_id) === side);
  const red = (e: MatchEvent) => e.outcome.includes("red") || e.outcome === "second_yellow";
  return { yellow: own.filter((e) => !red(e)).length, red: own.filter(red).length };
}

function useGroups(meta: MatchMeta, period: Period, h: TeamStats, a: TeamStats, coach: boolean): Group[] {
  const timeline = useLive((s) => s.timeline);
  const analyst = useAnalystWording() || coach;
  const ratings = useRatings().data;
  const players = usePlayersStats((ratings ?? []).map((r) => r.player_id));
  return useMemo(() => {
    const t = fromTimeline(meta, timeline, period);
    const all = period === "all";
    const groups: Group[] = [];
    const poss = all ? split(h.possession_pct, a.possession_pct) : split(h.possession_pct_by_half?.[period], a.possession_pct_by_half?.[period]);
    const tilt = all ? split(h.field_tilt_pct, a.field_tilt_pct) : split(h.field_tilt_pct_by_half?.[period], a.field_tilt_pct_by_half?.[period]);
    groups.push({
      title: copy.stats.topStats,
      bar: poss ? { label: L.possession, ...poss } : undefined,
      rows: [
        { label: analyst ? "xG" : L.xg, home: t.home.xg, away: t.away.xg, digits: 2 },
        { label: L.shots, home: t.home.shots, away: t.away.shots },
        ...(all ? [{ label: L.corners, home: h.corners, away: a.corners }] : []),
        ...(tilt ? [{ label: copy.coachPage.territory, home: tilt.home, away: tilt.away, unit: "%" }] : []),
      ],
    });
    groups.push({ title: L.shots, rows: [{ label: L.shots, home: t.home.shots, away: t.away.shots }, { label: L.onTarget, home: t.home.onTarget, away: t.away.onTarget }] });
    if (all) {
      groups.push({
        title: L.passes,
        rows: [
          { label: L.passes, home: h.passes, away: a.passes },
          ...(h.pass_accuracy != null && a.pass_accuracy != null ? [{ label: L.passAccuracy, home: h.pass_accuracy, away: a.pass_accuracy, unit: "%" }] : []),
        ],
      });
      const sum = (side: "home" | "away", f: (s: NonNullable<ReturnType<typeof players.get>>) => number | null | undefined) =>
        (ratings ?? []).filter((r) => r.team_id === meta[side].team_id).reduce((acc, r) => {
          const p = players.get(r.player_id);
          return acc + (p ? (f(p) ?? 0) : 0);
        }, 0);
      const hasDuels = [...players.values()].some((p) => p.duels_total != null);
      if (hasDuels) groups.push({ title: copy.stats.duels, rows: [{ label: copy.stats.duelsWon, home: sum("home", (p) => p.duels_won), away: sum("away", (p) => p.duels_won) }] });
      groups.push({
        title: copy.stats.defence,
        rows: [
          { label: L.saves, home: h.saves, away: a.saves },
          ...(players.size ? [{ label: L.tackles, home: sum("home", (p) => p.tackles), away: sum("away", (p) => p.tackles) }, { label: L.interceptions, home: sum("home", (p) => p.interceptions), away: sum("away", (p) => p.interceptions) }] : []),
        ],
      });
    }
    const ch = cards(meta, timeline, "home");
    const ca = cards(meta, timeline, "away");
    groups.push({
      title: copy.stats.discipline,
      rows: [
        ...(all ? [{ label: L.fouls, home: h.fouls, away: a.fouls, lower: true }] : []),
        { label: copy.stats.yellowCards, home: ch.yellow, away: ca.yellow, lower: true },
        { label: copy.stats.redCards, home: ch.red, away: ca.red, lower: true },
      ],
    });
    return groups.map((g) => ({ ...g, rows: g.rows.filter((r) => Number.isFinite(r.home) && Number.isFinite(r.away)) })).filter((g) => g.rows.length || g.bar);
  }, [meta, timeline, period, h, a, analyst, ratings, players]);
}

/** `value | label | value`: the leading value gets a pill in its team colour; ties get none. */
function StatRow({ row }: { row: Row }) {
  const tie = row.home === row.away;
  const lead = tie ? null : (row.home > row.away) !== Boolean(row.lower) ? "home" : "away";
  const value = (side: "home" | "away", n: number) => (
    <span
      className={cn(
        "justify-self-center rounded-[17px] px-2 py-1 text-base tabular-nums",
        lead === side ? cn("font-medium", side === "home" ? "bg-home text-white" : "bg-away text-[#1d1d1d]") : "text-foreground",
      )}
    >
      {formatNumber(n, row.digits ?? 0)}
      {row.unit}
    </span>
  );
  return (
    <div className="grid min-h-14 grid-cols-[5rem_minmax(0,1fr)_5rem] items-center border-t first:border-t-0 md:min-h-12">
      {value("home", row.home)}
      <span className="px-2 text-center text-sm text-muted-foreground">{row.label}</span>
      {value("away", row.away)}
    </div>
  );
}

/** Possession is the only bar: 36px tall, two segments with a gap, the percentage inside. */
function PossessionBar({ label, home, away }: { label: string; home: number; away: number }) {
  return (
    <div className="flex flex-col gap-1.5 pb-2">
      <span className="text-center text-sm text-muted-foreground">{label}</span>
      <div className="flex h-9 gap-2" role="img" aria-label={`${label}: ${home}% to ${away}%`}>
        <m.span layout transition={{ duration: 0.5 }} className="flex items-center justify-start rounded-full bg-home px-3 text-sm font-medium text-white tabular-nums" style={{ flexGrow: home || 1, flexBasis: 0 }}>
          {home}%
        </m.span>
        <m.span layout transition={{ duration: 0.5 }} className="flex items-center justify-end rounded-full bg-away px-3 text-sm font-medium text-[#1d1d1d] tabular-nums" style={{ flexGrow: away || 1, flexBasis: 0 }}>
          {away}%
        </m.span>
      </div>
    </div>
  );
}

function PeriodFilter({ value, onChange, second }: { value: Period; onChange: (p: Period) => void; second: boolean }) {
  const opts: [Period, string][] = [["all", copy.stats.periodAll], ["1", copy.stats.period1], ["2", copy.stats.period2]];
  return (
    <div role="radiogroup" aria-label={copy.stats.period} className="flex gap-2">
      {opts.map(([p, label]) => {
        const off = p === "2" && !second;
        return (
          <button
            key={p}
            type="button"
            role="radio"
            aria-checked={value === p}
            disabled={off}
            title={off ? copy.stats.notPlayed : undefined}
            onClick={() => onChange(p)}
            className={cn("h-11 rounded-3xl px-5 text-sm font-medium transition-colors disabled:opacity-40", value === p ? "bg-foreground text-background" : "bg-raised text-muted-foreground hover:text-foreground")}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

/** The whole team comparison: filter, possession bar, then grouped rows. Rows the feed lacks are left out. */
export function TeamStatRows({ coach = false }: { coach?: boolean }) {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const half = Boolean(state && state.elapsed >= 2700 && state.elapsed <= 3600 && state.status !== "full_time");
  const [period, setPeriod] = useState<Period>(half ? "1" : "all");
  const home = useTeamStats(meta?.home.team_id ?? null).data;
  const away = useTeamStats(meta?.away.team_id ?? null).data;
  if (!meta || !home || !away) {
    return (
      <div className="flex flex-col gap-3" aria-hidden>
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }
  return <Rows meta={meta} h={home} a={away} period={period} setPeriod={setPeriod} second={(state?.period ?? 1) >= 2} coach={coach} />;
}

function Rows({ meta, h, a, period, setPeriod, second, coach }: { meta: MatchMeta; h: TeamStats; a: TeamStats; period: Period; setPeriod: (p: Period) => void; second: boolean; coach: boolean }) {
  const groups = useGroups(meta, period, h, a, coach);
  return (
    <section aria-labelledby="stat-rows" className="flex flex-col gap-4">
      <h2 id="stat-rows" className="sr-only">{copy.stats.team}</h2>
      <div className="flex items-center justify-between gap-3">
        <span className="font-display text-lg font-semibold text-home-text">{meta.home.short_name}</span>
        <PeriodFilter value={period} onChange={setPeriod} second={second} />
        <span className="font-display text-lg font-semibold text-away-text">{meta.away.short_name}</span>
      </div>
      {groups.map((g) => (
        <div key={g.title} className="flex flex-col rounded-2xl bg-card p-4">
          <h3 className="flex items-center justify-center gap-2 pb-2 text-base font-medium">
            {g.title}
            {g.title === copy.stats.topStats && <XgHelp />}
          </h3>
          {g.bar && <PossessionBar {...g.bar} />}
          {g.rows.map((r) => (
            <StatRow key={r.label} row={r} />
          ))}
        </div>
      ))}
    </section>
  );
}

function XgHelp() {
  return (
    <Popover>
      <PopoverTrigger className="inline-flex min-h-11 items-center rounded-md px-1 text-[13px] font-normal text-muted-foreground underline underline-offset-2 hover:text-foreground">
        {copy.stats.whatIsXg}
      </PopoverTrigger>
      <PopoverContent className="w-72 text-sm">{copy.stats.xgExplained}</PopoverContent>
    </Popover>
  );
}

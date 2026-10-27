"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useCoach } from "@/features/coach/coach-store";
import { RatingBadge } from "@/features/coach/rating";
import { useLive } from "@/features/live/live-store";
import { usePlayersStats, useRatings } from "@/features/live/queries";
import { PlayerSheet } from "@/features/player/player-sheet";
import type { PlayerRating, PlayerStats } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { formatNumber } from "@/lib/match";
import { cn } from "@/lib/utils";

const T = copy.playersTable;
type SortKey = "name" | "position" | "rating" | "fans" | "goals" | "shots" | "xg" | "passes" | "defence" | "distance" | "minutes" | "duels";
const NUM_COLS: { key: SortKey; label: string; full: string }[] = [
  { key: "goals", label: T.goals, full: T.goalsFull },
  { key: "shots", label: T.shots, full: T.shots },
  { key: "xg", label: T.xg, full: T.xg },
  { key: "passes", label: T.passes, full: T.passes },
  { key: "defence", label: T.defenceShort, full: T.defence },
  { key: "distance", label: T.distance, full: T.distance },
  { key: "minutes", label: "Min", full: "Minutes played" },
  { key: "duels", label: "Duels", full: "Duels won / total" },
];
export type Team = "all" | "home" | "away";
const LINES = ["GK", "DEF", "MID", "FWD"];

type Stats = ReturnType<typeof usePlayersStats>;

function num(key: SortKey, s: PlayerStats | undefined): number | null {
  if (!s) return null;
  switch (key) {
    case "goals": return s.goals;
    case "shots": return s.shots;
    case "xg": return s.xg;
    case "passes": return s.passes;
    case "defence": return s.tackles + s.interceptions;
    case "distance": return s.distance_km;
    case "minutes": return s.minutes ?? null;
    case "duels": return s.duels_won ?? null;
    default: return null;
  }
}

function compare(key: SortKey, a: PlayerRating, b: PlayerRating, stats: Stats): number {
  if (!["name", "position", "rating", "fans"].includes(key)) return (num(key, stats.get(a.player_id)) ?? -1) - (num(key, stats.get(b.player_id)) ?? -1);
  switch (key) {
    case "name":
      return a.short_name.localeCompare(b.short_name);
    case "position":
      return LINES.indexOf(a.position) - LINES.indexOf(b.position);
    case "rating":
      return a.data_rating - b.data_rating;
    case "fans":
      return (a.fan_rating ?? -1) - (b.fan_rating ?? -1);
    default:
      return 0;
  }
}

function Th({ label, full, k, sort, dir, onSort, className }: { label: string; full?: string; k: SortKey; sort: SortKey; dir: 1 | -1; onSort: (k: SortKey) => void; className?: string }) {
  const active = sort === k;
  return (
    <th scope="col" aria-sort={active ? (dir === 1 ? "ascending" : "descending") : "none"} className={cn("px-3 py-0 font-medium", className)}>
      <button type="button" aria-label={T.sortBy(full ?? label)} title={full} onClick={() => onSort(k)} className={cn("inline-flex h-11 items-center gap-1 whitespace-nowrap rounded-md hover:text-foreground", active && "text-foreground")}>
        {label}
        {active && (dir === 1 ? <ArrowUp aria-hidden className="size-3.5" /> : <ArrowDown aria-hidden className="size-3.5" />)}
      </button>
    </th>
  );
}

/** Every player who has played: sortable, 48px rows, tap a row for the player sheet. */
export function PlayersTable({ team }: { team: Team }) {
  const meta = useLive((s) => s.meta);
  const ratings = useRatings();
  const coachMode = useCoach((s) => s.coachMode);
  const [sort, setSort] = useState<SortKey>("rating");
  const [dir, setDir] = useState<1 | -1>(-1);
  const [picked, setPicked] = useState<string | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const onSort = (k: SortKey) => {
    if (k === sort) setDir((d) => (d === 1 ? -1 : 1));
    else {
      setSort(k);
      setDir(k === "name" || k === "position" ? 1 : -1);
    }
  };
  const ids = useMemo(() => (ratings.data ?? []).map((r) => r.player_id), [ratings.data]);
  const stats = usePlayersStats(ids);
  const rows = useMemo(() => {
    const all = (ratings.data ?? []).filter((r) => team === "all" || r.team_id === meta?.[team].team_id);
    return [...all].sort((a, b) => dir * compare(sort, a, b, stats) || a.short_name.localeCompare(b.short_name));
  }, [ratings.data, team, sort, dir, meta, stats]);
  const returnFocus = useCallback(() => opener.current?.focus(), []);

  if (!meta || ratings.isPending) {
    return (
      <div className="flex flex-col gap-2" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }
  if (!rows.length && team === "all") {
    return <EmptyState>{T.empty}</EmptyState>;
  }
  const hasVotes = rows.some((r) => r.fan_votes > 0);
  const showFans = !coachMode && hasVotes;
  // Min and Duels are optional backend fields: leave the columns out until some player has them.
  const cols = NUM_COLS.filter((c) => (c.key !== "minutes" && c.key !== "duels") || rows.some((r) => num(c.key, stats.get(r.player_id)) !== null));
  return (
    <div className="flex flex-col gap-3">
      <div className="min-w-0 overflow-x-auto rounded-2xl border bg-card">
        <table className="w-full min-w-[44rem] border-separate border-spacing-0 text-left text-[15px]">
          <thead className="text-[13px] text-muted-foreground">
            <tr>
              <Th label={T.player} k="name" sort={sort} dir={dir} onSort={onSort} className="sticky left-0 z-10 bg-card" />
              <Th label={T.pos} k="position" sort={sort} dir={dir} onSort={onSort} />
              <Th label={T.rating} k="rating" sort={sort} dir={dir} onSort={onSort} />
              {cols.map((c) => (
                <Th key={c.key} label={c.label} full={c.full} k={c.key} sort={sort} dir={dir} onSort={onSort} className="text-right" />
              ))}
              {showFans && <Th label={T.fans} k="fans" sort={sort} dir={dir} onSort={onSort} className="text-right" />}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const home = r.team_id === meta.home.team_id;
              return (
                <tr key={r.player_id} className="group h-12 transition-colors hover:bg-raised/60 has-[:focus-visible]:bg-raised/60">
                  <th scope="row" className="sticky left-0 z-10 border-t bg-card px-3 text-left font-medium group-hover:bg-raised">
                    <button
                      type="button"
                      aria-label={T.open(r.name)}
                      onClick={(e) => {
                        opener.current = e.currentTarget;
                        setPicked(r.player_id);
                      }}
                      className="flex h-12 w-full items-center gap-2 rounded-md text-left outline-offset-[-2px] focus-visible:outline-2 focus-visible:outline-ring"
                    >
                      <span aria-hidden className={cn("h-5 w-1 shrink-0 rounded-full", home ? "bg-home" : "bg-away")} />
                      <span className={cn("truncate", !r.on_pitch && "text-muted-foreground")}>{r.short_name}</span>
                    </button>
                  </th>
                  <td className="border-t px-3 text-muted-foreground">{r.position}</td>
                  <td className="border-t px-3">
                    <RatingBadge value={r.data_rating} size="md" />
                  </td>
                  {cols.map((c) => {
                    const v = num(c.key, stats.get(r.player_id));
                    const st = stats.get(r.player_id);
                    return (
                      <td key={c.key} className={cn("border-t px-3 text-right tabular-nums", sort === c.key ? "font-semibold" : "text-muted-foreground")}>
                        {v === null ? "–" : c.key === "xg" ? v.toFixed(2) : c.key === "distance" ? v.toFixed(1) : c.key === "duels" ? `${v}/${st?.duels_total ?? v}` : v}
                      </td>
                    );
                  })}
                  {showFans && (
                    <td className="border-t px-3 text-right tabular-nums text-muted-foreground">
                      {r.fan_rating === null ? "–" : formatNumber(r.fan_rating, 1)}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <PlayerSheet playerId={picked} onClose={() => setPicked(null)} returnFocus={returnFocus} />
    </div>
  );
}

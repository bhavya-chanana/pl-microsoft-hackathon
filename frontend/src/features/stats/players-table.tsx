"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { useCoach } from "@/features/coach/coach-store";
import { useLive } from "@/features/live/live-store";
import { useRatings } from "@/features/live/queries";
import { PlayerSheet } from "@/features/player/player-sheet";
import type { PlayerRating } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { formatNumber } from "@/lib/match";
import { cn } from "@/lib/utils";

const T = copy.playersTable;
type SortKey = "name" | "position" | "rating" | "fans";
type Team = "all" | "home" | "away";
const LINES = ["GK", "DEF", "MID", "FWD"];

function compare(key: SortKey, a: PlayerRating, b: PlayerRating): number {
  switch (key) {
    case "name":
      return a.short_name.localeCompare(b.short_name);
    case "position":
      return LINES.indexOf(a.position) - LINES.indexOf(b.position);
    case "rating":
      return a.data_rating - b.data_rating;
    case "fans":
      return (a.fan_rating ?? -1) - (b.fan_rating ?? -1);
  }
}

function Th({ label, k, sort, dir, onSort, className }: { label: string; k: SortKey; sort: SortKey; dir: 1 | -1; onSort: (k: SortKey) => void; className?: string }) {
  const active = sort === k;
  return (
    <th scope="col" aria-sort={active ? (dir === 1 ? "ascending" : "descending") : "none"} className={cn("px-3 py-0 font-medium", className)}>
      <button type="button" aria-label={T.sortBy(label)} onClick={() => onSort(k)} className={cn("inline-flex h-11 items-center gap-1 rounded-md hover:text-foreground", active && "text-foreground")}>
        {label}
        {active && (dir === 1 ? <ArrowUp aria-hidden className="size-3.5" /> : <ArrowDown aria-hidden className="size-3.5" />)}
      </button>
    </th>
  );
}

/** Every player who has played: sortable, 48px rows, tap a row for the player sheet. */
export function PlayersTable() {
  const meta = useLive((s) => s.meta);
  const ratings = useRatings();
  const coachMode = useCoach((s) => s.coachMode);
  const [team, setTeam] = useState<Team>("all");
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
  const rows = useMemo(() => {
    const all = (ratings.data ?? []).filter((r) => team === "all" || r.team_id === meta?.[team].team_id);
    return [...all].sort((a, b) => dir * compare(sort, a, b) || a.short_name.localeCompare(b.short_name));
  }, [ratings.data, team, sort, dir, meta]);
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
    return <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{T.empty}</p>;
  }
  const best = Math.max(10, ...rows.map((r) => r.data_rating));
  return (
    <div className="flex flex-col gap-3">
      <Segmented<Team>
        label={copy.statsPage.players}
        value={team}
        onChange={setTeam}
        className="max-w-sm"
        options={[
          { value: "all", label: copy.momentsPage.kinds.all },
          { value: "home", label: <><span aria-hidden className="size-2.5 rounded-full bg-home" />{meta.home.short_name}</> },
          { value: "away", label: <><span aria-hidden className="size-2.5 rounded-full bg-away" />{meta.away.short_name}</> },
        ]}
      />
      <div className="min-w-0 overflow-x-auto rounded-2xl border bg-card">
        <table className="w-full min-w-[28rem] border-separate border-spacing-0 text-left text-[15px]">
          <thead className="text-[13px] text-muted-foreground">
            <tr>
              <Th label={T.player} k="name" sort={sort} dir={dir} onSort={onSort} className="sticky left-0 z-10 bg-card" />
              <Th label={T.pos} k="position" sort={sort} dir={dir} onSort={onSort} />
              <Th label={T.rating} k="rating" sort={sort} dir={dir} onSort={onSort} />
              {!coachMode && <Th label={T.fans} k="fans" sort={sort} dir={dir} onSort={onSort} className="text-right" />}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const home = r.team_id === meta.home.team_id;
              return (
                <tr key={r.player_id} className="group h-12">
                  <th scope="row" className="sticky left-0 z-10 border-t bg-card px-3 text-left font-medium">
                    <button
                      type="button"
                      aria-label={T.open(r.name)}
                      onClick={(e) => {
                        opener.current = e.currentTarget;
                        setPicked(r.player_id);
                      }}
                      className="flex h-12 w-full items-center gap-2 text-left outline-offset-[-2px]"
                    >
                      <span aria-hidden className={cn("h-5 w-1 shrink-0 rounded-full", home ? "bg-home" : "bg-away")} />
                      <span className={cn("truncate", !r.on_pitch && "text-muted-foreground")}>{r.short_name}</span>
                    </button>
                  </th>
                  <td className="border-t px-3 text-muted-foreground">{r.position}</td>
                  <td className="border-t px-3">
                    <span className="flex items-center gap-2">
                      <span className="w-9 font-display text-lg font-semibold tabular-nums">{formatNumber(r.data_rating, 1)}</span>
                      <span aria-hidden className="h-1.5 w-16 overflow-hidden rounded-full bg-raised">
                        <span className={cn("block h-full rounded-full", home ? "bg-home" : "bg-away")} style={{ width: `${(r.data_rating / best) * 100}%` }} />
                      </span>
                    </span>
                  </td>
                  {!coachMode && (
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

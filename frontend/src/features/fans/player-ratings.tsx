"use client";

import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { useRatings } from "@/features/live/queries";
import { postJson } from "@/lib/api";
import { type MatchMeta, PlayerRating } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { formatNumber, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

import { useFans } from "./fan-store";
import { OverallBadge } from "./match-card";

const T = copy.fans;
const SCORES = Array.from({ length: 10 }, (_, i) => String(10 - i));
/** A steady order (by line, then name), so rows never move under the pointer as ratings change. */
const LINES = ["GK", "DEF", "MID", "FWD"];
const byLine = (a: PlayerRating, b: PlayerRating) =>
  LINES.indexOf(a.position) - LINES.indexOf(b.position) || a.short_name.localeCompare(b.short_name);
const SCORE_ITEMS = [{ value: null, label: copy.fans.you }, ...SCORES.map((s) => ({ value: s, label: s }))];

function why(row: PlayerRating): string {
  return row.parts
    .slice(0, 2)
    .map((p) => `${T.parts[p.key] ?? p.key} ${p.delta > 0 ? "+" : ""}${formatNumber(p.delta, 1)}`)
    .join(" · ");
}

function RatingRow({ row, runId }: { row: PlayerRating; runId: string }) {
  const mine = useFans((s) => s.scores[runId]?.[row.player_id]);
  const rate = useMutation({
    mutationFn: (score: number) =>
      postJson("ratings", { voter_id: useFans.getState().ensureVoterId(), player_id: row.player_id, score }, PlayerRating),
    onSuccess: (_, score) => useFans.getState().score(runId, row.player_id, score),
    onError: () => toast.error(T.rateFailed),
  });
  const reasons = why(row);
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_3rem_4.5rem_4.5rem] items-center gap-2 py-2">
      <div className="flex min-w-0 flex-col">
        <span className="flex min-w-0 items-center gap-1.5">
          {row.overall !== null && <OverallBadge overall={row.overall} />}
          <span className={cn("truncate text-sm font-medium", !row.on_pitch && "text-muted-foreground")}>
            {row.short_name}
            <span className="ml-1.5 text-xs font-normal text-muted-foreground">{row.position}</span>
          </span>
        </span>
        {reasons && <span className="truncate text-xs text-muted-foreground">{reasons}</span>}
      </div>
      <span className="text-right font-display text-lg font-bold tabular-nums">{formatNumber(row.data_rating, 1)}</span>
      <span className="flex flex-col items-end" title={row.gap === null ? T.noFanRating : T.gap(row.gap)}>
        <span className="font-display text-lg font-semibold tabular-nums">
          {row.fan_rating === null ? "–" : formatNumber(row.fan_rating, 1)}
        </span>
        <span className="text-[0.7rem] text-muted-foreground">{T.votes(row.fan_votes)}</span>
      </span>
      <Select
        items={SCORE_ITEMS}
        value={mine ? String(mine) : null}
        onValueChange={(value) => value && rate.mutate(Number(value))}
      >
        <SelectTrigger aria-label={T.rate(row.name)} className="h-11 w-full" disabled={rate.isPending}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {SCORES.map((s) => (
            <SelectItem key={s} value={s}>
              {s}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </li>
  );
}

function TeamRatings({ meta, side, rows, runId }: { meta: MatchMeta; side: "home" | "away"; rows: PlayerRating[]; runId: string }) {
  const team = meta[side];
  const headingId = `ratings-${team.team_id}`;
  return (
    <section aria-labelledby={headingId} className="flex flex-col">
      <div className="grid grid-cols-[minmax(0,1fr)_3rem_4.5rem_4.5rem] items-end gap-2 border-b pb-2 text-xs text-muted-foreground">
        <h3 id={headingId} className="inline-flex items-center gap-1.5 text-sm font-semibold text-foreground">
          <span aria-hidden className={cn("h-3 w-1 rounded-full", sideTone[side].bg)} />
          {team.short_name}
        </h3>
        <span className="text-right">{T.data}</span>
        <span className="text-right">{T.fansColumn}</span>
        <span className="text-right">{T.you}</span>
      </div>
      <ul className="divide-y">
        {rows.map((r) => (
          <RatingRow key={r.player_id} row={r} runId={runId} />
        ))}
      </ul>
    </section>
  );
}

/** Data rating next to the fans' average, for every player who has played. */
export function PlayerRatings() {
  const meta = useLive((s) => s.meta);
  const runId = useLive((s) => s.runId);
  const ratings = useRatings();

  if (!meta || !runId || ratings.isPending) {
    return (
      <div className="flex flex-col gap-2" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-11 w-full" />
        ))}
      </div>
    );
  }
  const rows = ratings.data ?? [];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <h2 className="font-display text-lg font-semibold">{T.ratingsHeading}</h2>
        <p className="text-xs text-muted-foreground">{T.ratingsHelp}</p>
      </div>
      {rows.length ? (
        <div className="grid gap-6 xl:grid-cols-2">
          {(["home", "away"] as const).map((side) => (
            <TeamRatings
              key={side}
              meta={meta}
              side={side}
              runId={runId}
              rows={rows.filter((r) => r.team_id === meta[side].team_id).sort(byLine)}
            />
          ))}
        </div>
      ) : (
        <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{T.noRatings}</p>
      )}
    </div>
  );
}

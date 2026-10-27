"use client";

import Link from "next/link";

import { useLive } from "@/features/live/live-store";
import { useTeamStats } from "@/features/live/queries";
import { playerName, sideOf, sideTone } from "@/lib/match";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

/** Goals and cards in match order, one quiet line under the story. Hidden until something has happened. */
export function ScorersStrip() {
  const meta = useLive((s) => s.meta);
  const timeline = useLive((s) => s.timeline);
  if (!meta) return null;
  const items = timeline.filter((e) => e.type === "goal" || (e.type === "card" && e.outcome !== "yellow"));
  const yellows = timeline.filter((e) => e.type === "card" && e.outcome === "yellow");
  if (!items.length && !yellows.length) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted-foreground">
      {items.map((e) => {
        const side = sideOf(meta, e.team_id);
        return (
          <span key={e.event_id} className="inline-flex items-center gap-1.5 whitespace-nowrap">
            <span aria-hidden className={cn("size-2 rounded-full", side ? sideTone[side].bg : "bg-muted-foreground")} />
            <span className="sr-only">{e.type === "goal" ? "Goal" : "Red card"}</span>
            <span className={cn("font-medium text-foreground", e.type === "card" && "text-blocked")}>{playerName(meta, e.player_id, true)}</span>
            <span className="tabular-nums">{e.minute_label}</span>
          </span>
        );
      })}
      {yellows.length > 0 && (
        <span className="tabular-nums">
          {yellows.length} yellow {yellows.length === 1 ? "card" : "cards"}
        </span>
      )}
    </p>
  );
}

function Split({ label, home, away }: { label: string; home: number; away: number }) {
  const total = home + away || 1;
  return (
    <Link href={routes.stats} className="flex min-h-11 flex-col gap-2 rounded-2xl border bg-card p-3 hover:bg-raised">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="flex justify-between font-display text-2xl font-bold tabular-nums">
        <span className={sideTone.home.text}>{Number.isInteger(home) ? home : home.toFixed(2)}</span>
        <span className={sideTone.away.text}>{Number.isInteger(away) ? away : away.toFixed(2)}</span>
      </span>
      <span aria-hidden className="flex h-1.5 gap-0.5 overflow-hidden rounded-full">
        <span className="bg-home" style={{ flexGrow: home / total || 0.001 }} />
        <span className="bg-away" style={{ flexGrow: away / total || 0.001 }} />
      </span>
    </Link>
  );
}

/** Possession, shots and xG as three split tiles that open Stats. */
export function TopStats() {
  const meta = useLive((s) => s.meta);
  const h = useTeamStats(meta?.home.team_id ?? null).data;
  const a = useTeamStats(meta?.away.team_id ?? null).data;
  if (!meta || !h || !a) return null;
  const pt = (h.possession_pct ?? 0) + (a.possession_pct ?? 0) || 1;
  const hp = Math.round(((h.possession_pct ?? 0) / pt) * 100);
  return (
    <div className="grid grid-cols-3 gap-3">
      <Split label="Possession %" home={hp} away={100 - hp} />
      <Split label="Shots" home={h.shots} away={a.shots} />
      <Split label="xG" home={h.xg} away={a.xg} />
    </div>
  );
}

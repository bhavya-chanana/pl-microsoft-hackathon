"use client";

import { ArrowLeft, Star } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";
import { routes } from "@/lib/routes";

import { useMatchDetail, usePlayerDetail } from "./api";
import { formatClock } from "./time";
import { PERSONAS, STAT_LABELS } from "./personas";
import { PlayerFace } from "./player-face";
import { Freshness, SourceTag } from "./stats";
import { usePrefs } from "./stores";
import { useMatchParams } from "./use-match-params";

const SECTIONS: Array<{ title: string; keys: string[] }> = [
  { title: "Physical · from player vest", keys: ["distance", "topSpeed", "sprints", "highSpeedRun", "avgHeartRate"] },
  { title: "On the ball · vest + ball sensor", keys: ["touches", "passes", "passAccuracy", "progressivePasses", "shots", "goals", "assists", "xg"] },
  { title: "Defending", keys: ["duelsWon", "recoveries", "saves"] },
  { title: "Discipline · video AI", keys: ["yellow", "red"] },
];

export function PlayerPage() {
  const { matchId, playerId = "" } = useMatchParams();
  const match = useMatchDetail(matchId).data!;
  const detail = usePlayerDetail(playerId);
  const persona = usePrefs((s) => s.persona);
  const followed = usePrefs((s) => s.followedPlayers.includes(playerId));
  const toggleFollow = usePrefs((s) => s.toggleFollow);
  if (detail.isError) return <p role="alert" className="mt-6">Player not found.</p>;
  if (!detail.data) return <div className="min-h-[60dvh]" aria-busy="true" />;
  const { player, stats, heatmap: grid, moments, fantasy } = detail.data;
  const fmt = (k: string) => {
    const s = stats[k];
    if (!s || s.value === null) return "—";
    return `${s.value}${s.unit === "%" ? "%" : s.unit ? ` ${s.unit}` : ""}`;
  };
  const max = Math.max(0.0001, ...grid.flat());

  return (
    <main className="mt-4 flex flex-col gap-4">
      <Link href={routes.stats(matchId)} className="flex min-h-11 w-fit items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft aria-hidden className="size-4" />Back to stats
      </Link>
      <header className="flex flex-wrap items-center gap-4 rounded-xl border bg-card p-4">
        <PlayerFace id={player.id} shirt={player.shirt} kit={player.team} className="size-24" label={`${player.name} portrait`} />
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-display font-semibold">{player.name}</h1>
          <p className="text-sm text-muted-foreground">{match[player.team].name} · {player.position} · {stats.minutes.value}′ played</p>
          <Freshness throughMs={detail.data.throughMs} refreshedAt={detail.data.refreshedAt} />
        </div>
        {persona === "fantasy" && (
          <p className="font-display text-4xl font-semibold tabular-nums">{fantasy}<span className="ml-1 text-sm text-muted-foreground">pts</span></p>
        )}
        <button type="button" onClick={() => toggleFollow(playerId)} aria-pressed={followed}
          className="flex min-h-11 items-center gap-2 rounded-md border px-3 text-sm hover:bg-muted">
          <Star aria-hidden className={cn("size-4", followed && "fill-current text-graphic-warn")} />{followed ? "Following" : "Follow"}
        </button>
      </header>

      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {PERSONAS[persona].playerHeadline.map((k) => (
          <li key={k} className="rounded-xl border bg-card p-4">
            <p className="text-xs text-muted-foreground">{STAT_LABELS[k]}</p>
            <p className="mt-1 font-display text-3xl font-semibold tabular-nums">{fmt(k)}</p>
          </li>
        ))}
      </ul>

      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="grid gap-4 sm:grid-cols-2">
          {SECTIONS.map((sec) => (
            <section key={sec.title} className="rounded-xl border bg-card p-4">
              <h2 className="font-display text-lg font-semibold">{sec.title}</h2>
              <dl className="mt-2 divide-y text-sm">
                {sec.keys.filter((k) => stats[k] && !(k === "saves" && player.position !== "GK")).map((k) => (
                  <div key={k} className="flex items-center justify-between gap-2 py-2">
                    <dt className="flex items-center gap-2 text-muted-foreground">{STAT_LABELS[k]}<SourceTag source={stats[k].source} /></dt>
                    <dd className="font-semibold tabular-nums">{fmt(k)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <div className="flex flex-col gap-4">
          <section className="rounded-xl border bg-card p-4">
            <h2 className="font-display text-lg font-semibold">Heatmap</h2>
            <p className="text-xs text-muted-foreground">Time spent per zone · player vest · attacking →</p>
            {grid.length === 0 ? <p className="mt-3 text-sm text-muted-foreground">Not on the pitch yet.</p> : (
              <div className="mt-3 grid aspect-[3/2] grid-cols-6 gap-0.5 overflow-hidden rounded-md bg-turf p-1" role="img" aria-label={`Heatmap for ${player.name}`}>
                {grid.flatMap((row, r) => row.map((v, c) => (
                  <div key={`${r}-${c}`} className="rounded-sm bg-tally" style={{ opacity: 0.08 + (v / max) * 0.8 }} />
                )))}
              </div>
            )}
          </section>
          <section className="rounded-xl border bg-card p-4">
            <h2 className="font-display text-lg font-semibold">Key moments</h2>
            {moments.length === 0 ? <p className="mt-2 text-sm text-muted-foreground">No key moments yet.</p> : (
              <ol className="mt-2 flex flex-col gap-2 text-sm">
                {moments.map((e) => (
                  <li key={e.id} className={cn(e.status === "overturned" && "line-through opacity-70")}>
                    <span className="tabular-nums text-muted-foreground">{formatClock(e.atMs)}</span> {e.headline}
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}

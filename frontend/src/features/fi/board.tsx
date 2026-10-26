"use client";

import { Eye, EyeOff, Play, Radio } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { cn } from "@/lib/utils";
import { routes } from "@/lib/routes";

import { useMatches } from "./api";
import { usePrefs } from "./stores";
import type { MatchListItem } from "./types";

export function MatchBoard() {
  const tab = useSearchParams().get("tab") === "previous" ? "previous" : "live";
  const spoilers = usePrefs((s) => s.spoilersVisible);
  const setSpoilers = usePrefs((s) => s.setSpoilers);
  const matches = useMatches();
  const list = (matches.data ?? []).filter((m) => (tab === "live" ? m.status !== "completed" : m.status === "completed"));

  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <nav aria-label="Match lists" className="flex gap-1 rounded-lg border bg-card p-1">
          {(["live", "previous"] as const).map((t) => (
            <Link key={t} href={t === "live" ? routes.matches : routes.previous} aria-current={tab === t ? "page" : undefined}
              className={cn("flex min-h-10 items-center rounded-md px-4 text-sm font-medium text-muted-foreground",
                tab === t && "bg-muted text-foreground")}>
              {t === "live" ? "Live & today" : "Previous"}
            </Link>
          ))}
        </nav>
        {tab === "previous" && (
          <button type="button" onClick={() => setSpoilers(!spoilers)} aria-pressed={spoilers}
            className="flex min-h-11 items-center gap-2 rounded-md px-3 text-sm text-muted-foreground hover:bg-muted">
            {spoilers ? <Eye aria-hidden className="size-4" /> : <EyeOff aria-hidden className="size-4" />}
            {spoilers ? "Results shown" : "Results hidden"}
          </button>
        )}
      </div>
      {matches.isPending && <p className="mt-6 text-sm text-muted-foreground" aria-busy="true">Loading matches…</p>}
      {matches.isError && <p role="alert" className="mt-6 text-sm text-destructive">Matches could not be loaded. {matches.error.message}</p>}
      <ul className="mt-6 grid gap-3">
        {list.map((m) => <li key={m.id}><MatchCard match={m} showScore={tab === "live" || spoilers} /></li>)}
      </ul>
    </main>
  );
}

function MatchCard({ match, showScore }: { match: MatchListItem; showScore: boolean }) {
  const live = match.status === "live";
  const score = match.score;
  const scoreText = match.status === "scheduled" || !showScore || !score ? "v" : `${score.home} – ${score.away}`;
  return (
    <article className="flex flex-col gap-4 rounded-xl border bg-card p-4 shadow-raised sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          {live && <span className="flex items-center gap-1 font-semibold text-tally"><Radio aria-hidden className="size-3" />LIVE</span>}
          <span>{match.competition}</span><span aria-hidden>·</span><span>{match.kickoffLabel}</span>
        </p>
        <h2 className="mt-1 flex items-center gap-3 font-display text-2xl font-semibold">
          <span className="truncate">{match.home.name}</span>
          <span className="shrink-0 tabular-nums text-muted-foreground">{scoreText}</span>
          <span className="truncate">{match.away.name}</span>
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">{match.venue}{live && " · watching 90 s behind the stadium"}</p>
      </div>
      {match.status === "scheduled" ? (
        <span className="text-sm text-muted-foreground">Starts later</span>
      ) : (
        <div className="flex gap-2">
          <Link href={routes.watch(match.id)} className="flex min-h-11 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/85">
            <Play aria-hidden className="size-4" />{live ? "Watch live" : "Watch as live"}
          </Link>
          {!live && (
            <Link href={`${routes.stats(match.id)}?mode=full_review`} className="flex min-h-11 items-center rounded-md border px-4 text-sm font-medium hover:bg-muted">
              Full review
            </Link>
          )}
        </div>
      )}
    </article>
  );
}

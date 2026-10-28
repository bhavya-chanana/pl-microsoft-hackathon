"use client";

import { ArrowUpRight, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/ui/empty-state";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { useMatches } from "./api";
import { type MatchPhase, type Next10Match, phaseOf } from "./contract";
import { n10 } from "./copy";
import { shortTime } from "./lib";
import { CardsSkeleton, ErrorState, PageFrame } from "./ui";

const T = n10.matches;

function Score({ match, phase }: { match: Next10Match; phase: MatchPhase }) {
  if (phase === "upcoming" || match.score?.home == null || match.score?.away == null) {
    return <span className="font-display text-xl font-semibold text-subtle">{T.vs}</span>;
  }
  return (
    <span className="font-display text-score font-bold tnum" aria-label={`${match.score.home} ${match.score.away}`}>
      {match.score.home}-{match.score.away}
    </span>
  );
}

function MatchCard({ match, phase }: { match: Next10Match; phase: MatchPhase }) {
  return (
    <li>
      <Link
        href={routes.next10Match(match.match_id)}
        className="group flex h-full min-h-28 flex-col justify-between gap-3 rounded-2xl border bg-card p-4 shadow-raised transition duration-200 hover:-translate-y-0.5 hover:border-foreground/20 hover:shadow-floating focus-visible:-translate-y-0.5"
      >
        <div className="flex items-center justify-between gap-2 text-[13px] text-subtle">
          <span className="truncate">{match.competition ?? ""}</span>
          <span className={cn("inline-flex shrink-0 items-center gap-1.5 font-medium", phase === "live" && "text-live")}>
            {phase === "live" && <span aria-hidden className="size-2 rounded-full bg-live motion-safe:animate-pulse" />}
            {phase === "live" ? n10.match.liveLabel : phase === "finished" ? n10.match.ftLabel : shortTime(match.kickoff_utc)}
          </span>
        </div>
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
          <span className="min-w-0 text-right text-[17px] leading-tight font-semibold text-balance">
            <span aria-hidden className="mr-2 inline-block h-3 w-1 rounded-full bg-home align-middle" />
            {match.home.name}
          </span>
          <Score match={match} phase={phase} />
          <span className="min-w-0 text-[17px] leading-tight font-semibold text-balance">
            {match.away.name}
            <span aria-hidden className="ml-2 inline-block h-3 w-1 rounded-full bg-away align-middle" />
          </span>
        </div>
        <span className="flex items-center gap-1 text-[13px] text-muted-foreground">
          {T.open}
          <ArrowUpRight aria-hidden className="size-3.5 -translate-x-0.5 opacity-0 transition duration-200 group-hover:translate-x-0 group-hover:opacity-100 group-focus-visible:translate-x-0 group-focus-visible:opacity-100" />
        </span>
      </Link>
    </li>
  );
}

function Group({ id, title, matches, phase, none }: { id: string; title: string; matches: Next10Match[]; phase: MatchPhase; none: string }) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="flex items-baseline gap-2 text-lg font-semibold">
        {title}
        <span className="text-sm font-normal text-subtle tnum">{matches.length}</span>
      </h2>
      {matches.length ? (
        <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {matches.map((m) => (
            <MatchCard key={m.match_id} match={m} phase={phase} />
          ))}
        </ul>
      ) : (
        <p className="rounded-2xl border border-dashed px-4 py-3 text-[15px] text-muted-foreground">{none}</p>
      )}
    </section>
  );
}

/** Live, upcoming and finished matches of the twelve clubs. */
export function MatchesList() {
  const { data, isPending, isError, refetch } = useMatches();
  const grouped: Record<MatchPhase, Next10Match[]> = { live: [], upcoming: [], finished: [] };
  for (const m of data ?? []) grouped[phaseOf(m)].push(m);
  const time = (m: Next10Match) => Date.parse(m.kickoff_utc ?? "") || 0;
  grouped.upcoming.sort((a, b) => time(a) - time(b));
  grouped.finished.sort((a, b) => time(b) - time(a));
  return (
    <PageFrame
      title={n10.product}
      intro={n10.tagline}
      actions={
        <Link href={routes.next10Verify} className="inline-flex h-11 items-center gap-2 rounded-full border bg-card px-4 text-sm font-medium transition-colors hover:bg-raised">
          <ShieldCheck aria-hidden className="size-4" />
          {T.verifyLink}
        </Link>
      }
    >
      {isPending ? (
        <CardsSkeleton />
      ) : isError ? (
        <ErrorState title={T.offlineTitle} onRetry={() => void refetch()}>
          {T.offlineBody}
        </ErrorState>
      ) : data.length === 0 ? (
        <EmptyState title={T.emptyTitle}>{T.emptyBody}</EmptyState>
      ) : (
        <div className="flex flex-col gap-8">
          <Group id="g-live" title={T.live} matches={grouped.live} phase="live" none={T.none.live} />
          <Group id="g-up" title={T.upcoming} matches={grouped.upcoming} phase="upcoming" none={T.none.upcoming} />
          <Group id="g-ft" title={T.finished} matches={grouped.finished} phase="finished" none={T.none.finished} />
        </div>
      )}
    </PageFrame>
  );
}

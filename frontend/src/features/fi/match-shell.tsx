"use client";

import { FastForward, Pause, Play, Radio } from "lucide-react";
import Link from "next/link";
import { notFound, usePathname, useSearchParams } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { cn } from "@/lib/utils";
import { routes } from "@/lib/routes";

import { CLUB_COLORS } from "./clubs";
import { useT } from "./i18n";
import { ApiError, useMatchDetail, useMatchView } from "./api";
import { formatClock } from "./time";
import { usePlayback, type ViewMode } from "./stores";
import type { MatchSummary } from "./types";
import { useMatchParams } from "./use-match-params";

export function MatchShell({ children }: { children: ReactNode }) {
  const { matchId } = useMatchParams();
  const detail = useMatchDetail(matchId);
  const match = detail.data;
  const params = useSearchParams();
  const open = usePlayback((s) => s.open);
  const tick = usePlayback((s) => s.tick);
  const ready = usePlayback((s) => s.matchId === matchId);

  useEffect(() => {
    if (!match) return;
    const requested = params.get("mode");
    open(match, requested === "full_review" && match.status === "completed" ? "full_review" : undefined);
  }, [match, open, params]);

  useEffect(() => {
    const id = window.setInterval(() => tick(250), 250);
    return () => window.clearInterval(id);
  }, [tick]);

  // Follow the server's live edge (and restarts) for real-time clips.
  const syncLive = usePlayback((s) => s.syncLive);
  useEffect(() => {
    if (match?.serverClock && match.liveEdgeMs !== undefined) syncLive(match.liveEdgeMs, match.epoch ?? 0);
  }, [match?.serverClock, match?.liveEdgeMs, match?.epoch, syncLive]);

  if (detail.isError) {
    if (detail.error instanceof ApiError && detail.error.status === 404) notFound();
    return <p role="alert" className="mx-auto max-w-md px-4 py-16 text-center text-sm text-muted-foreground">This match could not be loaded. {detail.error.message}</p>;
  }
  if (!match || !ready) return <div className="min-h-[60dvh]" aria-busy="true" />;
  return (
    <div className="mx-auto max-w-screen-2xl px-4 pb-16 sm:px-6" style={{
      ["--team-home" as string]: CLUB_COLORS[match.home.short]?.deep, ["--team-away" as string]: CLUB_COLORS[match.away.short]?.bright }}>
      <MatchBar matchId={matchId} match={match} />
      {children}
    </div>
  );
}

function MatchBar({ matchId, match }: { matchId: string; match: MatchSummary }) {
  const pathname = usePathname();
  const { playheadMs, mode, playing, speed, liveEdgeMs, durationMs, delayMs } = usePlayback();
  const { setPlaying, setSpeed, goLive, seek, setMode } = usePlayback.getState();
  const score = useMatchView().data?.score ?? { home: 0, away: 0, provisional: false };
  const behind = mode === "live" ? Math.round((liveEdgeMs - playheadMs) / 1000) : 0;
  const atLive = mode === "live" && behind <= delayMs / 1000 + 1;
  const onWatch = pathname.endsWith("/watch");
  const max = mode === "live" ? Math.max(1, liveEdgeMs - delayMs) : durationMs;
  const fullClock = formatClock(playheadMs);
  const tr = useT();

  function changeMode(next: ViewMode) {
    if (next === "full_review" && !window.confirm("Full review shows the final score and every stat. Continue?")) return;
    setMode(next);
  }

  return (
    <section aria-label="Match" className="sticky top-14 z-30 -mx-4 border-b bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex min-w-0 items-center gap-3 font-display text-xl font-semibold sm:text-2xl">
          <span className="truncate">{match.home.short}</span>
          <span className="rounded bg-graphic px-2 tabular-nums text-graphic-foreground" aria-live="polite">
            {score.home}–{score.away}{score.provisional && <span className="ml-1 text-xs text-graphic-warn" title="Goal awaiting video confirmation">?</span>}
          </span>
          <span className="truncate">{match.away.short}</span>
          <span className="ml-1 font-sans text-sm font-medium tabular-nums text-muted-foreground">{fullClock}</span>
        </div>

        <nav aria-label="Match views" className="flex gap-1 rounded-lg border bg-card p-1">
          {[{ href: routes.watch(matchId), label: tr.watch, on: onWatch },
            { href: routes.stats(matchId), label: tr.stats, on: !onWatch }].map((t) => (
            <Link key={t.label} href={t.href} aria-current={t.on ? "page" : undefined}
              className={cn("flex min-h-9 items-center rounded-md px-4 text-sm font-medium text-muted-foreground", t.on && "bg-muted text-foreground")}>
              {t.label}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex flex-wrap items-center gap-2 text-sm">
          {mode === "live" ? (
            <button type="button" onClick={goLive} className={cn("flex min-h-9 items-center gap-1.5 rounded-md px-2.5 font-medium",
              atLive ? "text-tally" : "bg-tally text-tally-foreground")}>
              <Radio aria-hidden className="size-4" />
              {atLive ? `Live · ${delayMs / 1000}s delay` : `${behind - delayMs / 1000}s behind — go live`}
            </button>
          ) : (
            <select aria-label="Review mode" value={mode} onChange={(e) => changeMode(e.target.value as ViewMode)}
              className="h-9 rounded-md border bg-card px-2">
              <option value="as_live">As live (no spoilers)</option>
              <option value="full_review">Full review</option>
            </select>
          )}
          <button type="button" aria-label={playing ? "Pause" : "Play"} onClick={() => setPlaying(!playing)}
            className="grid size-9 place-items-center rounded-md border hover:bg-muted">
            {playing ? <Pause aria-hidden className="size-4" /> : <Play aria-hidden className="size-4" />}
          </button>
          <label className="flex items-center gap-1 text-muted-foreground">
            <FastForward aria-hidden className="size-4" />
            <select aria-label="Demo speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="h-9 rounded-md border bg-card px-1 text-foreground">
              {[1, 10, 30, 60].map((s) => <option key={s} value={s}>{s}×</option>)}
            </select>
          </label>
        </div>
      </div>
      <input type="range" aria-label="Seek" min={0} max={max} step={1000} value={Math.min(playheadMs, max)}
        onChange={(e) => seek(Number(e.target.value))} className="mt-3 w-full accent-[var(--tally)]" />
    </section>
  );
}

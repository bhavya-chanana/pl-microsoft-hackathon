"use client";

import { Maximize2, Minimize2, Rewind } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, XAxis, YAxis } from "recharts";

import { cn } from "@/lib/utils";
import { routes } from "@/lib/routes";

import { useT } from "./i18n";
import { useMatchDetail, useMatchView, usePlayersById } from "./api";
import { formatClock } from "./time";
import type { Persona, VisibleEvent } from "./types";
import { PERSONAS, SOURCE_LABELS, STAT_LABELS } from "./personas";
import { cadence } from "./polling";
import { usePlayback, usePrefs } from "./stores";
import { useMatchParams } from "./use-match-params";

export function WatchView() {
  const { matchId } = useMatchParams();
  const layout = usePlayback((s) => s.layout);
  const setLayout = usePlayback((s) => s.setLayout);

  useEffect(() => {
    if (layout !== "immersive") return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setLayout("scroll"); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [layout, setLayout]);

  if (layout === "immersive") {
    return (
      <div className="fixed inset-0 z-50 bg-black">
        <VideoStage matchId={matchId} immersive />
      </div>
    );
  }
  return (
    <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div className="flex min-w-0 flex-col gap-6">
        <VideoStage matchId={matchId} />
        <StatChips />
        <MatchPulse />
        <CatchUp />
      </div>
      <InsightFeed matchId={matchId} />
    </div>
  );
}

/** Events from the backend's as-of view; the server already excluded anything after its playhead. */
function useEvents(): VisibleEvent[] {
  return useMatchView().data?.events ?? [];
}

/** Persona copy, or an honest placeholder while the AI analysis is still being written. */
export const copyFor = (e: VisibleEvent, persona: Persona) => e.copy?.[persona] ?? "Analysis pending — the facts above are confirmed.";

function usePopup(): VisibleEvent | null {
  const t = usePlayback((s) => s.playheadMs);
  const persona = usePrefs((s) => s.persona);
  const events = useEvents();
  return useMemo(() => {
    const cfg = PERSONAS[persona];
    const recent = events.filter((e) => {
      // Re-show on each revision (provisional → confirmed / overturned).
      const latest = e.revisions.filter((r) => r.availableMs <= t).at(-1)?.availableMs ?? e.atMs;
      return t - Math.max(e.atMs, latest) < cadence.overlayDwellMs && (e.critical || cfg.popupTypes.includes(e.type));
    });
    return recent.at(-1) ?? null;
  }, [t, persona, events]);
}

/**
 * SoccerNet footage, when scripts/fetch_soccernet_video.py has placed it in public/video. The video follows
 * the shared playhead (seek on drift, rate = demo speed) so popups and stats stay in step with the picture.
 */
function MatchVideo({ matchId }: { matchId: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing">("loading");
  const t = usePlayback((s) => s.playheadMs);
  const playing = usePlayback((s) => s.playing);
  const speed = usePlayback((s) => s.speed);
  const tr = useT();

  useEffect(() => {
    const v = ref.current;
    if (!v || state !== "ready") return;
    if (Math.abs(v.currentTime - t / 1000) > 2) v.currentTime = t / 1000;
    v.playbackRate = Math.min(16, speed);
    if (playing && v.paused) void v.play().catch(() => {});
    if (!playing && !v.paused) v.pause();
  }, [t, playing, speed, state]);

  return (
    <>
      <div aria-hidden className="absolute inset-0 bg-[repeating-linear-gradient(90deg,var(--turf)_0_8%,var(--turf-stripe)_8%_16%)]" />
      <video ref={ref} src={`/video/${matchId}.mp4`} muted playsInline preload="auto"
        onLoadedMetadata={() => setState("ready")} onError={() => setState("missing")}
        className={cn("absolute inset-0 size-full bg-black object-contain", state !== "ready" && "hidden")} />
      {state !== "ready" && (
        <>
          <div aria-hidden className="absolute inset-[6%] rounded-sm border-2 border-chalk/60">
            <div className="absolute inset-y-0 left-1/2 border-l-2 border-chalk/60" />
            <div className="absolute top-1/2 left-1/2 size-[18%] -translate-1/2 rounded-full border-2 border-chalk/60" />
          </div>
          <div className="absolute inset-x-6 top-1/2 -translate-y-1/2 text-center text-sm text-chalk">
            <p className="font-semibold">{state === "loading" ? "…" : tr.videoMissing}</p>
            {state === "missing" && <p className="mt-1 text-xs opacity-80">{tr.videoHelp}</p>}
          </div>
        </>
      )}
      {state === "ready" && (
        <span className="absolute right-3 bottom-3 rounded bg-graphic/80 px-2 py-0.5 text-[11px] text-graphic-foreground">Footage: SoccerNet (research use)</span>
      )}
    </>
  );
}

function VideoStage({ matchId, immersive = false }: { matchId: string; immersive?: boolean }) {
  const match = useMatchDetail(matchId).data!;
  const t = usePlayback((s) => s.playheadMs);
  const setLayout = usePlayback((s) => s.setLayout);
  const persona = usePrefs((s) => s.persona);
  const score = useMatchView().data?.score ?? { home: 0, away: 0 };
  const popup = usePopup();

  return (
    <section aria-label="Match video" className={cn("relative overflow-hidden bg-turf",
      immersive ? "h-full w-full" : "aspect-video rounded-xl")}>
      <MatchVideo matchId={matchId} />

      <div className="absolute top-3 left-3 flex items-stretch font-display text-lg font-semibold sm:top-5 sm:left-5 sm:text-xl">
        <span className="graphic-cut-left bg-graphic py-1 pr-2 pl-4 text-graphic-foreground">{match.home.short}</span>
        <span className="bg-tally px-2.5 py-1 tabular-nums text-tally-foreground">{score.home}–{score.away}</span>
        <span className="bg-graphic py-1 pr-4 pl-2 text-graphic-foreground graphic-cut">{match.away.short}</span>
        <span className="ml-2 self-center rounded bg-graphic/80 px-2 py-0.5 font-sans text-sm tabular-nums text-graphic-foreground">{formatClock(t)}</span>
      </div>

      <button type="button" onClick={() => setLayout(immersive ? "scroll" : "immersive")}
        aria-label={immersive ? "Exit full screen (Esc)" : "Full screen"}
        className="absolute top-3 right-3 grid size-11 place-items-center rounded-md bg-graphic/80 text-graphic-foreground hover:bg-graphic sm:top-5 sm:right-5">
        {immersive ? <Minimize2 aria-hidden className="size-5" /> : <Maximize2 aria-hidden className="size-5" />}
      </button>
      {immersive && (
        <Link href={routes.stats(matchId)} onClick={() => setLayout("scroll")}
          className="absolute top-3 right-16 flex min-h-11 items-center rounded-md bg-graphic/80 px-4 text-sm font-medium text-graphic-foreground hover:bg-graphic sm:top-5 sm:right-18">
          Stats
        </Link>
      )}

      <AnimatePresence>
        {popup && (
          <motion.div key={`${popup.id}-${popup.status}`} role="status"
            initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} transition={{ duration: 0.25 }}
            className="absolute bottom-[8%] left-3 max-w-[min(560px,calc(100%-24px))] sm:left-5">
            <div className={cn("graphic-cut px-4 py-1 font-display text-lg font-semibold uppercase",
              popup.status === "overturned" ? "bg-graphic-warn text-graphic" : popup.critical ? "bg-tally text-tally-foreground" : "bg-graphic text-graphic-foreground")}>
              {popup.status === "overturned" ? `Overturned · ${popup.headline}` : popup.headline}
              <span className="ml-3 font-sans text-sm font-medium normal-case opacity-80">{formatClock(popup.atMs)}</span>
            </div>
            <p className="bg-graphic px-4 py-2 text-sm text-graphic-foreground">{copyFor(popup, persona)}</p>
            <p className="bg-graphic/85 px-4 pb-2 text-xs text-graphic-foreground/70">
              {popup.status === "provisional" ? "Provisional · " : ""}{popup.note ?? `Source: ${SOURCE_LABELS[popup.source]}`}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

function StatChips() {
  const persona = usePrefs((s) => s.persona);
  const stats = useMatchView().data?.chips;
  if (!stats) return null;
  return (
    <ul aria-label="Quick stats" className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {PERSONAS[persona].chips.map((k) => {
        const s = stats[k];
        const fmt = (v: number | null) => (v === null ? "—" : `${v}${s.unit === "%" ? "%" : ""}`);
        return (
          <li key={k} className="rounded-lg border bg-card px-3 py-2">
            <p className="text-xs text-muted-foreground">{STAT_LABELS[k]}</p>
            <p className="mt-0.5 font-display text-xl font-semibold tabular-nums">
              <span className="text-home">{fmt(s.home)}</span><span className="mx-1.5 text-muted-foreground">·</span><span className="text-away">{fmt(s.away)}</span>
            </p>
          </li>
        );
      })}
    </ul>
  );
}

function MatchPulse() {
  const t = usePlayback((s) => s.playheadMs);
  const seek = usePlayback((s) => s.seek);
  const view = useMatchView().data;
  const data = view?.pulse ?? [];
  const goals = (view?.events ?? []).filter((e) => e.type === "goal" && e.status !== "overturned" && e.atMs <= t);
  return (
    <section aria-labelledby="pulse-h" className="rounded-xl border bg-card p-4">
      <div className="flex items-baseline justify-between">
        <h2 id="pulse-h" className="font-display text-lg font-semibold">Match pulse</h2>
        <p className="text-xs text-muted-foreground">Field tilt + chances, per minute · vest + ball</p>
      </div>
      <div className="mt-2 h-28">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}
            onClick={(s) => { const m = Number(s?.activeLabel); if (!Number.isNaN(m)) seek(m * 60_000); }}>
            <XAxis dataKey="minute" type="number" domain={[0, 93]} ticks={[0, 15, 30, 45, 60, 75, 90]} tickLine={false} axisLine={false} fontSize={11} stroke="var(--muted-foreground)" />
            <YAxis domain={[-1, 1]} hide />
            <ReferenceLine y={0} stroke="var(--border)" />
            {goals.map((g) => <ReferenceLine key={g.id} x={Math.floor(g.atMs / 60_000)} stroke={g.team === "home" ? "var(--team-home)" : "var(--team-away)"} strokeDasharray="3 3" />)}
            <Area type="monotone" dataKey="value" stroke="var(--team-home)" fill="var(--team-home)" fillOpacity={0.25} isAnimationActive={false} baseValue={0} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 flex justify-between text-xs text-muted-foreground"><span className="text-home">▲ Home pressure</span><span className="text-away">▼ Away pressure</span></p>
    </section>
  );
}

function CatchUp() {
  const t = usePlayback((s) => s.playheadMs);
  const persona = usePrefs((s) => s.persona);
  const [win, setWin] = useState<5 | 15 | 30 | null>(null);
  const events = useEvents();
  const items = win === null ? [] : events.filter((e) => e.atMs >= t - win * 60_000 && (e.critical || PERSONAS[persona].weights[e.type]));
  return (
    <section aria-labelledby="catch-h" className="rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="catch-h" className="flex items-center gap-2 font-display text-lg font-semibold"><Rewind aria-hidden className="size-4" />What did I miss?</h2>
        <div className="ml-auto flex gap-1">
          {([5, 15, 30] as const).map((w) => (
            <button key={w} type="button" onClick={() => setWin(win === w ? null : w)} aria-pressed={win === w}
              className={cn("min-h-9 rounded-md border px-3 text-sm", win === w && "bg-muted font-medium")}>{w} min</button>
          ))}
        </div>
      </div>
      {win !== null && (
        items.length === 0 ? <p className="mt-3 text-sm text-muted-foreground">Nothing major in the last {win} minutes.</p> : (
          <ol className="mt-3 flex flex-col gap-2 text-sm">
            {items.map((e) => <li key={e.id}><span className="tabular-nums text-muted-foreground">{formatClock(e.atMs)}</span> — {copyFor(e, persona)}</li>)}
          </ol>
        )
      )}
    </section>
  );
}

function InsightFeed({ matchId }: { matchId: string }) {
  const persona = usePrefs((s) => s.persona);
  const followed = usePrefs((s) => s.followedPlayers);
  const [sort, setSort] = useState<"latest" | "for_you">("latest");
  const events = useEvents();
  const cfg = PERSONAS[persona];
  const ranked = sort === "latest" ? [...events].reverse() : [...events].sort((a, b) => score(b) - score(a));
  function score(e: VisibleEvent) {
    const fan = persona === "fantasy" && e.playerIds.some((p) => followed.includes(p)) ? 6 : 0;
    return (e.critical ? 10 : 0) + (cfg.weights[e.type] ?? 0) + fan + e.atMs / 1e7;
  }
  return (
    <aside aria-labelledby="feed-h" className="flex min-h-0 flex-col rounded-xl border bg-card lg:sticky lg:top-44 lg:max-h-[calc(100dvh-12rem)]">
      <div className="flex items-center justify-between gap-2 border-b p-4">
        <h2 id="feed-h" className="font-display text-lg font-semibold">Match feed</h2>
        <div className="flex gap-1 text-sm">
          {(["latest", "for_you"] as const).map((s) => (
            <button key={s} type="button" aria-pressed={sort === s} onClick={() => setSort(s)}
              className={cn("min-h-9 rounded-md px-3 text-muted-foreground", sort === s && "bg-muted font-medium text-foreground")}>
              {s === "latest" ? "Latest" : "For you"}
            </button>
          ))}
        </div>
      </div>
      <ol className="flex flex-col gap-3 overflow-y-auto p-4">
        {ranked.map((e) => <FeedCard key={e.id} e={e} matchId={matchId} />)}
      </ol>
    </aside>
  );
}

function FeedCard({ e, matchId }: { e: VisibleEvent; matchId: string }) {
  const persona = usePrefs((s) => s.persona);
  const seek = usePlayback((s) => s.seek);
  const byId = usePlayersById(matchId);
  const players = e.playerIds.map((id) => byId.get(id)).filter((p) => p !== undefined);
  return (
    <li className={cn("rounded-lg border p-3", e.critical && "border-l-4 border-l-tally", e.status === "overturned" && "opacity-75")}>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <button type="button" onClick={() => seek(e.atMs)} className="rounded px-1 tabular-nums underline-offset-2 hover:underline" aria-label={`Jump to ${formatClock(e.atMs)}`}>
          {formatClock(e.atMs)}
        </button>
        <span aria-hidden>·</span>
        <span>{SOURCE_LABELS[e.source]}</span>
        {e.status !== "confirmed" && (
          <span className={cn("ml-auto rounded px-1.5 py-0.5 font-medium", e.status === "overturned" ? "bg-blocked/15 text-blocked" : "bg-rewritten/15 text-rewritten")}>
            {e.status === "overturned" ? "Overturned" : "Provisional"}
          </span>
        )}
      </div>
      <p className={cn("mt-1 font-semibold", e.status === "overturned" && "line-through")}>{e.headline}</p>
      <p className={cn("mt-1 text-sm text-muted-foreground", e.analysis === "pending" && "italic")}>{copyFor(e, persona)}</p>
      {e.note && <p className="mt-1 text-xs text-muted-foreground">{e.note}</p>}
      {players.length > 0 && (
        <p className="mt-2 flex flex-wrap gap-2 text-xs">
          {players.map((p) => (
            <Link key={p.id} href={routes.player(matchId, p.id)} className="rounded-full border px-2 py-1 hover:bg-muted">
              {p.shirt} {p.name}
            </Link>
          ))}
        </p>
      )}
    </li>
  );
}

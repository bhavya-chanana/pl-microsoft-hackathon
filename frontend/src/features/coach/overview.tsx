"use client";

import { Check, ChevronDown, Pin, PinOff, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { useShell } from "@/components/shell/shell-store";
import { useCoachBrief, usePulse, useRatings, useTeamStats } from "@/features/live/queries";
import { XgTimeline } from "@/features/report/match-report";
import { useProfile } from "@/features/profile/profile-store";
import type { CoachBrief, CoachItem, Confidence, MatchEvent, MatchMeta } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { briefKey, useCoach } from "./coach-store";
import { RatingBadge } from "./rating";

const CONFIDENCE_TONE: Record<Confidence, string> = {
  high: "bg-verified/15 text-verified",
  medium: "bg-data/15 text-data",
  low: "bg-muted text-muted-foreground",
};

function ActionCard({ item, side, minute }: { item: CoachItem; side: "home" | "away"; minute: string }) {
  const pinned = useCoach((s) => s.pinned.includes(item.issue_id));
  const open = useCoach((s) => s.expanded.includes(item.issue_id));
  const detail = `detail-${item.issue_id}`;
  return (
    <li className={cn("relative flex gap-4 overflow-hidden rounded-[20px] border bg-card p-4 pl-5 shadow-raised sm:p-5 sm:pl-6", pinned && "ring-1 ring-primary/50")}>
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1.5", side === "home" ? "bg-home" : "bg-away")} />
      <span aria-hidden className="font-display text-[40px] leading-none font-bold text-muted-foreground tabular-nums">
        {item.rank}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h3 className="font-display text-xl font-semibold">
            <span className="sr-only">{item.rank}. </span>
            {item.title}
          </h3>
          <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[13px] font-medium", CONFIDENCE_TONE[item.confidence])} title={copy.coach.confidenceHelp}>
            <span aria-hidden className="size-1.5 rounded-full bg-current" />
            {copy.coach.confidence[item.confidence]}
          </span>
          <span className="ml-auto flex items-center gap-0.5">
            <Button variant="ghost" size="icon" className="size-11" aria-pressed={pinned} aria-label={pinned ? copy.coachPage.unpin : copy.coachPage.pin} onClick={() => useCoach.getState().togglePin(item.issue_id)}>
              {pinned ? <PinOff aria-hidden /> : <Pin aria-hidden />}
            </Button>
            <Button variant="ghost" size="icon" className="size-11" aria-label={copy.coachPage.dismiss} onClick={() => useCoach.getState().dismiss(item.issue_id)}>
              <X aria-hidden />
            </Button>
          </span>
        </div>
        <div>
          <p className="text-[13px] font-medium text-muted-foreground">{copy.coach.recommendation}</p>
          <p className="text-base font-medium">{item.recommendation}</p>
        </div>
        {open && (
          <dl id={detail} className="grid gap-3 text-base">
            <div>
              <dt className="text-[13px] font-medium text-muted-foreground">{copy.coach.evidence}</dt>
              <dd>{item.evidence}</dd>
            </div>
            <div>
              <dt className="text-[13px] font-medium text-muted-foreground">{copy.coach.tradeOff}</dt>
              <dd>{item.trade_off}</dd>
            </div>
          </dl>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {item.key_numbers.map((k) => (
            <span key={k.label} className="rounded-md border px-2.5 py-1 text-sm" title={`${copy.coachPage.asOfMinute(minute)}`}>
              <span className="text-muted-foreground">{k.label} </span>
              <span className="font-semibold tabular-nums">{k.value}</span>
              {k.unit && <span className="text-muted-foreground"> {k.unit}</span>}
            </span>
          ))}
          <button
            type="button"
            aria-expanded={open}
            aria-controls={detail}
            onClick={() => useCoach.getState().toggleExpanded(item.issue_id)}
            className="ml-auto inline-flex h-11 items-center gap-1 rounded-md px-2 text-sm font-medium text-muted-foreground hover:text-foreground"
          >
            {open ? copy.coachPage.hideWhy : copy.coachPage.showWhy}
            <ChevronDown aria-hidden className={cn("size-4 transition-transform", open && "rotate-180")} />
          </button>
        </div>
      </div>
    </li>
  );
}

/** Momentum over the match: the home side rises above the centre line, the away side falls below it. */
function MomentumStrip({ meta }: { meta: MatchMeta }) {
  const rows = usePulse().data?.rows ?? [];
  if (rows.length < 3) return null;
  const W = 300;
  const H = 56;
  const end = Math.max(5400, rows[rows.length - 1].elapsed);
  const X = (t: number) => (t / end) * W;
  const Y = (v: number) => H / 2 - (Math.max(-100, Math.min(100, v)) / 100) * (H / 2 - 2);
  const line = rows.map((r, i) => `${i ? "L" : "M"} ${X(r.elapsed).toFixed(1)} ${Y(r.momentum).toFixed(1)}`).join(" ");
  const area = (sign: 1 | -1) =>
    `M ${X(rows[0].elapsed)} ${H / 2} ` +
    rows.map((r) => `L ${X(r.elapsed).toFixed(1)} ${Y(sign * Math.max(0, sign * r.momentum)).toFixed(1)}`).join(" ") +
    ` L ${X(rows[rows.length - 1].elapsed)} ${H / 2} Z`;
  return (
    <figure className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-14 w-full" role="img" aria-label={copy.coachPage.momentum}>
        <line x1={0} x2={W} y1={H / 2} y2={H / 2} className="stroke-border" strokeWidth={0.6} />
        <path d={area(1)} className="fill-home/35" />
        <path d={area(-1)} className="fill-away/35" />
        <path d={line} className="fill-none stroke-foreground/60" strokeWidth={1} strokeLinejoin="round" />
      </svg>
      <figcaption className="flex justify-between text-[13px] text-muted-foreground">
        <span>{copy.coachPage.momentum}</span>
        <span>
          <span className="text-home-text">{meta.home.short_name}</span> up, <span className="text-away-text">{meta.away.short_name}</span> down
        </span>
      </figcaption>
    </figure>
  );
}

/** A 36px two-segment bar with the percentage inside (possession, territory). */
function SplitBar({ label, home, away }: { label: string; home: number; away: number }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-center text-sm text-muted-foreground">{label}</span>
      <div className="flex h-9 gap-2" role="img" aria-label={`${label}: ${home}% to ${away}%`}>
        <span className="flex items-center rounded-full bg-home px-3 text-sm font-medium text-white tabular-nums transition-[flex-grow] duration-500" style={{ flexGrow: home || 1, flexBasis: 0 }}>{home}%</span>
        <span className="flex items-center justify-end rounded-full bg-away px-3 text-sm font-medium text-[#1d1d1d] tabular-nums transition-[flex-grow] duration-500" style={{ flexGrow: away || 1, flexBasis: 0 }}>{away}%</span>
      </div>
    </div>
  );
}

/** xG race, momentum, territory and possession. Thin samples say so instead of showing 0.00. */
function GameSoFar({ meta, half, wide }: { meta: MatchMeta; half: boolean; wide: boolean }) {
  const timeline = useLive((s) => s.timeline);
  const elapsed = useLive((s) => s.state?.elapsed ?? 0);
  const home = useTeamStats(meta.home.team_id).data;
  const away = useTeamStats(meta.away.team_id).data;
  const shown = useMemo(() => (half ? timeline.filter((e) => e.period === 1) : timeline), [timeline, half]);
  const shots = timeline.filter((e) => e.type === "shot").length;
  const thin = shots < 10 && elapsed < 900;
  const split = (h: number | null | undefined, a: number | null | undefined) => {
    if (h == null || a == null || h + a <= 0) return null;
    const x = Math.round((h / (h + a)) * 100);
    return { home: x, away: 100 - x };
  };
  const possession = split(home?.possession_pct, away?.possession_pct);
  const tilt = split(home?.field_tilt_pct, away?.field_tilt_pct);
  return (
    <section aria-labelledby="gsf" className={cn("flex flex-col gap-4 rounded-[20px] border bg-card p-4 shadow-raised sm:p-5", wide && "lg:col-span-2")}>
      <h2 id="gsf" className="font-display text-xl font-semibold">
        {copy.coachPage.gameSoFar}
        {half && <span className="ml-2 text-[13px] font-normal text-muted-foreground">{copy.coachPage.firstHalf}</span>}
      </h2>
      <div className="grid gap-4 min-[700px]:grid-cols-[340px_minmax(0,1fr)] min-[700px]:gap-6 min-[1100px]:grid-cols-1">
      {thin ? (
        <p className="text-[15px] text-muted-foreground">{copy.coachPage.earlyDays}</p>
      ) : (
        <div className="flex flex-col gap-1">
          <div className="flex flex-col gap-1 min-[700px]:max-w-[340px] min-[1100px]:max-w-none"><span className="text-[13px] font-medium text-muted-foreground">xG</span><XgTimeline meta={meta} timeline={shown} minutes={half ? 45 : 90} /></div>
        </div>
      )}
      <div className="flex flex-col gap-4">
      <MomentumStrip meta={meta} />
      <div className="flex flex-col gap-3">
        {tilt && <SplitBar label={copy.coachPage.territory} home={tilt.home} away={tilt.away} />}
        {possession ? <SplitBar label={copy.stats.labels.possession} home={possession.home} away={possession.away} /> : <Skeleton className="h-12 w-full" aria-hidden />}
      </div>
      </div>
      </div>
    </section>
  );
}

function bookedIds(timeline: MatchEvent[]): Set<string> {
  const out = new Set<string>();
  for (const e of timeline) if (e.type === "card" && e.player_id) out.add(e.player_id);
  return out;
}

/** Players worth a look: under 6.0, carrying a card, or taken off. */
function WatchList({ teamId, onLineup }: { teamId: string; onLineup: () => void }) {
  const ratings = useRatings().data;
  const timeline = useLive((s) => s.timeline);
  const booked = useMemo(() => bookedIds(timeline), [timeline]);
  const subbedOff = useMemo(() => new Set(timeline.filter((e) => e.type === "substitution" && e.player_id).map((e) => e.player_id as string)), [timeline]);
  const list = (ratings ?? []).filter((r) => r.team_id === teamId && (r.data_rating < 6 || booked.has(r.player_id) || subbedOff.has(r.player_id))).sort((a, b) => a.data_rating - b.data_rating);
  if (!list.length) return null;
  return (
    <section aria-labelledby="watch" className="flex flex-col gap-3 rounded-[20px] border bg-card p-4 shadow-raised sm:p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 id="watch" className="font-display text-xl font-semibold">{copy.coachPage.watchList}</h2>
        <Button variant="ghost" className="h-11 px-3" onClick={onLineup}>{copy.coachPage.seeLineup}</Button>
      </div>
      <ul className="flex flex-wrap gap-2">
        {list.map((r) => (
          <li key={r.player_id}>
            <button type="button" onClick={() => useShell.getState().setPlayerId(r.player_id)} className="flex h-11 items-center gap-2 rounded-full bg-raised pr-3.5 pl-2 text-[15px] ring-1 ring-border hover:bg-foreground/10">
              <RatingBadge value={r.data_rating} />
              {r.short_name}
              {booked.has(r.player_id) && <span aria-label={copy.coachPage.booked} className="h-3.5 w-2.5 rounded-[2px] bg-amber-400" />}
              {subbedOff.has(r.player_id) && <span className="text-[13px] text-muted-foreground">{copy.coachPage.off}</span>}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function TopPlayers({ meta }: { meta: MatchMeta }) {
  const ratings = useRatings().data ?? [];
  const top = [...ratings].sort((a, b) => b.data_rating - a.data_rating).slice(0, 3);
  if (!top.length) return null;
  return (
    <section aria-labelledby="mr" className="flex flex-col gap-3 rounded-[20px] border bg-card p-4 shadow-raised sm:p-5">
      <h2 id="mr" className="font-display text-xl font-semibold">{copy.coachPage.matchRating}</h2>
      <ul className="flex flex-wrap gap-2">
        {top.map((r, i) => (
          <li key={r.player_id}>
            <button type="button" onClick={() => useShell.getState().setPlayerId(r.player_id)} className="flex h-11 items-center gap-2 rounded-full bg-raised pr-3.5 pl-2 text-[15px] ring-1 ring-border hover:bg-foreground/10">
              <RatingBadge value={r.data_rating} potm={i === 0} />
              {r.short_name}
              <span aria-hidden className={cn("size-2 rounded-full", r.team_id === meta.home.team_id ? "bg-home" : "bg-away")} />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Up to three chips: what is new or moved since the previous brief. */
function changes(brief: CoachBrief, prev: CoachBrief | null): string[] {
  if (!prev) return [];
  const out: string[] = [];
  for (const item of brief.items) {
    const was = prev.items.find((p) => p.issue_id === item.issue_id);
    if (!was) out.push(copy.coachPage.newItem(item.title));
    else if (item.rank < was.rank) out.push(copy.coachPage.movedUp(item.title));
    else if (item.confidence !== was.confidence) {
      const order = ["low", "medium", "high"];
      out.push((order.indexOf(item.confidence) > order.indexOf(was.confidence) ? copy.coachPage.confidenceUp : copy.coachPage.confidenceDown)(item.title));
    }
  }
  return out.slice(0, 3);
}

function useAgeSeconds(at: number | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, []);
  return at ? Math.max(0, Math.round((now - at) / 1000)) : 0;
}

/** The 90-second view: what changed, what to do, how the game is going. Cache-first brief. */
export function Overview({ onReport, onLineup }: { onReport: () => void; onLineup: () => void }) {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const finished = useLive((s) => s.status === "finished" || s.status === "archived");
  const language = useProfile((s) => s.language);
  const picked = useCoach((s) => s.teamId);
  const teamId = picked ?? meta?.home.team_id ?? null;
  const query = useCoachBrief(finished ? null : teamId, language);
  const key = teamId ? briefKey(teamId, language) : "";
  const cached = useCoach((s) => s.briefs[key]);
  const dismissed = useCoach((s) => s.dismissed);
  const pinned = useCoach((s) => s.pinned);
  const live = query.data;
  useEffect(() => {
    if (live && key) useCoach.getState().save(key, live);
  }, [live, key]);
  const brief = live ?? cached?.brief;
  const age = useAgeSeconds(cached?.at);
  const strip = useMemo(() => (brief ? changes(brief, cached?.prev ?? null) : []), [brief, cached?.prev]);
  const side = meta && teamId === meta.away.team_id ? "away" : "home";
  const items = useMemo(() => {
    const all = (brief?.items ?? []).filter((i) => !dismissed.includes(i.issue_id));
    return [...all.filter((i) => pinned.includes(i.issue_id)), ...all.filter((i) => !pinned.includes(i.issue_id))].slice(0, 3);
  }, [brief, dismissed, pinned]);
  const hidden = (brief?.items.length ?? 0) - items.length;
  const half = !finished && Boolean(state && state.elapsed >= 2700 && state.elapsed <= 3600);
  if (!meta || !teamId) return <Skeleton className="h-64 w-full rounded-[20px]" aria-hidden />;

  const actions = finished ? (
    <section className="flex flex-col items-start gap-3 rounded-[20px] border bg-card p-5 shadow-raised">
      <p className="font-display text-xl font-semibold">{copy.coachPage.fullTimeShort}</p>
      <p className="text-[15px] text-muted-foreground">{copy.coachPage.fullTime}</p>
      <Button className="h-12 px-6 text-base" onClick={onReport}>{copy.coachPage.seeReport}</Button>
    </section>
  ) : (
    <section aria-busy={query.isFetching} aria-label={copy.report.actions} lang={language} className="flex flex-col gap-4">
      <p className="text-[13px] text-muted-foreground tabular-nums">
        {brief && `${copy.coach.asOf(brief.minute)} · ${query.isFetching ? copy.coachPage.refreshing : copy.coachPage.updated(age)}`}
      </p>
      {!brief && query.isPending ? (
        <div role="status" className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">{copy.coach.loading}</p>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-40 w-full rounded-[20px]" />
          ))}
        </div>
      ) : query.isError && !brief ? (
        <p role="alert" className="text-sm">{copy.coach.error}</p>
      ) : brief ? (
        <>
          <ol className="flex flex-col gap-4">
            {items.map((item) => (
              <ActionCard key={item.issue_id} item={item} side={side} minute={brief.minute} />
            ))}
          </ol>
          {hidden > 0 && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              {copy.coachPage.dismissedN(hidden)}
              <Button variant="ghost" className="h-11 px-3" onClick={() => useCoach.getState().restore()}>{copy.coachPage.undo}</Button>
            </p>
          )}
        </>
      ) : null}
    </section>
  );
  return (
    <div className="grid items-start gap-4 py-4 min-[1100px]:grid-cols-12 min-[1100px]:gap-6">
      <div className={cn("order-2 flex min-w-0 flex-col gap-4 min-[1100px]:order-1", finished ? "min-[1100px]:col-span-12" : "min-[1100px]:col-span-7")}>
        {actions}
      </div>
      <div className={cn("order-1 min-w-0 min-[1100px]:order-2", finished ? "min-[1100px]:col-span-12" : "min-[1100px]:col-span-5")}>
        <GameSoFar meta={meta} half={half} wide={false} />
      </div>
      <div className="order-3 flex min-w-0 flex-col gap-4 min-[1100px]:col-span-12">
        {finished ? <TopPlayers meta={meta} /> : <WatchList teamId={teamId} onLineup={onLineup} />}
        {!finished && strip.length > 0 && (
          <section aria-label={copy.coachPage.changed} className="flex flex-col gap-2 rounded-[20px] border bg-card p-4">
            <span className="text-[13px] font-medium text-muted-foreground">{copy.coachPage.changed}</span>
            <ul className="flex flex-wrap gap-2">
              {strip.map((c) => (
                <li key={c} className="rounded-full bg-raised px-3 py-1.5 text-[13px] ring-1 ring-border">{c}</li>
              ))}
            </ul>
          </section>
        )}
        {brief && !finished && (
          <Popover>
            <PopoverTrigger className="inline-flex min-h-11 w-fit items-center gap-1.5 rounded-md text-[13px] text-muted-foreground hover:text-foreground">
              <Check aria-hidden className="size-4 text-verified" />
              {copy.coachPage.checked}
            </PopoverTrigger>
            <PopoverContent className="w-72 text-sm">{copy.coach.pipeline}</PopoverContent>
          </Popover>
        )}
      </div>
    </div>
  );
}


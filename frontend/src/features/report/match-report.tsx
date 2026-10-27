"use client";

import { ArrowLeftRight, Link2, Printer, RectangleVertical } from "lucide-react";
import { useTheme } from "next-themes";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { briefKey, useCoach } from "@/features/coach/coach-store";
import { RatingBadge } from "@/features/coach/rating";
import { useLive } from "@/features/live/live-store";
import { useCoachBrief, usePlayersStats, useRatings, useTeamStats } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import { ShotMapPanel } from "@/features/stats/shot-map";
import type { MatchEvent, MatchMeta, PlayerStats } from "@/lib/contract";
import { copy, eventLabels } from "@/lib/copy";
import { playerName, sideOf, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

const R = copy.report;

function Section({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("flex flex-col gap-3 break-inside-avoid rounded-2xl border bg-card p-4 shadow-raised sm:p-5 print:shadow-none", className)}>
      <h2 className="font-display text-xl font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** Cumulative chances created per team as a step chart: minute ticks, goal dots on the line, end totals. */
export function XgTimeline({ meta, timeline, minutes = 90 }: { meta: MatchMeta; timeline: MatchEvent[]; minutes?: number }) {
  const series = useMemo(() => {
    const out = { home: [{ t: 0, v: 0 }], away: [{ t: 0, v: 0 }] };
    const total = { home: 0, away: 0 };
    const goals: { t: number; v: number; side: "home" | "away" }[] = [];
    let end = minutes * 60;
    for (const e of timeline) {
      end = Math.max(end, e.elapsed);
      const side = sideOf(meta, e.team_id);
      if (!side || e.type !== "shot") continue;
      if (typeof e.attrs.xg === "number") total[side] += e.attrs.xg;
      out[side].push({ t: e.elapsed, v: total[side] });
      if (e.outcome === "goal") goals.push({ t: e.elapsed, v: total[side], side });
    }
    return { ...out, goals, end, max: Math.max(0.5, total.home, total.away), total };
  }, [meta, timeline, minutes]);
  const W = 300;
  const H = 120;
  const pad = { l: 26, r: 8, t: 8, b: 20 };
  const X = (t: number) => pad.l + (t / series.end) * (W - pad.l - pad.r);
  const Y = (v: number) => H - pad.b - (v / series.max) * (H - pad.t - pad.b);
  const path = (pts: { t: number; v: number }[]) =>
    pts.map((p, i) => `${i === 0 ? "M" : "L"} ${X(p.t)} ${Y(pts[i - 1]?.v ?? 0)} L ${X(p.t)} ${Y(p.v)}`).join(" ") + ` L ${X(series.end)} ${Y(pts[pts.length - 1].v)}`;
  const ticks = (minutes < 90 ? [0, 15, 30, 45] : [0, 45, 90]).filter((m) => m * 60 <= series.end);
  const yTicks = [0, series.max / 2, series.max];
  return (
    <figure className="flex flex-col gap-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${R.xgTimeline}: ${meta.home.short_name} ${series.total.home.toFixed(2)}, ${meta.away.short_name} ${series.total.away.toFixed(2)}`}
      >
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={W - pad.r} y1={Y(v)} y2={Y(v)} className="stroke-border" strokeWidth={0.5} />
            <text x={pad.l - 4} y={Y(v) + 3} textAnchor="end" fontSize={8} className="fill-muted-foreground">
              {v.toFixed(1)}
            </text>
          </g>
        ))}
        {ticks.map((m) => (
          <text key={m} x={X(m * 60)} y={H - 6} textAnchor={m === 0 ? "start" : m === minutes ? "end" : "middle"} fontSize={8} className="fill-muted-foreground">
            {m}&prime;
          </text>
        ))}
        <line x1={X(2700)} x2={X(2700)} y1={pad.t} y2={H - pad.b} className="stroke-border" strokeDasharray="3 3" strokeWidth={0.6} />
        <path d={path(series.home)} className={cn("fill-none", sideTone.home.stroke)} strokeWidth={1.8} strokeLinejoin="round" />
        <path d={path(series.away)} className={cn("fill-none", sideTone.away.stroke)} strokeWidth={1.8} strokeLinejoin="round" />
        {series.goals.map((g, i) => (
          <circle key={i} cx={X(g.t)} cy={Y(g.v)} r={3.2} className={cn(sideTone[g.side].fill, "stroke-card")} strokeWidth={1.2} />
        ))}
      </svg>
      <figcaption className="flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-muted-foreground">
        {(["home", "away"] as const).map((side) => (
          <span key={side} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={cn("size-2.5 rounded-full", sideTone[side].bg)} />
            {meta[side].short_name} <b className="font-semibold text-foreground tabular-nums">{series.total[side].toFixed(2)}</b>
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-full bg-foreground" />
          {copy.stats.goal}
        </span>
      </figcaption>
    </figure>
  );
}

function Possession({ meta }: { meta: MatchMeta }) {
  const h = useTeamStats(meta.home.team_id).data;
  const a = useTeamStats(meta.away.team_id).data;
  if (!h || !a) return null;
  const total = (h.possession_pct ?? 0) + (a.possession_pct ?? 0) || 1;
  const home = Math.round(((h.possession_pct ?? 0) / total) * 100);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-between font-display text-3xl font-semibold tabular-nums">
        <span className={sideTone.home.text}>{home}%</span>
        <span className={sideTone.away.text}>{100 - home}%</span>
      </div>
      <div aria-hidden className="flex h-2 gap-0.5 overflow-hidden rounded-full">
        <span className="bg-home" style={{ flexGrow: home || 0.001 }} />
        <span className="bg-away" style={{ flexGrow: 100 - home || 0.001 }} />
      </div>
    </div>
  );
}

const ROWS = 6;

function EventIcon({ e }: { e: MatchEvent }) {
  if (e.type === "substitution") return <ArrowLeftRight aria-hidden className="size-4 text-muted-foreground" />;
  const red = e.outcome.includes("red") || e.outcome === "second_yellow";
  return <RectangleVertical aria-hidden className={cn("size-4", red ? "fill-red-500 text-red-500" : "fill-amber-400 text-amber-400")} />;
}

function SubsCards({ meta, timeline }: { meta: MatchMeta; timeline: MatchEvent[] }) {
  const [all, setAll] = useState(false);
  const rows = timeline.filter((e) => e.type === "substitution" || e.type === "card");
  if (!rows.length) return <p className="text-sm text-muted-foreground">{R.noSubsCards}</p>;
  const shown = all ? rows : rows.slice(0, ROWS);
  return (
    <div className="flex flex-col gap-2">
      <ul className="divide-y">
        {shown.map((e) => {
          const side = sideOf(meta, e.team_id);
          const label = `${eventLabels[e.type] ?? e.type}${e.type === "card" && e.outcome ? `, ${e.outcome.replaceAll("_", " ")}` : ""}`;
          return (
            <li key={e.event_id} className="grid h-11 grid-cols-[2.5rem_1.25rem_minmax(0,1fr)_auto] items-center gap-2 text-[15px]">
              <span className="font-display font-semibold tabular-nums">{e.minute_label}</span>
              <EventIcon e={e} />
              <span className="truncate">
                <span className="sr-only">{label}: </span>
                {playerName(meta, e.player_id, true)}
              </span>
              <span className="text-[13px] text-muted-foreground">{side ? meta[side].short_name : ""}</span>
            </li>
          );
        })}
      </ul>
      {rows.length > ROWS && (
        <Button variant="ghost" className="h-11 self-start px-3 print:hidden" onClick={() => setAll((v) => !v)}>
          {all ? R.showFewer : R.showAll(rows.length)}
        </Button>
      )}
    </div>
  );
}

/** One line on why a player stood out, from the stats the feed gives. */
function whyLine(s: PlayerStats | undefined): string {
  if (!s) return "";
  const W = R.why;
  const bits: string[] = [];
  if (s.goals) bits.push(W.goals(s.goals));
  if (s.assists) bits.push(W.assists(s.assists));
  if (s.shots) bits.push(W.shots(s.shots, s.shots_on_target));
  if (s.key_passes) bits.push(W.keyPasses(s.key_passes));
  if (s.saves) bits.push(W.saves(s.saves));
  if (s.tackles + s.interceptions >= 3) bits.push(W.defence(s.tackles + s.interceptions));
  return bits.slice(0, 3).join(", ") || W.none;
}

function KeyPlayers({ meta }: { meta: MatchMeta }) {
  const ratings = useRatings().data ?? [];
  const top = [...ratings].sort((a, b) => b.data_rating - a.data_rating).slice(0, 3);
  const stats = usePlayersStats(top.map((p) => p.player_id));
  if (!top.length) return <p className="text-sm text-muted-foreground">{copy.playersTable.empty}</p>;
  return (
    <ol className="flex flex-col divide-y">
      {top.map((p) => (
        <li key={p.player_id} className="flex min-h-14 items-center gap-3 py-2">
          <span aria-hidden className={cn("h-8 w-1 shrink-0 rounded-full", p.team_id === meta.home.team_id ? "bg-home" : "bg-away")} />
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="font-medium">{p.name}</span>
            <span className="text-[13px] text-muted-foreground">{whyLine(stats.get(p.player_id))}</span>
          </span>
          <RatingBadge value={p.data_rating} size="lg" />
        </li>
      ))}
    </ol>
  );
}

/** Goalscorers under one team's name. */
function Scorers({ meta, timeline, only }: { meta: MatchMeta; timeline: MatchEvent[]; only: "home" | "away" }) {
  const goals = timeline.filter((e) => e.type === "goal" && sideOf(meta, e.team_id) === only);
  if (!goals.length) return null;
  return (
    <p className="text-[13px] text-muted-foreground">
      <span className="sr-only">{R.scorers}: </span>
      {goals.map((e) => `${playerName(meta, e.player_id, true)} ${e.minute_label}`).join(", ")}
    </p>
  );
}

/** Shots and chances created, split by half. */
function ByHalf({ meta, timeline }: { meta: MatchMeta; timeline: MatchEvent[] }) {
  const rows = [1, 2].map((period) => {
    const pick = (side: "home" | "away") => {
      const shots = timeline.filter((e) => e.type === "shot" && e.period === period && sideOf(meta, e.team_id) === side);
      return { shots: shots.length, xg: shots.reduce((sum, e) => sum + (typeof e.attrs.xg === "number" ? e.attrs.xg : 0), 0), goals: shots.filter((e) => e.outcome === "goal").length };
    };
    return { period, home: pick("home"), away: pick("away") };
  }).filter((r) => r.period === 1 || r.home.shots + r.away.shots > 0);
  return (
    <table className="w-full text-[15px]">
      <caption className="sr-only">{R.byHalf}</caption>
      <thead className="text-[13px] text-muted-foreground">
        <tr>
          <th scope="col" className="py-1 text-left font-medium"><span className="sr-only">Half</span></th>
          <th scope="col" className="py-1 text-right font-medium">{meta.home.short_name}</th>
          <th scope="col" className="py-1 text-right font-medium">{meta.away.short_name}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.period} className="border-t align-top">
            <th scope="row" className="py-2 text-left font-medium">{r.period === 1 ? R.firstHalf : R.secondHalf}</th>
            {(["home", "away"] as const).map((side) => (
              <td key={side} className="py-2 text-right tabular-nums">
                <div className="font-semibold">{r[side].goals} <span className="font-normal text-muted-foreground">goals</span></div>
                <div className="text-[13px] text-muted-foreground">{r[side].shots} shots, xG {r[side].xg.toFixed(2)}</div>
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** One page, print-friendly: the story of the match for the bench. */
export function MatchReport() {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const timeline = useLive((s) => s.timeline);
  const finished = useLive((s) => s.status === "finished" || s.status === "archived");
  const language = useProfile((s) => s.language);
  const teamId = useCoach((s) => s.teamId) ?? meta?.home.team_id ?? null;
  const stored = useCoach((s) => (teamId ? s.briefs[briefKey(teamId, language)]?.brief : undefined));
  // The report fetches its own brief, so it never asks the coach to visit the Brief tab first.
  const fetched = useCoachBrief(finished ? null : teamId, language);
  const brief = fetched.data ?? stored;
  const { resolvedTheme, setTheme } = useTheme();
  if (!meta || !state) return null;
  const phase = finished ? R.phase.full : state.elapsed >= 2700 && state.elapsed <= 3600 ? R.phase.half : R.phase.latest;
  /** Print on paper colours, then put the viewer's theme back. */
  const printLight = () => {
    const back = resolvedTheme ?? "dark";
    setTheme("light");
    window.addEventListener("afterprint", () => setTheme(back), { once: true });
    // Print only once the light theme is really applied (and painted), or the snapshot stays dark.
    const started = Date.now();
    const go = () => {
      const applied = document.documentElement.classList.contains("light");
      if (!applied && Date.now() - started < 1500) return void setTimeout(go, 50);
      requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(() => window.print(), 100)));
    };
    go();
  };
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast.success(R.linkCopied);
    } catch {
      /* clipboard blocked */
    }
  };
  return (
    <article className="match-report flex flex-col gap-4">
      <header className="flex flex-col gap-4 rounded-2xl border bg-card p-4 sm:p-5 print:shadow-none">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="rounded-full bg-raised px-3 py-1.5 text-[13px] font-medium ring-1 ring-border tabular-nums">
            {phase} · {state.minute_label}
          </span>
          <div className="flex gap-2 print:hidden">
            <Button variant="secondary" className="h-11 gap-2 px-4" onClick={() => void copyLink()}>
              <Link2 aria-hidden />
              {R.copyLink}
            </Button>
            <Button className="h-11 gap-2 px-4" onClick={() => printLight()}>
              <Printer aria-hidden />
              {R.export}
            </Button>
          </div>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-start gap-3 sm:gap-6">
          <div className="flex min-w-0 flex-col items-end gap-1 text-right">
            <span className={cn("flex items-center gap-2 text-lg font-medium", sideTone.home.text)}>
              <span className="truncate">{meta.home.name}</span>
              <span aria-hidden className="size-3 shrink-0 rounded-full bg-home" />
            </span>
            <Scorers meta={meta} timeline={timeline} only="home" />
          </div>
          <p className="font-display text-[44px] leading-[48px] font-bold whitespace-nowrap tabular-nums sm:text-[56px] sm:leading-[56px]">
            {state.score[meta.home.team_id] ?? 0} - {state.score[meta.away.team_id] ?? 0}
          </p>
          <div className="flex min-w-0 flex-col items-start gap-1">
            <span className={cn("flex items-center gap-2 text-lg font-medium", sideTone.away.text)}>
              <span aria-hidden className="size-3 shrink-0 rounded-full bg-away" />
              <span className="truncate">{meta.away.name}</span>
            </span>
            <Scorers meta={meta} timeline={timeline} only="away" />
          </div>
        </div>
        <p className="border-t pt-3 text-center text-[13px] text-muted-foreground">
          {meta.competition} · {meta.venue}
        </p>
      </header>
      <div className="grid items-start gap-4 lg:grid-cols-2 [&>*]:min-w-0">
        <div className="flex min-w-0 flex-col gap-4">
          <Section title={R.actions}>
            {finished ? (
              <p className="text-[15px] text-muted-foreground">{R.matchOver}</p>
            ) : brief?.items.length ? (
              <ol className="flex flex-col gap-3">
                {brief.items.slice(0, 3).map((i) => (
                  <li key={i.issue_id} className="flex gap-3">
                    <span aria-hidden className="font-display text-3xl leading-none font-bold text-muted-foreground tabular-nums">{i.rank}</span>
                    <div className="flex flex-col">
                      <span className="font-semibold">{i.title}</span>
                      <span className="text-[15px] text-muted-foreground">{i.recommendation}</span>
                    </div>
                  </li>
                ))}
              </ol>
            ) : fetched.isPending ? (
              <p className="text-sm text-muted-foreground">{R.loadingActions}</p>
            ) : (
              <p className="text-sm text-muted-foreground">{R.noActions}</p>
            )}
          </Section>
          <Section title={R.xgTimelineAnalyst}>
            <XgTimeline meta={meta} timeline={timeline} />
          </Section>
          <Section title={R.possession}>
            <Possession meta={meta} />
          </Section>
          <Section title={R.byHalf}>
            <ByHalf meta={meta} timeline={timeline} />
          </Section>
          <Section title={R.keyPlayers}>
            <KeyPlayers meta={meta} />
          </Section>
        </div>
        <div className="flex flex-col gap-4">
          <ShotMapPanel className="break-inside-avoid print:shadow-none" />
          <Section title={R.subsCards}>
            <SubsCards meta={meta} timeline={timeline} />
          </Section>
        </div>
      </div>
    </article>
  );
}

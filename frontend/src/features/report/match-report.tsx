"use client";

import { Printer, Link2 } from "lucide-react";
import { useTheme } from "next-themes";
import { useMemo } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { briefKey, useCoach } from "@/features/coach/coach-store";
import { useLive } from "@/features/live/live-store";
import { useRatings, useTeamStats } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import { ShotMapPanel } from "@/features/stats/shot-map";
import type { MatchEvent, MatchMeta } from "@/lib/contract";
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

/** Cumulative chances created per team as a step chart, goals marked. */
function XgTimeline({ meta, timeline }: { meta: MatchMeta; timeline: MatchEvent[] }) {
  const series = useMemo(() => {
    const out = { home: [{ t: 0, v: 0 }], away: [{ t: 0, v: 0 }] };
    const total = { home: 0, away: 0 };
    const goals: { t: number; side: "home" | "away" }[] = [];
    let end = 90 * 60;
    for (const e of timeline) {
      end = Math.max(end, e.elapsed);
      const side = sideOf(meta, e.team_id);
      if (!side) continue;
      if (e.type === "shot" && typeof e.attrs.xg === "number") {
        total[side] += e.attrs.xg;
        out[side].push({ t: e.elapsed, v: total[side] });
      }
      if (e.type === "goal" || (e.type === "shot" && e.outcome === "goal")) goals.push({ t: e.elapsed, side });
    }
    return { ...out, goals, end, max: Math.max(0.5, total.home, total.away) };
  }, [meta, timeline]);
  const X = (t: number) => (t / series.end) * 100;
  const Y = (v: number) => 40 - (v / series.max) * 36;
  const path = (pts: { t: number; v: number }[]) =>
    pts.map((p, i) => `${i === 0 ? "M" : "L"} ${X(p.t)} ${Y(pts[i - 1]?.v ?? 0)} L ${X(p.t)} ${Y(p.v)}`).join(" ") + ` L 100 ${Y(pts[pts.length - 1].v)}`;
  const last = (s: "home" | "away") => series[s][series[s].length - 1].v;
  return (
    <figure className="flex flex-col gap-2">
      <svg viewBox="0 0 100 42" preserveAspectRatio="none" className="h-40 w-full" role="img" aria-label={`${R.xgTimeline}: ${meta.home.short_name} ${last("home").toFixed(2)}, ${meta.away.short_name} ${last("away").toFixed(2)}`}>
        <line x1={0} y1={40} x2={100} y2={40} className="stroke-border" strokeWidth={0.4} vectorEffect="non-scaling-stroke" />
        <line x1={X(2700)} y1={0} x2={X(2700)} y2={40} className="stroke-border" strokeDasharray="2 2" strokeWidth={0.4} vectorEffect="non-scaling-stroke" />
        <path d={path(series.home)} className={cn("fill-none", sideTone.home.stroke)} strokeWidth={2} vectorEffect="non-scaling-stroke" />
        <path d={path(series.away)} className={cn("fill-none", sideTone.away.stroke)} strokeWidth={2} vectorEffect="non-scaling-stroke" />
        {series.goals.map((g, i) => (
          <line key={i} x1={X(g.t)} x2={X(g.t)} y1={36} y2={40} className={sideTone[g.side].stroke} strokeWidth={3} vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      <figcaption className="flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-full bg-home" />
          {meta.home.short_name} <b className="font-semibold text-foreground tabular-nums">{last("home").toFixed(2)}</b>
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-full bg-away" />
          {meta.away.short_name} <b className="font-semibold text-foreground tabular-nums">{last("away").toFixed(2)}</b>
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

function SubsCards({ meta, timeline }: { meta: MatchMeta; timeline: MatchEvent[] }) {
  const rows = timeline.filter((e) => e.type === "substitution" || e.type === "card");
  if (!rows.length) return <p className="text-sm text-muted-foreground">{R.noSubsCards}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[22rem] text-left text-[15px]">
        <thead className="text-[13px] text-muted-foreground">
          <tr>
            <th className="py-1.5 pr-3 font-medium">{R.minute}</th>
            <th className="py-1.5 pr-3 font-medium">{R.event}</th>
            <th className="py-1.5 pr-3 font-medium">{R.player}</th>
            <th className="py-1.5 font-medium">{R.team}</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((e) => {
            const side = sideOf(meta, e.team_id);
            return (
              <tr key={e.event_id} className="h-11">
                <td className="pr-3 font-display font-semibold tabular-nums">{e.minute_label}</td>
                <td className="pr-3">{eventLabels[e.type] ?? e.type}{e.type === "card" && e.outcome ? ` (${e.outcome.replaceAll("_", " ")})` : ""}</td>
                <td className="pr-3">{playerName(meta, e.player_id, true)}</td>
                <td>{side ? meta[side].short_name : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function KeyPlayers({ meta }: { meta: MatchMeta }) {
  const ratings = useRatings().data ?? [];
  const top = [...ratings].sort((a, b) => b.data_rating - a.data_rating).slice(0, 3);
  if (!top.length) return <p className="text-sm text-muted-foreground">{copy.playersTable.empty}</p>;
  return (
    <ol className="flex flex-col divide-y">
      {top.map((p) => (
        <li key={p.player_id} className="flex h-12 items-center gap-3">
          <span aria-hidden className={cn("h-5 w-1 rounded-full", p.team_id === meta.home.team_id ? "bg-home" : "bg-away")} />
          <span className="flex-1 font-medium">{p.name}</span>
          <span className="font-display text-xl font-semibold tabular-nums">{p.data_rating.toFixed(1)}</span>
        </li>
      ))}
    </ol>
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
  const brief = useCoach((s) => (teamId ? s.briefs[briefKey(teamId, language)]?.brief : undefined));
  const { resolvedTheme, setTheme } = useTheme();
  if (!meta || !state) return null;
  const phase = finished ? R.phase.full : state.elapsed >= 2700 && state.elapsed <= 3600 ? R.phase.half : R.phase.latest;
  /** Print on paper colours, then put the viewer's theme back. */
  const printLight = () => {
    const back = resolvedTheme ?? "dark";
    setTheme("light");
    window.addEventListener("afterprint", () => setTheme(back), { once: true });
    setTimeout(() => window.print(), 150);
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
      <header className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border bg-card p-4 shadow-raised sm:p-5 print:shadow-none">
        <div className="flex flex-col gap-1">
          <p className="text-[13px] font-medium text-muted-foreground">
            {R.heading} · {phase} · {state.minute_label}
          </p>
          <p className="flex items-center gap-3 font-display text-3xl font-bold tabular-nums">
            <span className={sideTone.home.text}>{meta.home.name}</span>
            {state.score[meta.home.team_id] ?? 0} - {state.score[meta.away.team_id] ?? 0}
            <span className={sideTone.away.text}>{meta.away.name}</span>
          </p>
        </div>
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
      </header>
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
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
            ) : (
              <p className="text-sm text-muted-foreground">{R.noActions}</p>
            )}
          </Section>
          <Section title={R.xgTimeline}>
            <XgTimeline meta={meta} timeline={timeline} />
          </Section>
          <Section title={R.possession}>
            <Possession meta={meta} />
          </Section>
        </div>
        <div className="flex flex-col gap-4">
          <ShotMapPanel className="break-inside-avoid print:shadow-none" />
          <Section title={R.subsCards}>
            <SubsCards meta={meta} timeline={timeline} />
          </Section>
          <Section title={R.keyPlayers}>
            <KeyPlayers meta={meta} />
          </Section>
        </div>
      </div>
    </article>
  );
}

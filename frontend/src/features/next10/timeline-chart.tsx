"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";

import type { Outcome, Prediction, Side } from "./contract";
import { n10 } from "./copy";
import { pct } from "./lib";

const H = 236;
const M = { l: 40, r: 14, t: 12, b: 44 };
const IH = H - M.t - M.b;

type Props = {
  docs: Prediction[];
  head: string;
  outcomes: Outcome[] | null;
  names: Record<Side, string>;
  /** Mark the current match minute (live). */
  nowMinute?: number | null;
  /** Mark the minute the pitch is showing. */
  cursorMinute?: number | null;
};

function nice(max: number): number {
  const steps = [0.04, 0.08, 0.2, 0.4, 0.6, 0.8, 1];
  return steps.find((s) => s >= max) ?? 1;
}

/** Team lines for one head by minute, with the actual shots and goals along the bottom once the match is scored. */
export function TimelineChart({ docs, head, outcomes, names, nowMinute, cursorMinute }: Props) {
  const titleId = useId();
  // Draw at the real pixel width so axis text stays the same size on a phone and on a desktop.
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(640);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(280, Math.round(el.getBoundingClientRect().width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const IW = W - M.l - M.r;
  const series = useMemo(() => {
    const by: Record<Side, Array<{ m: number; p: number }>> = { home: [], away: [] };
    for (const d of docs) {
      const v = d.probs[head];
      if (d.team_side && v !== undefined) by[d.team_side].push({ m: d.minute, p: v });
    }
    for (const s of ["home", "away"] as const) by[s].sort((a, b) => a.m - b.m);
    return by;
  }, [docs, head]);
  const lastMinute = Math.max(0, ...series.home.map((p) => p.m), ...series.away.map((p) => p.m));
  const xMax = Math.max(90, Math.ceil(lastMinute / 15) * 15);
  const yMax = nice(Math.max(0.02, ...series.home.map((p) => p.p), ...series.away.map((p) => p.p)) * 1.1);
  const x = (m: number) => M.l + (m / xMax) * IW;
  const y = (p: number) => M.t + IH - (p / yMax) * IH;
  const [hover, setHover] = useState<number | null>(null);
  const at = hover ?? null;
  const valueAt = (side: Side, minute: number) => {
    const pts = series[side];
    let best: { m: number; p: number } | null = null;
    for (const pt of pts) if (!best || Math.abs(pt.m - minute) < Math.abs(best.m - minute)) best = pt;
    return best && Math.abs(best.m - minute) <= 1.5 ? best.p : null;
  };
  const move = (clientX: number, rect: DOMRect) => {
    const m = ((clientX - rect.left) / rect.width) * W;
    setHover(Math.min(xMax, Math.max(0, Math.round(((m - M.l) / IW) * xMax))));
  };
  const empty = series.home.length + series.away.length === 0;
  const shots = (outcomes ?? []).filter((o) => o.type === "shot" || o.type === "goal");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * yMax);
  const path = (pts: Array<{ m: number; p: number }>) => pts.map((pt, i) => `${i ? "L" : "M"}${x(pt.m).toFixed(1)} ${y(pt.p).toFixed(1)}`).join(" ");
  const headLabel = n10.match.heads[head] ?? head;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
        {(["home", "away"] as const).map((side) => (
          <span key={side} className="inline-flex items-center gap-2">
            <svg aria-hidden width="26" height="8" viewBox="0 0 26 8">
              <line x1="0" y1="4" x2="26" y2="4" strokeWidth="3" strokeLinecap="round" strokeDasharray={side === "away" ? "6 4" : undefined} className={side === "home" ? "stroke-home" : "stroke-away"} />
            </svg>
            <span className={side === "home" ? "text-home-text" : "text-away-text"}>{names[side]}</span>
          </span>
        ))}
        {outcomes && (
          <span className="inline-flex items-center gap-3 text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <svg aria-hidden width="10" height="10"><circle cx="5" cy="5" r="3.5" className="fill-none stroke-foreground" strokeWidth="1.5" /></svg>
              {n10.match.actualShots}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <svg aria-hidden width="12" height="12"><circle cx="6" cy="6" r="5" className="fill-foreground" /></svg>
              {n10.match.actualGoals}
            </span>
          </span>
        )}
      </div>
      <div ref={box} className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-labelledby={titleId}
          tabIndex={0}
          className="block h-auto w-full touch-pan-y rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onPointerMove={(e) => move(e.clientX, e.currentTarget.getBoundingClientRect())}
          onPointerLeave={() => setHover(null)}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
              e.preventDefault();
              setHover((h) => Math.min(xMax, Math.max(0, (h ?? Math.round(nowMinute ?? lastMinute)) + (e.key === "ArrowRight" ? 1 : -1))));
            } else if (e.key === "Escape") setHover(null);
          }}
          onBlur={() => setHover(null)}
        >
          <title id={titleId}>{`${headLabel} by minute for ${names.home} and ${names.away}`}</title>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} className="stroke-border" strokeWidth={1} />
              <text x={M.l - 6} y={y(t) + 4} textAnchor="end" className="fill-subtle" style={{ fontSize: 11 }}>{Math.round(t * 1000) / 10}%</text>
            </g>
          ))}
          {Array.from({ length: Math.floor(xMax / 15) + 1 }, (_, i) => i * 15).map((m) => (
            <text key={m} x={x(m)} y={H - 6} textAnchor="middle" className="fill-subtle" style={{ fontSize: 11 }}>{m}&prime;</text>
          ))}
          {cursorMinute != null && <line x1={x(cursorMinute)} x2={x(cursorMinute)} y1={M.t} y2={M.t + IH} className="stroke-foreground" strokeWidth={1} strokeOpacity={0.5} strokeDasharray="2 3" />}
          {nowMinute != null && <line x1={x(nowMinute)} x2={x(nowMinute)} y1={M.t} y2={M.t + IH} className="stroke-live" strokeWidth={1.5} />}
          {!empty && (
            <>
              <path d={path(series.away)} className="fill-none stroke-away" strokeWidth={2.2} strokeDasharray="7 4" strokeLinejoin="round" strokeLinecap="round" />
              <path d={path(series.home)} className="fill-none stroke-home" strokeWidth={2.2} strokeLinejoin="round" strokeLinecap="round" />
            </>
          )}
          {shots.map((o, i) => {
            const cy = M.t + IH + (o.team_side === "home" ? 12 : 26);
            const cx = x(o.t_s != null ? o.t_s / 60 : o.minute);
            return o.type === "goal" ? (
              <circle key={i} cx={cx} cy={cy} r={5} className={o.team_side === "home" ? "fill-home stroke-foreground" : "fill-away stroke-foreground"} strokeWidth={1.5}><title>{`${n10.match.outcomeLabel.goal} ${Math.floor(o.minute)}'`}</title></circle>
            ) : (
              <circle key={i} cx={cx} cy={cy} r={3.5} className={cn("fill-none", o.team_side === "home" ? "stroke-home" : "stroke-away")} strokeWidth={2}><title>{`${n10.match.outcomeLabel.shot} ${Math.floor(o.minute)}'`}</title></circle>
            );
          })}
          {at != null && (
            <>
              <line x1={x(at)} x2={x(at)} y1={M.t} y2={M.t + IH} className="stroke-foreground" strokeWidth={1} strokeOpacity={0.7} />
              {(["home", "away"] as const).map((side) => {
                const v = valueAt(side, at);
                return v == null ? null : <circle key={side} cx={x(at)} cy={y(v)} r={4} className={cn("stroke-background", side === "home" ? "fill-home" : "fill-away")} strokeWidth={2} />;
              })}
            </>
          )}
        </svg>
        {empty && <p className="absolute inset-0 grid place-items-center px-6 text-center text-[15px] text-muted-foreground">{n10.match.noShadow}</p>}
      </div>
      <p className="min-h-5 text-[13px] text-muted-foreground tnum" aria-live="polite">
        {at != null
          ? `${at}′  ${names.home} ${pct(valueAt("home", at))}  ·  ${names.away} ${pct(valueAt("away", at))}`
          : outcomes
            ? ""
            : n10.match.outcomesPending}
      </p>
    </div>
  );
}

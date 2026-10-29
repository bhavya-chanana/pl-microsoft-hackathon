"use client";

import { useEffect, useId, useRef, useState } from "react";

import { cn } from "@/lib/utils";

import type { Side } from "./contract";
import { type FotmobDoc, regularShots } from "./fotmob-contract";

/** A goal or card on the match clock, drawn on both minute charts so they can be read against each other. */
export type Mark = { minute: number; kind: "goal" | "yellow" | "red"; side: Side; label: string };

export const CHART_M = { l: 40, r: 14, t: 12, b: 44 };

export function marksOf(doc: FotmobDoc | null | undefined): Mark[] {
  if (!doc) return [];
  const events = doc.events ?? [];
  const marks: Mark[] = [];
  if (events.length > 0) {
    for (const e of events) {
      if (!e.team) continue;
      const minute = e.minute + (e.added ?? 0) / 60;
      if (e.type === "goal" && !e.shootout) {
        const who = e.player?.name ?? "Goal";
        marks.push({ minute, kind: "goal", side: e.team, label: `${e.own_goal ? "Own goal" : e.penalty ? "Penalty goal" : "Goal"} ${Math.floor(e.minute)}′ ${who}` });
      } else if (e.type === "card" && (e.card === "yellow" || e.card === "red" || e.card === "second_yellow" || e.card === "yellow_red")) {
        marks.push({ minute, kind: e.card === "yellow" ? "yellow" : "red", side: e.team, label: `${e.card === "yellow" ? "Yellow" : e.card === "red" ? "Red" : "Second yellow"} card ${Math.floor(e.minute)}′ ${e.player?.name ?? ""}`.trim() });
      }
    }
    return marks;
  }
  // No event feed yet: the shot map still knows the goals.
  for (const s of regularShots(doc)) {
    if (s.outcome === "goal") marks.push({ minute: s.minute, kind: "goal", side: s.team, label: `Goal ${Math.floor(s.minute)}′ ${s.player ?? ""}`.trim() });
  }
  return marks;
}

/** The last minute any block of the match page reaches, so both minute charts share one axis. */
export function lastMinuteOf(doc: FotmobDoc | null | undefined): number {
  if (!doc) return 0;
  const vals = [...(doc.momentum ?? []).filter((m) => m.value != null).map((m) => m.minute), ...regularShots(doc).map((s) => s.minute + (s.added ?? 0) / 60), ...(doc.events ?? []).filter((e) => !e.shootout).map((e) => e.minute + (e.added ?? 0) / 60)];
  return Math.max(0, ...vals);
}

/** A finished match ends its minute axis at its last real minute; a live one steps by 15 so the axis does not jitter. */
export function axisMax(lastMinute: number, finished = false): number {
  return Math.max(90, finished ? Math.ceil(lastMinute) : Math.ceil(lastMinute / 15) * 15);
}

/** Goal and card glyphs on a minute chart: a faint line down the plot and a marker on top. */
export function MarkLayer({ marks, x, top, bottom }: { marks: Mark[]; x: (minute: number) => number; top: number; bottom: number }) {
  return (
    <g>
      {marks.map((m, i) => {
        const cx = x(m.minute);
        const tone = m.side === "home" ? "fill-home" : "fill-away";
        return (
          <g key={`${m.kind}-${i}`}>
            <line x1={cx} x2={cx} y1={top} y2={bottom} className="stroke-foreground" strokeOpacity={m.kind === "goal" ? 0.28 : 0.14} strokeDasharray="2 3" />
            {m.kind === "goal" ? (
              <circle cx={cx} cy={top} r={5.5} className={cn(tone, "stroke-foreground")} strokeWidth={1.5}>
                <title>{m.label}</title>
              </circle>
            ) : (
              <rect x={cx - 3.5} y={top - 5} width={7} height={10} rx={1.2} className={cn(m.kind === "yellow" ? "fill-[#e9b200]" : "fill-[#d6322b]", "stroke-background")} strokeWidth={1}>
                <title>{m.label}</title>
              </rect>
            )}
          </g>
        );
      })}
    </g>
  );
}

export function MarkLegend({ marks }: { marks: Mark[] }) {
  const has = (k: Mark["kind"]) => marks.some((m) => m.kind === k);
  if (marks.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground">
      {has("goal") && (
        <span className="inline-flex items-center gap-1.5">
          <svg aria-hidden width="12" height="12"><circle cx="6" cy="6" r="5" className="fill-foreground" /></svg>
          Goal
        </span>
      )}
      {has("yellow") && (
        <span className="inline-flex items-center gap-1.5">
          <svg aria-hidden width="10" height="12"><rect width="8" height="11" x="1" y="0.5" rx="1.2" className="fill-[#e9b200]" /></svg>
          Yellow card
        </span>
      )}
      {has("red") && (
        <span className="inline-flex items-center gap-1.5">
          <svg aria-hidden width="10" height="12"><rect width="8" height="11" x="1" y="0.5" rx="1.2" className="fill-[#d6322b]" /></svg>
          Red card
        </span>
      )}
    </span>
  );
}

const H = 150;

/**
 * FotMob's momentum, minute by minute: above the line the home side pushed, below it the away side. It shares its
 * minute axis, margins and width with the shot-chance chart under it, so the two line up.
 */
export function MomentumChart({ doc, marks, names, xMax }: { doc: FotmobDoc; marks: Mark[]; names: Record<Side, string>; xMax: number }) {
  const titleId = useId();
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(640);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(280, Math.round(el.getBoundingClientRect().width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const [hover, setHover] = useState<number | null>(null);
  const { l, r, t } = CHART_M;
  const IW = W - l - r;
  const IH = H - t - 8;
  const points = (doc.momentum ?? []).filter((p) => p.value != null) as Array<{ minute: number; value: number }>;
  const peak = Math.max(50, ...points.map((p) => Math.abs(p.value)));
  const yMax = Math.ceil(peak / 25) * 25;
  const x = (m: number) => l + (m / xMax) * IW;
  const mid = t + IH / 2;
  const bar = Math.max(1.5, (IW / xMax) * 0.78);
  const at = hover == null ? null : points.reduce<{ minute: number; value: number } | null>((best, p) => (!best || Math.abs(p.minute - hover) < Math.abs(best.minute - hover) ? p : best), null);
  const move = (clientX: number, rect: DOMRect) => {
    const px = ((clientX - rect.left) / rect.width) * W;
    setHover(Math.min(xMax, Math.max(0, ((px - l) / IW) * xMax)));
  };
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
        <span className="inline-flex items-center gap-2">
          <svg aria-hidden width="12" height="12"><rect width="12" height="12" rx="2" className="fill-home" /></svg>
          <span className="text-home-text">{names.home}</span>
          <span className="text-subtle">above the line</span>
        </span>
        <span className="inline-flex items-center gap-2">
          <svg aria-hidden width="12" height="12"><rect width="12" height="12" rx="2" className="fill-away" /></svg>
          <span className="text-away-text">{names.away}</span>
          <span className="text-subtle">below</span>
        </span>
        <MarkLegend marks={marks} />
      </div>
      <div ref={box}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-labelledby={titleId}
          className="block h-auto w-full touch-pan-y rounded-lg"
          onPointerMove={(e) => move(e.clientX, e.currentTarget.getBoundingClientRect())}
          onPointerLeave={() => setHover(null)}
        >
          <title id={titleId}>{`Momentum by minute: ${names.home} above the line, ${names.away} below`}</title>
          {Array.from({ length: Math.floor(xMax / 15) + 1 }, (_, i) => i * 15).map((m) => (
            <line key={m} x1={x(m)} x2={x(m)} y1={t} y2={t + IH} className="stroke-border" strokeWidth={1} strokeOpacity={0.6} />
          ))}
          <line x1={l} x2={W - r} y1={mid} y2={mid} className="stroke-border" strokeWidth={1.2} />
          <text x={l - 6} y={t + 9} textAnchor="end" className="fill-subtle" style={{ fontSize: 11 }}>{yMax}</text>
          <text x={l - 6} y={t + IH} textAnchor="end" className="fill-subtle" style={{ fontSize: 11 }}>{yMax}</text>
          {points.map((p) => {
            const h = (Math.abs(p.value) / yMax) * (IH / 2);
            return <rect key={p.minute} x={x(p.minute) - bar / 2} y={p.value >= 0 ? mid - h : mid} width={bar} height={Math.max(0.5, h)} rx={1} className={p.value >= 0 ? "fill-home" : "fill-away"} fillOpacity={at?.minute === p.minute ? 1 : 0.85} />;
          })}
          <MarkLayer marks={marks} x={x} top={t} bottom={t + IH} />
        </svg>
      </div>
      <p className="min-h-5 text-[13px] text-muted-foreground tnum" aria-live="polite">
        {at ? `${Math.floor(at.minute)}′  ${at.value === 0 ? "Even" : `${names[at.value > 0 ? "home" : "away"]} on top (${Math.abs(Math.round(at.value))})`}` : ""}
      </p>
    </div>
  );
}

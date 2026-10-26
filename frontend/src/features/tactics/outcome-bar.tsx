import type { OutcomeStats } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { PROB_METRICS, TONE_CLASS, labelOf, liftText, liftTone, metricValue, pct } from "./lib";

const t = copy.tactics.outcome;

type Interval = { value: number; low: number; high: number };

function intervalOf(stats: OutcomeStats, metric: (typeof PROB_METRICS)[number]): Interval {
  return stats[metric];
}

function Row({ metric, stats, baseline, scale }: { metric: (typeof PROB_METRICS)[number]; stats: OutcomeStats; baseline: OutcomeStats | null; scale: number }) {
  const iv = intervalOf(stats, metric);
  const base = baseline ? intervalOf(baseline, metric).value : null;
  const delta = base === null ? null : iv.value - base;
  const at = (v: number) => `${Math.min(100, (v / scale) * 100)}%`;
  return (
    <li className="grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5">
      <span className="text-sm">{labelOf(t.metrics, metric)}</span>
      <span aria-hidden className="relative block h-3">
        <span className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-muted" />
        <span className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-home" style={{ width: at(iv.value) }} />
        <span className="absolute top-1/2 h-3 -translate-y-1/2 rounded-sm bg-foreground/20" style={{ left: at(iv.low), width: `calc(${at(iv.high)} - ${at(iv.low)})` }} />
        {base !== null && <span className="absolute top-0 h-3 w-0.5 -translate-x-1/2 bg-foreground" style={{ left: at(base) }} title={t.baselineTick} />}
      </span>
      <span className="text-right text-sm tabular-nums">
        <span className="font-semibold">{pct(iv.value)}</span>
        {delta !== null && (
          <span className={cn("ml-2 text-xs font-medium", TONE_CLASS[liftTone(metric, delta)])}>{liftText(metric, delta)}</span>
        )}
      </span>
      <span className="sr-only">
        {t.range(pct(iv.low), pct(iv.high))}
        {base !== null && `, ${t.baselineTick} ${pct(base)}`}
      </span>
    </li>
  );
}

const TERMINAL_TONE: Record<string, string> = {
  goal: "bg-verified",
  shot: "bg-home",
  box_entry: "bg-data",
  retained: "bg-muted-foreground/50",
  turnover: "bg-blocked",
};
const TERMINAL_ORDER = ["goal", "shot", "box_entry", "retained", "turnover"];

/** How the rollouts ended, as one stacked bar with a legend (never colour alone). */
export function TerminalBar({ terminals }: { terminals: Record<string, number> }) {
  const total = Object.values(terminals).reduce((s, n) => s + n, 0);
  if (total === 0) return null;
  const keys = [...TERMINAL_ORDER.filter((k) => terminals[k]), ...Object.keys(terminals).filter((k) => !TERMINAL_ORDER.includes(k) && terminals[k])];
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium text-muted-foreground">{t.ended}</p>
      <div aria-hidden className="flex h-3 overflow-hidden rounded-full bg-muted">
        {keys.map((k) => (
          <span key={k} className={cn("h-full border-r border-card last:border-r-0", TERMINAL_TONE[k] ?? "bg-border")} style={{ width: `${(terminals[k] / total) * 100}%` }} />
        ))}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {keys.map((k) => (
          <li key={k} className="flex items-center gap-1.5">
            <span aria-hidden className={cn("size-2 rounded-full", TERMINAL_TONE[k] ?? "bg-border")} />
            <span>{labelOf(copy.tactics.stage.terminals, k)}</span>
            <span className="font-semibold tabular-nums">{pct(terminals[k] / total)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Shot, box entry, goal, turnover and xG for one idea, each with its range and the do-nothing marker. */
export function OutcomeBar({ stats, baseline, className }: { stats: OutcomeStats | null; baseline: OutcomeStats | null; className?: string }) {
  if (!stats) return <p className="text-sm text-muted-foreground">{t.empty}</p>;
  const highs = PROB_METRICS.flatMap((m) => [intervalOf(stats, m).high, baseline ? intervalOf(baseline, m).high : 0]);
  const scale = Math.max(0.5, Math.ceil(Math.max(...highs) * 10) / 10);
  const xgDelta = baseline ? stats.mean_xg - baseline.mean_xg : null;
  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex items-baseline gap-2" title={t.xgHelp}>
          <span className="font-display text-score font-semibold tabular-nums">{stats.mean_xg.toFixed(2)}</span>
          <span className="text-sm text-muted-foreground">{t.xg}</span>
          {xgDelta !== null && (
            <span className={cn("text-sm font-medium tabular-nums", TONE_CLASS[liftTone("mean_xg", xgDelta)])}>{liftText("mean_xg", xgDelta)}</span>
          )}
        </div>
        <p className="text-xs text-muted-foreground">{copy.tactics.outcome.timeToShot(stats.time_to_shot_s)}</p>
      </div>
      <ul className="flex flex-col gap-2.5">
        {PROB_METRICS.map((m) => (
          <Row key={m} metric={m} stats={stats} baseline={baseline} scale={scale} />
        ))}
      </ul>
      <TerminalBar terminals={stats.terminals} />
      <p className="text-xs text-muted-foreground">
        {t.help} {t.rollouts(stats.rollouts, stats.horizon_s)}
      </p>
    </div>
  );
}

/** Rows for the side-by-side view: every measure, both values, and who is ahead. */
export function metricRows(a: OutcomeStats, b: OutcomeStats) {
  return [...PROB_METRICS, "mean_xg" as const].map((metric) => {
    const va = metricValue(a, metric);
    const vb = metricValue(b, metric);
    return { metric, a: va, b: vb, delta: va - vb };
  });
}

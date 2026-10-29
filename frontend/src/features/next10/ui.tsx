"use client";

import { ArrowLeft, FlaskConical, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useId } from "react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { MOCK } from "./config";
import type { CalibStatus, LiveStats } from "./contract";
import { n10 } from "./copy";

/** Page frame for every Next-10s page: width, a way back, the title, and a mock-data flag in dev. */
export function PageFrame({ title, intro, back, actions, children }: { title: ReactNode; intro?: ReactNode; back?: { href: string; label: string }; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 xl:px-8">
      <header className="flex flex-col gap-3">
        <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-3 text-sm">
          <Link href={back?.href ?? routes.explore} className="inline-flex min-h-11 items-center gap-1.5 rounded-full text-muted-foreground transition-colors hover:text-foreground">
            <ArrowLeft aria-hidden className="size-4" />
            {back?.label ?? n10.nav.explore}
          </Link>
          {MOCK && (
            <span title={n10.mockHelp} className="inline-flex items-center gap-1 rounded-full bg-raised px-2.5 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border">
              <FlaskConical aria-hidden className="size-3" />
              {n10.mockBadge}
            </span>
          )}
        </nav>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{title}</h1>
            {intro && <p className="max-w-prose text-muted-foreground">{intro}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      </header>
      {children}
    </div>
  );
}

export function Panel({ title, help, actions, children, className }: { title?: ReactNode; help?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("flex min-w-0 flex-col gap-3 rounded-2xl border bg-card p-4 shadow-raised md:p-5", className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex flex-col gap-0.5">
            {title && <h2 className="text-lg font-semibold">{title}</h2>}
            {help && <p className="max-w-prose text-[13px] text-subtle">{help}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

/** A loading placeholder shaped like a list of cards. */
export function CardsSkeleton({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div aria-busy className={cn("grid gap-3 md:grid-cols-2 xl:grid-cols-3", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-28 rounded-2xl" />
      ))}
    </div>
  );
}

export function ErrorState({ title, children, onRetry }: { title: string; children?: ReactNode; onRetry?: () => void }) {
  return (
    <EmptyState title={title} className="border-destructive/40" action={onRetry && <Button variant="outline" size="lg" className="h-11 px-4" onClick={onRetry}>{n10.empty.retry}</Button>}>
      {children}
    </EmptyState>
  );
}

/** A probability as a labelled bar: the value is always printed, never colour alone. */
export function ProbBar({ label, value, std, tone = "bg-primary" }: { label: string; value: number | null | undefined; std?: number | null; tone?: string }) {
  const v = value ?? 0;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-display text-lg font-semibold tnum">
          {value === null || value === undefined ? "–" : `${Math.round(v * 100)}%`}
          {std ? <span className="ml-1 text-xs font-normal text-subtle">±{Math.round(std * 100)}</span> : null}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="presentation">
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${Math.min(100, Math.max(0, v * 100))}%` }} />
      </div>
    </div>
  );
}

export type LayerChip = { id: string; label: string; on: boolean; disabled?: boolean; reason?: string };

/** Layer switches as pressed-state buttons. A disabled layer says why, in text, below the row. */
export function LayerChips({ label, chips, onToggle }: { label: string; chips: LayerChip[]; onToggle: (id: string) => void }) {
  const uid = useId();
  const reasons = chips.filter((c) => (c.on || c.disabled) && c.reason);
  return (
    <div className="flex flex-col gap-2">
      <div role="group" aria-label={label} className="flex flex-wrap gap-2">
        {chips.map((c) => (
          <button
            key={c.id}
            type="button"
            aria-pressed={c.on}
            aria-describedby={c.reason && (c.on || c.disabled) ? `${uid}-${c.id}` : undefined}
            disabled={c.disabled}
            onClick={() => onToggle(c.id)}
            className={cn(
              "inline-flex h-11 items-center gap-2 rounded-full border px-4 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
              c.on ? "border-primary/50 bg-primary/15 text-foreground" : "bg-card text-muted-foreground hover:bg-raised hover:text-foreground",
            )}
          >
            <span aria-hidden className={cn("size-2 rounded-full", c.on ? "bg-primary" : "border border-muted-foreground")} />
            {c.label}
          </button>
        ))}
      </div>
      {reasons.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-[13px] text-subtle">
          {reasons.map((c) => (
            <li key={c.id} id={`${uid}-${c.id}`}>{c.reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Pitch key: observed against estimated players, ball, referee. */
export function PitchLegend({ text, zone }: { text: { observed: string; imputed: string; ball: string; ref: string }; zone?: string | null }) {
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-1.5 text-[13px] text-muted-foreground">
      {zone && (
        <li className="flex w-full items-center gap-2">
          <span aria-hidden className="h-2.5 w-14 shrink-0 rounded-full bg-gradient-to-r from-primary/10 to-primary/70" />
          {zone}
        </li>
      )}
      <li className="flex items-center gap-1.5">
        <svg aria-hidden width="14" height="14"><circle cx="7" cy="7" r="5.5" className="fill-home stroke-graphic" strokeWidth="1" /></svg>
        {text.observed}
      </li>
      <li className="flex items-center gap-1.5">
        <svg aria-hidden width="14" height="14"><circle cx="7" cy="7" r="5.5" className="fill-home stroke-home" fillOpacity="0.22" strokeWidth="1.5" strokeDasharray="3 2" /></svg>
        {text.imputed}
      </li>
      <li className="flex items-center gap-1.5">
        <svg aria-hidden width="14" height="14"><circle cx="7" cy="7" r="3.5" className="fill-graphic-foreground stroke-graphic" strokeWidth="1" /></svg>
        {text.ball}
      </li>
      <li className="flex items-center gap-1.5">
        <svg aria-hidden width="14" height="14"><rect x="3" y="3" width="8" height="8" rx="1.5" className="fill-graphic-warn" /></svg>
        {text.ref}
      </li>
    </ul>
  );
}

/** What the pitch mapping of the frame on screen means, in plain words. Nothing is said for an ordinary fitted frame. */
export function FrameNote({ status }: { status: CalibStatus | null | undefined }) {
  if (!status || status === "fitted" || status === "frame") return null;
  const low = status === "interpolated";
  return (
    <p role="status" className={cn("flex items-start gap-2 rounded-lg px-3 py-2 text-[13px]", low ? "bg-rewritten/15 text-foreground ring-1 ring-rewritten/40" : "bg-raised text-muted-foreground ring-1 ring-border")}>
      <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
      {n10.match.calib[status]}
    </p>
  );
}

/** Video pipeline health, from the live "stats" messages or the job's last stats. */
export function StatsChip({ stats }: { stats: LiveStats | null | undefined }) {
  if (!stats || (stats.live_ready == null && stats.calibrated_share == null)) return null;
  const T = n10.match.pipeline;
  const parts = [stats.live_ready == null ? null : stats.live_ready ? T.ready : T.notReady, stats.calibrated_share == null ? null : T.calibrated(stats.calibrated_share), stats.on_pitch_rate == null ? null : T.onPitch(stats.on_pitch_rate)].filter(Boolean);
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-full bg-raised px-3 py-1.5 text-[13px] ring-1 ring-border", stats.live_ready ? "text-foreground" : "text-muted-foreground")}>
      <span aria-hidden className={cn("size-2 shrink-0 rounded-full", stats.live_ready ? "bg-verified" : "bg-rewritten")} />
      {parts.join(" · ")}
    </span>
  );
}

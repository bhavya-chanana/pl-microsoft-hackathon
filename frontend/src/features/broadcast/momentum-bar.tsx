"use client";

import { useAnalystWording } from "@/features/coach/use-analyst";
import { useLive } from "@/features/live/live-store";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

/**
 * Diverging momentum gauge. The index runs from -100 (away on top) to +100
 * (home on top); the label names the team so colour is never the only cue.
 */
export function MomentumBar({ className, compact = false }: { className?: string; compact?: boolean }) {
  const meta = useLive((s) => s.meta);
  const index = useLive((s) => s.state?.momentum_index ?? 0);
  const analystWording = useAnalystWording();
  if (!meta) return null;
  const clamped = Math.max(-100, Math.min(100, index));
  const leader = Math.abs(clamped) < 10 ? null : clamped > 0 ? meta.home : meta.away;
  const summary = leader ? `${leader.short_name} on top` : "Even";
  const shown = analystWording ? `${summary} (${clamped > 0 ? "+" : ""}${Math.round(clamped)} on a -100 to +100 scale)` : summary;
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      {!compact && (
        <div className="flex items-baseline justify-between text-xs text-muted-foreground">
          <span>{meta.home.short_name}</span>
          <span className="font-medium text-foreground">
            {copy.status.momentum}: {shown}
          </span>
          <span>{meta.away.short_name}</span>
        </div>
      )}
      <div
        role="meter"
        aria-label={`${copy.status.momentum}: ${shown}`}
        aria-valuemin={-100}
        aria-valuemax={100}
        aria-valuenow={Math.round(clamped)}
        aria-valuetext={summary}
        className="relative h-2 overflow-hidden rounded-full bg-muted"
      >
        <span aria-hidden className="absolute inset-y-0 left-1/2 w-px bg-foreground/40" />
        <span
          aria-hidden
          className={cn(
            "absolute inset-y-0 origin-left transition-transform duration-500 ease-broadcast",
            clamped >= 0 ? "left-1/2 bg-home" : "right-1/2 origin-right bg-away",
          )}
          style={{ width: "50%", transform: `scaleX(${Math.abs(clamped) / 100})` }}
        />
      </div>
    </div>
  );
}

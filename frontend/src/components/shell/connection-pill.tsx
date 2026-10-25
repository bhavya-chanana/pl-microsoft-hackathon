"use client";

import { useLive } from "@/features/live/live-store";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const LABEL = {
  connecting: copy.connection.connecting,
  live: copy.connection.live,
  finished: copy.connection.finished,
  archived: copy.connection.archived,
  offline: copy.connection.offline,
  no_match: copy.connection.noMatch,
} as const;

/** On-air indicator. Tally red is reserved for this: it means the feed is live. */
export function ConnectionPill({ className }: { className?: string }) {
  const status = useLive((s) => s.status);
  const live = status === "live";
  return (
    <p
      role="status"
      aria-live="polite"
      className={cn(
        "flex h-7 items-center gap-2 rounded-sm px-2 font-display text-sm font-semibold tracking-wide uppercase",
        live ? "bg-tally text-tally-foreground" : "bg-muted text-muted-foreground",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn("size-2 rounded-full", live ? "animate-pulse bg-tally-foreground" : "bg-muted-foreground")}
      />
      <span className={cn(!live && "sr-only sm:not-sr-only")}>{LABEL[status]}</span>
    </p>
  );
}

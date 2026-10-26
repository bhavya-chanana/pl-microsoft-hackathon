"use client";

import { useLive } from "@/features/live/live-store";
import { useReconnect } from "@/features/live/live-stream-context";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

/**
 * The feed's state as one small pill. Never a wall: when the feed drops the page keeps its last
 * data and this turns amber with a Retry.
 */
export function ConnectionPill({ className }: { className?: string }) {
  const status = useLive((s) => s.status);
  const hasData = useLive((s) => s.meta !== null);
  const reconnect = useReconnect();
  const live = status === "live";
  const trouble = status === "offline" || (status === "connecting" && hasData);
  const label =
    status === "offline"
      ? hasData
        ? copy.connection.reconnecting
        : copy.connection.offline
      : status === "connecting"
        ? hasData
          ? copy.connection.reconnecting
          : copy.connection.connectingShort
        : status === "live"
          ? copy.connection.live
          : status === "finished"
            ? copy.connection.finishedShort
            : status === "archived"
              ? copy.connection.archived
              : copy.connection.noMatch;
  return (
    <p
      role="status"
      aria-live="polite"
      className={cn(
        "flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[13px] font-semibold tracking-wide whitespace-nowrap",
        live && "bg-live/15 text-live",
        trouble && "bg-warning/15 text-warning",
        !live && !trouble && "bg-muted text-muted-foreground",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-2 rounded-full",
          live && "animate-pulse bg-live",
          trouble && "animate-pulse bg-warning",
          !live && !trouble && "bg-subtle",
        )}
      />
      {label}
      {status === "offline" && (
        <button type="button" onClick={reconnect} className="ml-1 rounded-sm underline underline-offset-2 hover:no-underline">
          {copy.connection.retry}
        </button>
      )}
    </p>
  );
}

"use client";

import { SlidersHorizontal } from "lucide-react";

import { useLive } from "@/features/live/live-store";
import { useReconnect } from "@/features/live/live-stream-context";
import { useSourceLabel } from "@/features/stream/use-replay";
import { copy } from "@/lib/copy";

import { useShell } from "./shell-store";
import { cn } from "@/lib/utils";

/**
 * The feed's state as one small pill. Never a wall: when the feed drops the page keeps its last
 * data and this turns amber with a Retry.
 */
export function ConnectionPill({ className }: { className?: string }) {
  const status = useLive((s) => s.status);
  const hasData = useLive((s) => s.meta !== null);
  const reconnect = useReconnect();
  const source = useSourceLabel();
  const openStream = useShell((s) => s.setStream);
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
          ? (source.pill ?? copy.connection.live)
          : status === "finished"
            ? copy.connection.finishedShort
            : status === "archived"
              ? copy.connection.archived
              : copy.connection.noMatch;
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex h-7 items-center rounded-full text-[13px] font-semibold tracking-wide whitespace-nowrap",
        live && "bg-live/15 text-live",
        trouble && "bg-warning/15 text-warning",
        !live && !trouble && "bg-muted text-muted-foreground",
        className,
      )}
    >
      <button
        type="button"
        onClick={() => openStream(true)}
        aria-label={`${copy.stream.open}: ${label}`}
        className="relative flex h-7 items-center gap-1.5 rounded-full px-2.5 outline-none before:absolute before:-inset-2 before:content-[''] focus-visible:ring-2 focus-visible:ring-ring"
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
        <SlidersHorizontal aria-hidden className="size-3 opacity-70" />
      </button>
      {status === "offline" && (
        <button type="button" onClick={reconnect} className="mr-2.5 rounded-sm underline underline-offset-2 hover:no-underline">
          {copy.connection.retry}
        </button>
      )}
    </div>
  );
}

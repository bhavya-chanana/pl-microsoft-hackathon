"use client";

import { RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { useReconnect } from "@/features/live/live-stream-context";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { MomentumBar } from "./momentum-bar";
import { OverlayLayer } from "./overlay-layer";
import { Pitch } from "./pitch";
import { ScoreBug } from "./score-bug";

function FeedNotice() {
  const status = useLive((s) => s.status);
  const hasMatch = useLive((s) => s.meta !== null);
  const reconnect = useReconnect();
  if (status === "connecting" && !hasMatch) {
    return (
      <div className="absolute inset-0 grid place-items-center">
        <Skeleton className="absolute inset-[6%] rounded-sm bg-graphic-foreground/5" />
        <p className="relative text-sm text-graphic-foreground/80">{copy.connection.connecting}</p>
      </div>
    );
  }
  if (status !== "offline" && status !== "no_match") return null;
  const offline = status === "offline";
  return (
    <div className="absolute inset-0 grid place-items-center bg-graphic/80 p-6 text-center text-graphic-foreground">
      <div className="flex max-w-sm flex-col items-center gap-3">
        <p className="font-display text-display font-semibold">
          {offline ? copy.connection.offline : copy.connection.noMatch}
        </p>
        <p className="text-sm text-graphic-foreground/80">
          {offline ? copy.connection.offlineHelp : copy.connection.noMatchHelp}
        </p>
        {offline && (
          <Button variant="secondary" size="lg" className="min-h-11 px-4" onClick={reconnect}>
            <RotateCw aria-hidden />
            {copy.connection.retry}
          </Button>
        )}
      </div>
    </div>
  );
}

/**
 * The programme monitor: live pitch with the score bug and timed graphics, at
 * 16:9 like the real output. `fill` lets the /broadcast route use the whole screen.
 */
export function BroadcastMonitor({ className, fill = false }: { className?: string; fill?: boolean }) {
  return (
    <section
      aria-label={copy.nav.broadcast}
      className={cn(
        "@container relative isolate overflow-hidden bg-graphic text-graphic-foreground",
        fill ? "h-dvh w-full" : "aspect-video w-full rounded-lg shadow-floating ring-1 ring-border",
        className,
      )}
    >
      <div className="absolute inset-0 grid place-items-center p-[3%] pt-[12%] @2xl:pt-[9%]">
        <Pitch />
      </div>
      <div className="absolute top-[3%] left-[3%] flex flex-col gap-1.5">
        <ScoreBug />
        <MomentumBar compact className="hidden w-full max-w-56 @2xl:flex" />
      </div>
      <OverlayLayer />
      <FeedNotice />
    </section>
  );
}

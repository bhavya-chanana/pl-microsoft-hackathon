"use client";

import { useEffect, useState } from "react";

import { History, MessageCircleQuestion, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useShell } from "@/components/shell/shell-store";
import { useLive } from "@/features/live/live-store";
import { useRestart } from "@/features/stream/use-replay";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

/** True while any dialog or sheet is open (their popups are portalled to the body). */
function useOverlayOpen(): boolean {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const check = () => setOpen(Boolean(document.querySelector('[data-slot="dialog-content"], [data-slot="sheet-content"]')));
    const mo = new MutationObserver(check);
    mo.observe(document.body, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, []);
  return open;
}

/** Catch me up and Ask the pundit as plain buttons; the Live page places them in its hero row. */
export function ActionButtons({ size = "md", className }: { size?: "md" | "sm" | "bar"; className?: string }) {
  const hasMatch = useLive((s) => s.meta !== null);
  const fullTime = useLive((s) => s.status === "finished" || s.status === "archived" || s.state?.status === "full_time");
  const restart = useRestart();
  const h = size === "sm" ? "h-8 text-[13px]" : size === "bar" ? "h-10 flex-1 text-sm" : "h-10 text-sm";
  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {fullTime && hasMatch && (
        <Button
          variant="secondary"
          className={cn("gap-2 rounded-full px-4", h, size === "bar" && "order-first w-full flex-none")}
          disabled={restart.isPending}
          onClick={() => restart.mutate({ startMinute: 0 })}
        >
          <RotateCcw aria-hidden className={cn(restart.isPending && "motion-safe:animate-spin")} />
          {restart.isPending ? copy.replay.restarting : copy.replay.restart}
        </Button>
      )}
      <Button className={cn("gap-2 rounded-full px-4", h)} disabled={!hasMatch} onClick={() => useShell.getState().setCatchup(true)}>
        <History aria-hidden />
        {copy.catchup.cta}
      </Button>
      <Button variant="ghost" className={cn("gap-2 rounded-full px-4 hover:bg-foreground/10", h)} disabled={!hasMatch} onClick={() => useShell.getState().setPundit(true)}>
        <MessageCircleQuestion aria-hidden />
        {copy.pundit.open}
      </Button>
    </div>
  );
}

/** True while the hero actions row (id="live-actions") is on screen. */
function useHeroVisible(): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const el = document.getElementById("live-actions");
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { rootMargin: "-110px 0px 0px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return visible;
}

/**
 * Desktop: a compact version beside the match header once the hero row has scrolled away.
 * Mobile actions stay in the page flow so they never cover match content.
 */
export function FloatingActions() {
  const hidden = useOverlayOpen();
  const heroVisible = useHeroVisible();
  return (
    <>
      <div
        className={cn(
          "glass-strong fixed top-[calc(var(--topbar-h)+8px)] right-6 z-40 hidden rounded-full p-1 transition-opacity lg:block",
          (heroVisible || hidden) && "invisible opacity-0",
        )}
      >
        <ActionButtons size="sm" />
      </div>
    </>
  );
}

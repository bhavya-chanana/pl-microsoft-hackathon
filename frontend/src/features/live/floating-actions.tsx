"use client";

import { useEffect, useState } from "react";

import { History, MessageCircleQuestion } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useShell } from "@/components/shell/shell-store";
import { useLive } from "@/features/live/live-store";
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
  const h = size === "sm" ? "h-8 text-[13px]" : size === "bar" ? "h-10 flex-1 text-sm" : "h-10 text-sm";
  return (
    <div className={cn("flex items-center gap-1", className)}>
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
 * Never floats over content. Mobile: a 48px bar docked 12px above the tab bar (the page reserves its
 * height). Desktop: a compact version beside the match header once the hero row has scrolled away.
 */
export function FloatingActions() {
  const hidden = useOverlayOpen();
  const heroVisible = useHeroVisible();
  return (
    <>
      <div
        className={cn(
          "glass-strong fixed inset-x-3 bottom-[calc(12px+64px+12px+env(safe-area-inset-bottom))] z-40 flex h-12 items-center rounded-full p-1 md:hidden",
          hidden && "invisible opacity-0",
        )}
      >
        <ActionButtons size="bar" className="w-full" />
      </div>
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

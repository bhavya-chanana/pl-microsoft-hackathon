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

/** The two things a fan reaches for mid-match, floating over the page. Above the tab bar on mobile. */
export function FloatingActions() {
  const hidden = useOverlayOpen();
  const hasMatch = useLive((s) => s.meta !== null);
  return (
    <div
      className={cn(
        "pointer-events-none fixed inset-x-0 bottom-[calc(64px+12px+16px+env(safe-area-inset-bottom))] z-40 flex justify-center px-4 transition-opacity md:bottom-6",
        hidden && "invisible opacity-0",
      )}
    >
      <div className="glass-strong pointer-events-auto flex items-center gap-1.5 rounded-full p-1.5">
        <Button className="h-11 gap-2 rounded-full px-5 text-sm" disabled={!hasMatch} onClick={() => useShell.getState().setCatchup(true)}>
          <History aria-hidden />
          {copy.catchup.cta}
        </Button>
        <Button variant="ghost" className="h-11 gap-2 rounded-full px-4 text-sm hover:bg-foreground/10" disabled={!hasMatch} onClick={() => useShell.getState().setPundit(true)}>
          <MessageCircleQuestion aria-hidden />
          {copy.pundit.open}
        </Button>
      </div>
    </div>
  );
}

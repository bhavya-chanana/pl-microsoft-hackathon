"use client";

import { CatchupDialog } from "@/features/companion/catchup-dialog";
import { PunditPanel } from "@/features/pundit/pundit-panel";

/** The two things a fan reaches for mid-match, floating over the page. Above the tab bar on mobile. */
export function FloatingActions() {
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(64px+12px+12px+env(safe-area-inset-bottom))] z-40 flex justify-center px-4 md:bottom-6">
      <div className="glass-strong pointer-events-auto flex items-center gap-1.5 rounded-full p-1.5">
        <CatchupDialog className="h-11 w-auto rounded-full px-5 text-sm" />
        <PunditPanel className="h-11 w-auto rounded-full border-0 bg-transparent px-4 text-sm hover:bg-foreground/10" />
      </div>
    </div>
  );
}

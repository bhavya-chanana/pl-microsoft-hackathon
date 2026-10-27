"use client";

import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { useShell } from "./shell-store";

/** Demo controls, replays and the agent room: out of the fan's way, one tap from the profile menu. */
export function DevDrawer() {
  const open = useShell((s) => s.devOpen);
  const setOpen = useShell((s) => s.setDev);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent side="right" className="w-full overflow-y-auto overscroll-contain p-4 sm:max-w-[520px]">
        <SheetHeader className="p-0">
          <SheetTitle className="font-display text-xl">{copy.nav.dev}</SheetTitle>
          <SheetDescription>{copy.dev.help}</SheetDescription>
        </SheetHeader>
        <div className="mt-4 flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">{copy.dev.agentsHelp}</p>
          <Link href={routes.agents} onClick={() => setOpen(false)} className={cn(buttonVariants({ variant: "secondary" }), "h-11 self-start px-4")}>
            {copy.dev.openAgents}
          </Link>
        </div>
      </SheetContent>
    </Sheet>
  );
}

"use client";

import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PastMatches } from "@/features/replay/past-matches";
import { ReplayControls } from "@/features/replay/replay-controls";
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
        <Tabs defaultValue="demo" className="gap-4">
          <TabsList className="h-11 w-full">
            <TabsTrigger value="demo" className="h-9 flex-1">{copy.dev.demo}</TabsTrigger>
            <TabsTrigger value="matches" className="h-9 flex-1">{copy.dev.matches}</TabsTrigger>
            <TabsTrigger value="agents" className="h-9 flex-1">{copy.dev.agents}</TabsTrigger>
          </TabsList>
          <TabsContent value="demo">
            <ReplayControls />
          </TabsContent>
          <TabsContent value="matches">
            <PastMatches />
          </TabsContent>
          <TabsContent value="agents" className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">{copy.dev.agentsHelp}</p>
            <Link href={routes.agents} onClick={() => setOpen(false)} className={cn(buttonVariants({ variant: "secondary" }), "h-11 self-start px-4")}>
              {copy.dev.openAgents}
            </Link>
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}

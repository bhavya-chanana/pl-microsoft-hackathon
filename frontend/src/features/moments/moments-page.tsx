"use client";

import { useState } from "react";

import { Segmented } from "@/components/ui/segmented";
import { StablePanel } from "@/components/ui/stable-panel";
import { InsightFeed } from "@/features/companion/insight-feed";
import { MatchTimeline } from "@/features/companion/match-timeline";
import { copy } from "@/lib/copy";

import { CatchupDialog, RecapDialog } from "@/features/companion/catchup-dialog";

type View = "highlights" | "timeline";

/** Moments: what happened, newest first, with the full event timeline one tap away. */
export function MomentsPage() {
  const [view, setView] = useState<View>("highlights");
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6 md:px-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{copy.momentsPage.heading}</h1>
        <p className="text-muted-foreground">{copy.momentsPage.intro}</p>
      </header>
      <div className="grid gap-3 sm:grid-cols-2">
        <CatchupDialog />
        <RecapDialog />
      </div>
      <div className="sticky top-[var(--stack-h)] z-30 -mx-4 bg-background px-4 py-2 md:-mx-6 md:px-6">
        <Segmented
        label={copy.momentsPage.heading}
        value={view}
        onChange={setView}
        options={[
          { value: "highlights", label: copy.feed.heading },
          { value: "timeline", label: copy.tabs.timeline },
        ]}
      />
      </div>
      <StablePanel panelKey={view}>
        {view === "highlights" ? (
          <InsightFeed />
        ) : (
          <div className="rounded-2xl border bg-card px-4">
            <MatchTimeline />
          </div>
        )}
      </StablePanel>
    </div>
  );
}

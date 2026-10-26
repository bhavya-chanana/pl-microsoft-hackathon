"use client";

import { useState } from "react";

import { Segmented } from "@/components/ui/segmented";
import { StablePanel } from "@/components/ui/stable-panel";
import { PlayerRatings } from "@/features/fans/player-ratings";
import { copy } from "@/lib/copy";

import { ShotMapPanel } from "./shot-map";
import { PlayerCard, TeamComparison } from "./team-stats";

type View = "team" | "players" | "shots";

/** Stats: team comparison, player ratings and shots, one segment at a time. */
export function StatsPage() {
  const [view, setView] = useState<View>("team");
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-4 py-6 md:px-6">
      <header>
        <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{copy.statsPage.heading}</h1>
      </header>
      <Segmented
        label={copy.statsPage.heading}
        value={view}
        onChange={setView}
        options={[
          { value: "team", label: copy.statsPage.team },
          { value: "players", label: copy.statsPage.players },
          { value: "shots", label: copy.statsPage.shots },
        ]}
      />
      <StablePanel panelKey={view}>
        {view === "team" && (
          <div className="flex flex-col gap-6">
            <PlayerCard />
            <TeamComparison />
          </div>
        )}
        {view === "players" && <PlayerRatings />}
        {view === "shots" && <ShotMapPanel />}
      </StablePanel>
    </div>
  );
}

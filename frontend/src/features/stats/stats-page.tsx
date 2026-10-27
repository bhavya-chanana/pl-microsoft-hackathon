"use client";

import { useAnalystWording } from "@/features/coach/use-analyst";
import { useState } from "react";

import { Segmented } from "@/components/ui/segmented";
import { StickyBar } from "@/components/ui/sticky-bar";
import { StablePanel } from "@/components/ui/stable-panel";
import { copy } from "@/lib/copy";

import { XgTimeline } from "@/features/report/match-report";
import { useLive } from "@/features/live/live-store";

import { PlayersTable, type Team } from "./players-table";
import { ShotMapPanel } from "./shot-map";
import { PlayerCard, TeamComparison } from "./team-stats";

type View = "team" | "players" | "shots";

/** Chances created over the match, as a race between the two sides. */
function XgRace() {
  const analyst = useAnalystWording();
  const meta = useLive((s) => s.meta);
  const timeline = useLive((s) => s.timeline);
  if (!meta) return null;
  return (
    <section aria-labelledby="xg-race" className="flex flex-col gap-3 rounded-2xl border bg-card p-4 shadow-raised sm:p-5 min-[1100px]:sticky min-[1100px]:top-[calc(var(--stack-h)+4.5rem)]">
      <h2 id="xg-race" className="font-display text-xl font-semibold">
        {analyst ? copy.report.xgTimelineAnalyst : copy.report.xgTimeline}
      </h2>
      <XgTimeline meta={meta} timeline={timeline} />
    </section>
  );
}

/** Stats: team comparison, player ratings and shots, one segment at a time. */
export function StatsPage() {
  const [view, setView] = useState<View>("team");
  const [team, setTeam] = useState<Team>("all");
  const meta = useLive((s) => s.meta);
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 py-6 md:px-6 xl:px-8">
      <header>
        <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{copy.statsPage.heading}</h1>
      </header>
      <StickyBar className="flex flex-wrap items-center justify-between gap-3">
        <Segmented
          label={copy.statsPage.heading}
          value={view}
          onChange={setView}
          className="min-w-0 w-full max-w-sm sm:w-auto"
          options={[
            { value: "team", label: copy.statsPage.team },
            { value: "players", label: copy.statsPage.players },
            { value: "shots", label: copy.statsPage.shots },
          ]}
        />
        {view === "players" && meta && (
          <Segmented<Team>
            label={copy.statsPage.players}
            value={team}
            onChange={setTeam}
            className="min-w-0"
            options={[
              { value: "all", label: copy.momentsPage.kinds.all },
              { value: "home", label: <><span aria-hidden className="size-2.5 rounded-full bg-home" />{meta.home.short_name}</> },
              { value: "away", label: <><span aria-hidden className="size-2.5 rounded-full bg-away" />{meta.away.short_name}</> },
            ]}
          />
        )}
      </StickyBar>
      <StablePanel panelKey={view}>
        {view === "team" && (
          <div className="grid items-start gap-6 min-[1100px]:grid-cols-2 [&>*]:min-w-0">
            <div className="flex flex-col gap-6">
              <PlayerCard />
              <TeamComparison />
            </div>
            <XgRace />
          </div>
        )}
        {view === "players" && <PlayersTable team={team} />}
        {view === "shots" && <ShotMapPanel />}
      </StablePanel>
    </div>
  );
}

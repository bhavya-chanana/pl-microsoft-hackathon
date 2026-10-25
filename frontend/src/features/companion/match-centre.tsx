"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BroadcastMonitor } from "@/features/broadcast/broadcast-monitor";
import { FanPolls } from "@/features/fans/fan-polls";
import { PlayerRatings } from "@/features/fans/player-ratings";
import { MomentumBar } from "@/features/broadcast/momentum-bar";
import { useLive } from "@/features/live/live-store";
import { ProfilePanel } from "@/features/profile/profile-panel";
import { useProfile } from "@/features/profile/profile-store";
import { PastMatches } from "@/features/replay/past-matches";
import { ReplayControls } from "@/features/replay/replay-controls";
import { MetricTile } from "@/features/stats/metric-tile";
import { ShotMapPanel } from "@/features/stats/shot-map";
import { PlayerCard, TeamComparison } from "@/features/stats/team-stats";
import { useMediaQuery } from "@/hooks/use-media-query";
import { copy } from "@/lib/copy";

import { CatchupDialog, RecapDialog } from "./catchup-dialog";
import { InsightFeed } from "./insight-feed";
import { MatchTimeline } from "./match-timeline";
import { ListenToggle } from "./narration";

function MatchHeading() {
  const meta = useLive((s) => s.meta);
  return (
    <div className="flex flex-col gap-0.5">
      <h1 className="font-display text-display font-semibold">
        {meta ? `${meta.home.name} v ${meta.away.name}` : copy.nav.matchCentre}
      </h1>
      {meta && (
        <p className="text-sm text-muted-foreground">
          {meta.competition}, {meta.venue}
        </p>
      )}
    </div>
  );
}

function StatsPanel() {
  return (
    <div className="flex flex-col gap-6">
      <PlayerCard />
      <TeamComparison />
    </div>
  );
}

const trigger = "h-11 flex-1 px-3 text-sm";

export function MatchCentre() {
  const wide = useMediaQuery("(min-width: 1024px)");
  const analyst = useProfile((s) => s.mode === "analyst");
  return (
    <main className="mx-auto grid max-w-screen-2xl gap-6 px-4 py-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_24rem] lg:py-6">
      <div className="flex min-w-0 flex-col gap-4">
        <MatchHeading />
        <BroadcastMonitor interactive />
        <MomentumBar />
        <MetricTile />
        <FanPolls />
        {analyst && <ShotMapPanel />}
        {wide && (
          <Tabs defaultValue="timeline" className="gap-4">
            <TabsList className="h-11 w-full max-w-md">
              <TabsTrigger value="timeline" className={trigger}>
                {copy.tabs.timeline}
              </TabsTrigger>
              <TabsTrigger value="stats" className={trigger}>
                {copy.tabs.stats}
              </TabsTrigger>
              <TabsTrigger value="ratings" className={trigger}>
                {copy.tabs.ratings}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="timeline">
              <div tabIndex={0} aria-label={copy.timeline.heading} className="max-h-96 overflow-y-auto rounded-lg border bg-card px-4">
                <MatchTimeline />
              </div>
            </TabsContent>
            <TabsContent value="stats">
              <StatsPanel />
            </TabsContent>
            <TabsContent value="ratings">
              <PlayerRatings />
            </TabsContent>
          </Tabs>
        )}
        {wide && <ReplayControls className="max-w-xl" />}
        {wide && <PastMatches className="max-w-xl" />}
      </div>
      <aside
        aria-label={copy.feed.heading}
        className="flex min-w-0 flex-col gap-4 lg:sticky lg:top-20 lg:h-[calc(100dvh-6rem)]"
      >
        <div className="flex flex-col gap-4 rounded-lg border bg-card p-4 shadow-raised">
          <CatchupDialog />
          <RecapDialog />
          <ListenToggle />
        </div>
        <ProfilePanel className="rounded-lg border bg-card p-4" />
        {wide ? (
          <section aria-labelledby="feed-heading" className="flex min-h-0 flex-1 flex-col gap-3">
            <h2 id="feed-heading" className="font-display text-lg font-semibold">
              {copy.feed.heading}
            </h2>
            <div tabIndex={0} aria-labelledby="feed-heading" className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1 pb-4">
              <InsightFeed />
            </div>
          </section>
        ) : (
          <Tabs defaultValue="feed" className="gap-4">
            <TabsList className="h-11 w-full">
              <TabsTrigger value="feed" className={trigger}>
                {copy.tabs.feed}
              </TabsTrigger>
              <TabsTrigger value="timeline" className={trigger}>
                {copy.tabs.timeline}
              </TabsTrigger>
              <TabsTrigger value="stats" className={trigger}>
                {copy.tabs.stats}
              </TabsTrigger>
              <TabsTrigger value="ratings" className={trigger}>
                {copy.tabs.ratings}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="feed">
              <InsightFeed />
            </TabsContent>
            <TabsContent value="timeline">
              <MatchTimeline />
            </TabsContent>
            <TabsContent value="stats">
              <StatsPanel />
            </TabsContent>
            <TabsContent value="ratings">
              <PlayerRatings />
            </TabsContent>
          </Tabs>
        )}
        {!wide && <ReplayControls />}
        {!wide && <PastMatches />}
      </aside>
    </main>
  );
}

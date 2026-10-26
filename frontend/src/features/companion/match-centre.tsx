"use client";

import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { BroadcastMonitor } from "@/features/broadcast/broadcast-monitor";
import { MomentumBar } from "@/features/broadcast/momentum-bar";
import { FanPolls } from "@/features/fans/fan-polls";
import { useProfile } from "@/features/profile/profile-store";
import { PunditPanel } from "@/features/pundit/pundit-panel";
import { MetricTile } from "@/features/stats/metric-tile";
import { ShotMapPanel } from "@/features/stats/shot-map";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { CatchupDialog, RecapDialog } from "./catchup-dialog";
import { InsightFeed } from "./insight-feed";
import { MatchPulse } from "./match-pulse";
import { ListenToggle } from "./narration";

/** The Live page: the pitch first, then flow, then what just happened. One document scroll. */
export function MatchCentre() {
  const analyst = useProfile((s) => s.mode === "analyst");
  return (
    <div className="mx-auto grid max-w-7xl items-start gap-6 px-4 py-6 md:px-6 lg:grid-cols-12 xl:px-8">
      <div className="flex min-w-0 flex-col gap-4 lg:col-span-8">
        <h1 className="sr-only">{copy.nav.live}</h1>
        <BroadcastMonitor interactive />
        <MomentumBar />
        <MatchPulse />
        <MetricTile />
        {analyst && <ShotMapPanel />}
      </div>
      <div className="flex min-w-0 flex-col gap-4 lg:col-span-4">
        <div className="flex flex-col gap-4 rounded-2xl border bg-card p-4 shadow-raised">
          <CatchupDialog />
          <PunditPanel />
          <RecapDialog />
          <ListenToggle />
        </div>
        <FanPolls />
        <section aria-labelledby="feed-heading" className="flex flex-col gap-3">
          <h2 id="feed-heading" className="text-xl font-semibold">
            {copy.feed.heading}
          </h2>
          <InsightFeed limit={4} />
          <Link href={routes.moments} className={cn(buttonVariants({ variant: "secondary" }), "h-11 self-start px-4")}>
            {copy.feed.seeAll}
          </Link>
        </section>
      </div>
    </div>
  );
}

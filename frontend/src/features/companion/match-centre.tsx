"use client";

import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { MomentumBar } from "@/features/broadcast/momentum-bar";
import { FanPolls } from "@/features/fans/fan-polls";
import { FloatingActions } from "@/features/live/floating-actions";
import { LivePitch } from "@/features/live/live-pitch";
import { StoryLine } from "@/features/live/story-line";
import { useProfile } from "@/features/profile/profile-store";
import { MetricTile } from "@/features/stats/metric-tile";
import { ShotMapPanel } from "@/features/stats/shot-map";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { InsightFeed } from "./insight-feed";
import { MatchPulse } from "./match-pulse";
import { ListenToggle } from "./narration";

/**
 * The Live page: story, pitch, flow, then what just happened. A document-scroll grid (8/4) with no
 * inner scrolling; on mobile the columns dissolve into one stack in a fixed order.
 */
export function MatchCentre() {
  const analyst = useProfile((s) => s.mode === "analyst");
  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 pt-4 pb-28 md:px-6 md:pt-6 xl:px-8">
      <h1 className="sr-only">{copy.nav.live}</h1>
      <StoryLine />
      <div className="grid items-start gap-4 lg:grid-cols-12 lg:gap-6">
        <div className="contents lg:col-span-8 lg:flex lg:flex-col lg:gap-4">
          <div className="order-1 min-w-0 lg:order-none">
            <LivePitch />
          </div>
          <div className="order-2 min-w-0 lg:order-none">
            <MomentumBar />
          </div>
          <div className="order-4 min-w-0 lg:order-none">
            <MatchPulse />
          </div>
          <div className="order-5 min-w-0 empty:hidden lg:order-none">
            <MetricTile />
          </div>
          {analyst && (
            <div className="order-5 min-w-0 lg:order-none">
              <ShotMapPanel />
            </div>
          )}
        </div>
        <div className="contents lg:col-span-4 lg:flex lg:flex-col lg:gap-4">
          <section aria-labelledby="feed-heading" className="order-3 flex min-w-0 flex-col gap-3 lg:order-none">
            <h2 id="feed-heading" className="text-xl font-semibold">
              {copy.feed.heading}
            </h2>
            <InsightFeed limit={4} carousel />
            <Link href={routes.moments} className={cn(buttonVariants({ variant: "secondary" }), "h-11 self-start px-4")}>
              {copy.feed.seeAll}
            </Link>
          </section>
          <div className="order-6 min-w-0 lg:order-none">
            <FanPolls />
          </div>
          <div className="order-7 min-w-0 rounded-2xl border bg-card p-4 lg:order-none">
            <ListenToggle />
          </div>
        </div>
      </div>
      <FloatingActions />
    </div>
  );
}

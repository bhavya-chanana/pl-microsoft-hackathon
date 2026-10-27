"use client";

import Link from "next/link";
import { MonitorPlay } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { MomentumBar } from "@/features/broadcast/momentum-bar";
import { FanPolls } from "@/features/fans/fan-polls";
import { ActionButtons, FloatingActions } from "@/features/live/floating-actions";
import { ScorersStrip, TopStats } from "@/features/live/live-extras";
import { LivePitch } from "@/features/live/live-pitch";
import { StoryLine } from "@/features/live/story-line";
import { useCoach } from "@/features/coach/coach-store";
import { useProfile } from "@/features/profile/profile-store";
import { MetricTile } from "@/features/stats/metric-tile";
import { ShotMapPanel } from "@/features/stats/shot-map";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { InsightFeed } from "./insight-feed";
import { MatchPulse } from "./match-pulse";

/**
 * The Live page: story, pitch, flow, then what just happened. A document-scroll grid (8/4) with no
 * inner scrolling; on mobile the columns dissolve into one stack in a fixed order.
 */
export function MatchCentre() {
  const analyst = useProfile((s) => s.mode === "analyst");
  const coachMode = useCoach((s) => s.coachMode);
  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 pt-4 pb-8 md:pb-12 md:px-6 md:pt-6 xl:px-8">
      <h1 className="sr-only">{copy.nav.live}</h1>
      <div id="live-actions" className="flex items-center justify-between gap-4">
        <div className="min-w-0 flex-1">
          <StoryLine />
        </div>
        <ActionButtons className="hidden shrink-0 md:flex" />
      </div>
      <ActionButtons size="bar" className="w-full md:hidden" />
      <ScorersStrip />
      <div className="grid items-start gap-4 min-[1100px]:grid-cols-12 min-[1100px]:gap-6">
        <div className="contents min-[1100px]:col-span-8 min-[1100px]:flex min-[1100px]:flex-col min-[1100px]:gap-4">
          <div className="order-1 min-w-0 min-[1100px]:order-none">
            <LivePitch />
            <div className="mt-2 flex justify-end">
              <Link href={routes.broadcast} className={cn(buttonVariants({ variant: "outline" }), "h-11 gap-2 px-4")}><MonitorPlay aria-hidden />{copy.nav.broadcast}</Link>
            </div>
          </div>
          <div className="order-2 min-w-0 min-[1100px]:order-none">
            <MomentumBar />
          </div>
          <div className="order-2 min-w-0 min-[1100px]:order-none">
            <TopStats />
          </div>
          <div className="order-4 min-w-0 min-[1100px]:order-none">
            <MatchPulse />
          </div>
          <div className="order-5 min-w-0 empty:hidden min-[1100px]:order-none">
            <MetricTile />
          </div>
          <div className="order-6 min-w-0 min-[1100px]:order-none">
            {!coachMode && <FanPolls />}
          </div>
          {analyst && (
            <div className="order-5 min-w-0 min-[1100px]:order-none">
              <ShotMapPanel />
            </div>
          )}
        </div>
        <div className="contents min-[1100px]:col-span-4 min-[1100px]:flex min-[1100px]:flex-col min-[1100px]:gap-4">
          <section aria-labelledby="feed-heading" className="order-3 flex min-w-0 flex-col gap-3 min-[1100px]:order-none">
            <h2 id="feed-heading" className="text-xl font-semibold">
              {copy.feed.heading}
            </h2>
            <InsightFeed limit={3} highlights />
            <Link href={routes.moments} className={cn(buttonVariants({ variant: "secondary" }), "h-11 self-start px-4")}>
              {copy.feed.seeAll}
            </Link>
          </section>
        </div>
      </div>
      <FloatingActions />
    </div>
  );
}

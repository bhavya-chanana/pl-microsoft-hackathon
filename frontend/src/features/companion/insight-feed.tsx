"use client";

import { RotateCw } from "lucide-react";
import { AnimatePresence } from "motion/react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useFeed } from "@/features/live/queries";
import { useFanProfile } from "@/features/profile/profile-store";
import { copy } from "@/lib/copy";

import { InsightCard } from "./insight-card";

export function FeedSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex flex-col gap-2 rounded-lg border bg-card p-4">
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-6 w-4/5" />
          <Skeleton className="h-4 w-full" />
        </div>
      ))}
    </div>
  );
}

/** Personalised, fact-checked insight feed. Newest first. */
export function InsightFeed() {
  const profile = useFanProfile();
  const { language } = profile;
  const feed = useFeed(profile);

  if (feed.isPending) return <FeedSkeleton />;
  if (feed.isError && !feed.data) {
    return (
      <div role="alert" className="flex flex-col items-start gap-3 rounded-lg border bg-card p-4">
        <p className="text-sm">{copy.feed.error}</p>
        <Button variant="outline" className="min-h-11 px-4" onClick={() => void feed.refetch()}>
          <RotateCw aria-hidden />
          {copy.connection.retry}
        </Button>
      </div>
    );
  }
  if (!feed.data.length) {
    return <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{copy.feed.empty}</p>;
  }
  return (
    <ol className="flex flex-col gap-3" aria-label={copy.feed.heading}>
      <AnimatePresence initial={false}>
        {feed.data.map((item) => (
          <li key={item.insight_id}>
            <InsightCard item={item} language={language} />
          </li>
        ))}
      </AnimatePresence>
    </ol>
  );
}

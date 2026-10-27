"use client";

import { RotateCw } from "lucide-react";
import { AnimatePresence } from "motion/react";

import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useFeed } from "@/features/live/queries";
import { useFanProfile } from "@/features/profile/profile-store";
import { copy } from "@/lib/copy";

import type { FeedItem } from "@/lib/contract";

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
export function InsightFeed({
  limit,
  highlights = false,
  filter,
}: {
  limit?: number;
  /** Only the big moments plus the very latest item. */
  highlights?: boolean;
  filter?: (item: FeedItem) => boolean;
}) {
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
  let items = feed.data;
  if (filter) items = items.filter(filter);
  if (highlights) {
    const latest = items[0];
    const sure = items.filter((i) => i.signal_type === "goal" || i.signal_type === "card");
    const chance = items.find((i) => i.signal_type === "big_chance");
    const picked = new Set([...sure, ...(chance ? [chance] : []), ...(latest ? [latest] : [])]);
    items = items.filter((i) => picked.has(i));
  }
  if (limit) items = items.slice(0, limit);
  if (!items.length) {
    return <EmptyState>{copy.feed.empty}</EmptyState>;
  }
  return (
    <ol className="flex flex-col gap-3" aria-label={copy.feed.heading}>
      <AnimatePresence initial={false}>
        {items.map((item) => (
          <li key={item.insight_id}>
            <InsightCard item={item} language={language} />
          </li>
        ))}
      </AnimatePresence>
    </ol>
  );
}

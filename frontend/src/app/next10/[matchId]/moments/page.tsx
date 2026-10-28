import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { n10 } from "@/features/next10/copy";
import { MomentsPlayout } from "@/features/next10/moments-playout";

export const metadata: Metadata = { title: n10.moments.heading };

async function Playout({ params }: { params: Promise<{ matchId: string }> }) {
  const { matchId } = await params;
  return <MomentsPlayout matchId={decodeURIComponent(matchId)} />;
}

/** One placeholder so the route has a prerendered shell; every real match id renders on demand. */
export function generateStaticParams() {
  return [{ matchId: "demo" }];
}

export default function Page({ params }: { params: Promise<{ matchId: string }> }) {
  return (
    <Suspense fallback={<Skeleton className="mx-auto mt-6 h-96 w-full max-w-7xl rounded-2xl" />}>
      <Playout params={params} />
    </Suspense>
  );
}

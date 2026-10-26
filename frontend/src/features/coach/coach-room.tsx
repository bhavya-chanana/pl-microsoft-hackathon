"use client";

import { RotateCw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { FactCheckBadge } from "@/features/companion/fact-check-badge";
import { useLive } from "@/features/live/live-store";
import { useCoachBrief } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import { ReplayControls } from "@/features/replay/replay-controls";
import type { CoachItem, Confidence } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const CONFIDENCE_TONE: Record<Confidence, string> = {
  high: "bg-verified/15 text-verified",
  medium: "bg-data/15 text-data",
  low: "bg-muted text-muted-foreground",
};

function ItemCard({ item }: { item: CoachItem }) {
  return (
    <li className="flex gap-4 rounded-lg border bg-card p-4 shadow-raised sm:p-5">
      <span aria-hidden className="font-display text-4xl leading-none font-bold text-muted-foreground tabular-nums">
        {item.rank}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h3 className="font-display text-xl font-semibold">
            <span className="sr-only">{item.rank}. </span>
            {item.title}
          </h3>
          <span
            className={cn("rounded-full px-2 py-0.5 text-xs font-medium", CONFIDENCE_TONE[item.confidence])}
            title={copy.coach.confidenceHelp}
          >
            {copy.coach.confidence[item.confidence]}
          </span>
        </div>
        <dl className="grid gap-3 text-sm">
          <div>
            <dt className="text-xs font-medium text-muted-foreground">{copy.coach.evidence}</dt>
            <dd>{item.evidence}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-muted-foreground">{copy.coach.recommendation}</dt>
            <dd className="font-medium">{item.recommendation}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-muted-foreground">{copy.coach.tradeOff}</dt>
            <dd>{item.trade_off}</dd>
          </div>
        </dl>
        {item.key_numbers.length > 0 && (
          <ul className="flex flex-wrap gap-2">
            {item.key_numbers.map((k) => (
              <li key={k.label} className="rounded-md border px-2.5 py-1 text-xs">
                <span className="text-muted-foreground">{k.label} </span>
                <span className="font-semibold tabular-nums">{k.value}</span>
                {k.unit && <span className="text-muted-foreground"> {k.unit}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

/** Coach persona: ranked, evidence-backed recommendations for the bench of one side. */
export function CoachRoom() {
  const meta = useLive((s) => s.meta);
  const language = useProfile((s) => s.language);
  const [picked, setPicked] = useState<string | null>(null);
  const teamId = picked ?? meta?.home.team_id ?? null;
  const query = useCoachBrief(teamId, language);
  const brief = query.data;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="font-display text-display font-semibold">{copy.coach.heading}</h1>
        <p className="max-w-prose text-sm text-muted-foreground">{copy.coach.description}</p>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        {meta && teamId && (
          <div className="flex flex-col gap-1.5">
            <span id="coach-team" className="text-xs font-medium text-muted-foreground">
              {copy.coach.team}
            </span>
            <ToggleGroup
              aria-labelledby="coach-team"
              value={[teamId]}
              onValueChange={(v: string[]) => v[0] && setPicked(v[0])}
              variant="outline"
              spacing={0}
            >
              {[meta.home, meta.away].map((t) => (
                <ToggleGroupItem
                  key={t.team_id}
                  value={t.team_id}
                  className="h-11 px-4 aria-pressed:bg-primary aria-pressed:text-primary-foreground"
                >
                  {t.name}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        )}
        <div className="flex items-center gap-3">
          {brief && <span className="text-sm text-muted-foreground tabular-nums">{copy.coach.asOf(brief.minute)}</span>}
          <Button
            variant="outline"
            className="min-h-11 px-4"
            onClick={() => void query.refetch()}
            disabled={!teamId || query.isFetching}
          >
            <RotateCw aria-hidden className={cn(query.isFetching && "animate-spin")} />
            {copy.coach.refresh}
          </Button>
        </div>
      </div>
      <section aria-busy={query.isFetching} lang={language} className="flex flex-col gap-4">
        {query.isPending ? (
          <div role="status" className="flex flex-col gap-4">
            <p className="text-sm text-muted-foreground">{copy.coach.loading}</p>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-40 w-full rounded-lg" />
            ))}
          </div>
        ) : query.isError && !brief ? (
          <p role="alert" className="text-sm">
            {copy.coach.error}
          </p>
        ) : brief ? (
          <>
            <ol className="flex flex-col gap-4">
              {brief.items.map((item) => (
                <ItemCard key={item.issue_id} item={item} />
              ))}
            </ol>
            <p className="flex flex-wrap items-center gap-2 border-t pt-3 text-xs text-muted-foreground">
              <FactCheckBadge
                status={brief.source === "data" && brief.fact_check.status === "verified" ? "data_only" : brief.fact_check.status}
              />
              <span>{copy.coach.pipeline}</span>
            </p>
          </>
        ) : null}
      </section>
      <ReplayControls className="max-w-xl" />
    </div>
  );
}

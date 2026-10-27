"use client";

import { Check, Pin, PinOff, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { useCoachBrief } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import { ReplayControls } from "@/features/replay/replay-controls";
import type { CoachBrief, CoachItem, Confidence } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { briefKey, useCoach } from "./coach-store";

const CONFIDENCE_TONE: Record<Confidence, string> = {
  high: "bg-verified/15 text-verified",
  medium: "bg-data/15 text-data",
  low: "bg-muted text-muted-foreground",
};

function ItemCard({ item, side, minute }: { item: CoachItem; side: "home" | "away"; minute: string }) {
  const pinned = useCoach((s) => s.pinned.includes(item.issue_id));
  return (
    <li className={cn("relative flex gap-4 overflow-hidden rounded-2xl border bg-card p-4 pl-5 shadow-raised sm:p-5 sm:pl-6", pinned && "ring-1 ring-primary/50")}>
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1.5", side === "home" ? "bg-home" : "bg-away")} />
      <span aria-hidden className="font-display text-[40px] leading-none font-bold text-muted-foreground tabular-nums">
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
          <span className="ml-auto flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="icon"
              className="size-11"
              aria-pressed={pinned}
              aria-label={pinned ? copy.coachPage.unpin : copy.coachPage.pin}
              onClick={() => useCoach.getState().togglePin(item.issue_id)}
            >
              {pinned ? <PinOff aria-hidden /> : <Pin aria-hidden />}
            </Button>
            <Button variant="ghost" size="icon" className="size-11" aria-label={copy.coachPage.dismiss} onClick={() => useCoach.getState().dismiss(item.issue_id)}>
              <X aria-hidden />
            </Button>
          </span>
        </div>
        <dl className="grid gap-3 text-[15px]">
          <div>
            <dt className="text-[13px] font-medium text-muted-foreground">{copy.coach.evidence}</dt>
            <dd>{item.evidence}</dd>
          </div>
          <div>
            <dt className="text-[13px] font-medium text-muted-foreground">{copy.coach.recommendation}</dt>
            <dd className="font-medium">{item.recommendation}</dd>
          </div>
          <div>
            <dt className="text-[13px] font-medium text-muted-foreground">{copy.coach.tradeOff}</dt>
            <dd>{item.trade_off}</dd>
          </div>
        </dl>
        {item.key_numbers.length > 0 && (
          <ul className="flex flex-wrap gap-2">
            {item.key_numbers.map((k) => (
              <li key={k.label} className="rounded-md border px-2.5 py-1 text-[13px]">
                <span className="text-muted-foreground">{k.label} </span>
                <span className="font-semibold tabular-nums">{k.value}</span>
                {k.unit && <span className="text-muted-foreground"> {k.unit}</span>}
                <span className="text-muted-foreground"> · {minute}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

/** Up to three chips: what is new or moved since the previous brief. */
function changes(brief: CoachBrief, prev: CoachBrief | null): string[] {
  if (!prev) return [];
  const out: string[] = [];
  for (const item of brief.items) {
    const was = prev.items.find((p) => p.issue_id === item.issue_id);
    if (!was) out.push(copy.coachPage.newItem(item.title));
    else if (item.rank < was.rank) out.push(copy.coachPage.movedUp(item.title));
    else if (item.confidence !== was.confidence) {
      const order = ["low", "medium", "high"];
      out.push((order.indexOf(item.confidence) > order.indexOf(was.confidence) ? copy.coachPage.confidenceUp : copy.coachPage.confidenceDown)(item.title));
    }
  }
  return out.slice(0, 3);
}

function useAgeSeconds(at: number | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, []);
  return at ? Math.max(0, Math.round((now - at) / 1000)) : 0;
}

/** Coach persona: ranked, evidence-backed recommendations for the bench of one side. Cache-first. */
export function CoachRoom({ onReport }: { onReport: () => void }) {
  const meta = useLive((s) => s.meta);
  const finished = useLive((s) => s.status === "finished" || s.status === "archived");
  const language = useProfile((s) => s.language);
  const picked = useCoach((s) => s.teamId);
  const teamId = picked ?? meta?.home.team_id ?? null;
  const query = useCoachBrief(teamId, language);
  const key = teamId ? briefKey(teamId, language) : "";
  const cached = useCoach((s) => s.briefs[key]);
  const dismissed = useCoach((s) => s.dismissed);
  const pinned = useCoach((s) => s.pinned);
  const live = query.data;
  useEffect(() => {
    if (live && key) useCoach.getState().save(key, live);
  }, [live, key]);
  const brief = live ?? cached?.brief;
  const age = useAgeSeconds(cached?.at);
  const strip = useMemo(() => (brief ? changes(brief, cached?.prev ?? null) : []), [brief, cached?.prev]);
  const side = meta && teamId === meta.away.team_id ? "away" : "home";
  const items = useMemo(() => {
    const all = (brief?.items ?? []).filter((i) => !dismissed.includes(i.issue_id));
    return [...all.filter((i) => pinned.includes(i.issue_id)), ...all.filter((i) => !pinned.includes(i.issue_id))];
  }, [brief, dismissed, pinned]);
  const hidden = (brief?.items.length ?? 0) - items.length;
  const fresh = !query.isFetching && Boolean(live);
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <p className="flex flex-wrap items-center gap-x-3 text-sm text-muted-foreground">
        <span className="max-w-prose">{copy.coach.description}</span>
        {brief && (
          <span className="tabular-nums">
            {copy.coach.asOf(brief.minute)} · {query.isFetching ? copy.coachPage.refreshing : copy.coachPage.updated(age)}
          </span>
        )}
      </p>
      {finished && (
        <div className="flex flex-col items-start gap-3 rounded-2xl border bg-card p-5 shadow-raised">
          <p className="text-lg font-semibold">{copy.coachPage.fullTime}</p>
          <Button className="h-11 px-5" onClick={onReport}>
            {copy.coachPage.seeReport}
          </Button>
        </div>
      )}
      {!finished && strip.length > 0 && (
        <section aria-label={copy.coachPage.changed} className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium text-muted-foreground">{copy.coachPage.changed}</span>
          {strip.map((c) => (
            <span key={c} className="rounded-full bg-raised px-3 py-1 text-[13px] ring-1 ring-border">
              {c}
            </span>
          ))}
        </section>
      )}
      {!finished && (
        <section aria-busy={query.isFetching} lang={language} className="flex flex-col gap-4">
          {!brief && query.isPending ? (
            <div role="status" className="flex flex-col gap-4">
              <p className="text-sm text-muted-foreground">{copy.coach.loading}</p>
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-40 w-full rounded-2xl" />
              ))}
            </div>
          ) : query.isError && !brief ? (
            <p role="alert" className="text-sm">
              {copy.coach.error}
            </p>
          ) : brief ? (
            <>
              <ol className="flex flex-col gap-4">
                {items.map((item) => (
                  <ItemCard key={item.issue_id} item={item} side={side} minute={brief.minute} />
                ))}
              </ol>
              {hidden > 0 && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  {copy.coachPage.dismissedN(hidden)}
                  <Button variant="ghost" className="h-11 px-3" onClick={() => useCoach.getState().restore()}>
                    {copy.coachPage.undo}
                  </Button>
                </p>
              )}
              <p className="flex flex-wrap items-center gap-2 border-t pt-3 text-[13px] text-muted-foreground">
                <Popover>
                  <PopoverTrigger className="inline-flex min-h-11 items-center gap-1.5 rounded-md px-1 hover:text-foreground">
                    <Check aria-hidden className={cn("size-4", fresh && "text-verified")} />
                    {copy.coachPage.checked}
                  </PopoverTrigger>
                  <PopoverContent className="w-72 text-sm">{copy.coach.pipeline}</PopoverContent>
                </Popover>
              </p>
            </>
          ) : null}
        </section>
      )}
      <ReplayControls className="max-w-xl" />
    </div>
  );
}

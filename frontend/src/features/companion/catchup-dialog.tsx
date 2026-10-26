"use client";

import { History, RotateCw, Sparkles, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useLive } from "@/features/live/live-store";
import { useCatchup, useRecap } from "@/features/live/queries";
import { useFanProfile } from "@/features/profile/profile-store";
import { CATCHUP_WINDOWS, type CatchupWindow, type FactCheck, type MatchMeta } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { playerName } from "@/lib/match";
import { cn } from "@/lib/utils";

import { FactCheckBadge } from "./fact-check-badge";

function Loading({ label }: { label: string }) {
  return (
    <div className="flex flex-col gap-3" role="status">
      <p className="text-sm text-muted-foreground">{label}</p>
      <Skeleton className="h-7 w-3/4" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-4 w-2/3" />
    </div>
  );
}

function Failed({ label, retry }: { label: string; retry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-3">
      <p className="text-sm">{label}</p>
      <Button variant="outline" className="min-h-11 px-4" onClick={retry}>
        <RotateCw aria-hidden />
        {copy.connection.retry}
      </Button>
    </div>
  );
}

function Score({ meta, score, label }: { meta: MatchMeta; score: Record<string, number>; label: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="font-display text-2xl font-bold tabular-nums">
        {score[meta.home.team_id] ?? 0}-{score[meta.away.team_id] ?? 0}
      </span>
    </div>
  );
}

function Provenance({ factCheck, source }: { factCheck: FactCheck; source: string }) {
  return (
    <p className="flex flex-wrap items-center gap-2 border-t pt-3 text-xs text-muted-foreground">
      <FactCheckBadge status={source === "data" && factCheck.status === "verified" ? "data_only" : factCheck.status} />
    </p>
  );
}

function Header({ title, description }: { title: string; description?: string }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex flex-col gap-1">
        <DialogTitle className="font-display text-2xl font-semibold">{title}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </div>
      <DialogClose render={<Button variant="ghost" size="icon-lg" className="-mt-1 -mr-1 size-11" aria-label={copy.catchup.close} />}>
        <X aria-hidden />
      </DialogClose>
    </div>
  );
}

/** The primary action: "What did I miss?" for the last 5, 15 or 30 minutes. */
export function CatchupDialog({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const [minutes, setMinutes] = useState<CatchupWindow>(15);
  const profile = useFanProfile();
  const meta = useLive((s) => s.meta);
  const query = useCatchup(minutes, profile, open);
  const data = query.data;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="lg" className={cn("h-12 w-full gap-2 text-base", className)} disabled={!meta} />}>
        <History aria-hidden className="size-5" />
        {copy.catchup.cta}
      </DialogTrigger>
      <DialogContent showCloseButton={false} className="gap-5 p-5 sm:max-w-lg">
        <Header title={copy.catchup.heading} description={data ? `${data.from_minute} to ${data.to_minute}` : undefined} />
        <div className="flex flex-col gap-1.5">
          <span id="window-label" className="text-xs font-medium text-muted-foreground">
            {copy.catchup.windowLabel}
          </span>
          <ToggleGroup
            aria-labelledby="window-label"
            value={[String(minutes)]}
            onValueChange={(v: string[]) => v[0] && setMinutes(Number(v[0]) as CatchupWindow)}
            variant="outline"
            spacing={0}
            className="w-full"
          >
            {CATCHUP_WINDOWS.map((m) => (
              <ToggleGroupItem
                key={m}
                value={String(m)}
                className="h-11 flex-1 aria-pressed:bg-primary aria-pressed:text-primary-foreground"
              >
                {copy.catchup.window(m)}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <div lang={profile.language} aria-busy={query.isFetching} className="flex min-h-48 flex-col gap-4">
          {query.isPending ? (
            <Loading label={copy.catchup.loading} />
          ) : query.isError && !data ? (
            <Failed label={copy.catchup.error} retry={() => void query.refetch()} />
          ) : data && meta ? (
            <>
              <div className="flex items-end gap-6">
                <Score meta={meta} score={data.score_before} label={copy.catchup.scoreThen} />
                <Score meta={meta} score={data.score_now} label={copy.catchup.scoreNow} />
              </div>
              <p className="font-display text-xl leading-snug font-semibold">{data.headline}</p>
              {data.bullets.length > 0 && (
                <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm marker:text-muted-foreground">
                  {data.bullets.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              )}
              {data.momentum_summary && <p className="text-sm text-muted-foreground">{data.momentum_summary}</p>}
              {data.spotlight_player_id && (
                <p className="text-sm">
                  <Sparkles aria-hidden className="mr-1 inline size-4" />
                  {playerName(meta, data.spotlight_player_id)}
                </p>
              )}
              <Provenance factCheck={data.fact_check} source={data.source} />
            </>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function RecapDialog() {
  const [open, setOpen] = useState(false);
  const profile = useFanProfile();
  const meta = useLive((s) => s.meta);
  const query = useRecap(profile, open);
  const data = query.data;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="lg" className="h-12 w-full text-base" disabled={!meta} />}>
        {copy.recap.cta}
      </DialogTrigger>
      <DialogContent showCloseButton={false} className="gap-5 p-5 sm:max-w-lg">
        <Header title={data?.title ?? copy.recap.heading} />
        <div lang={profile.language} aria-busy={query.isFetching} className="flex min-h-48 flex-col gap-4">
          {query.isPending ? (
            <Loading label={copy.recap.loading} />
          ) : query.isError && !data ? (
            <Failed label={copy.recap.error} retry={() => void query.refetch()} />
          ) : data ? (
            <>
              <p className="max-w-prose text-sm leading-relaxed">{data.summary}</p>
              {data.key_moments.length > 0 && (
                <section className="flex flex-col gap-1.5">
                  <h3 className="text-xs font-medium text-muted-foreground">{copy.recap.keyMoments}</h3>
                  <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
                    {data.key_moments.map((m) => (
                      <li key={m}>{m}</li>
                    ))}
                  </ul>
                </section>
              )}
              <dl className="grid gap-3 sm:grid-cols-2">
                {data.turning_point && (
                  <div>
                    <dt className="text-xs text-muted-foreground">{copy.recap.turningPoint}</dt>
                    <dd className="text-sm">{data.turning_point}</dd>
                  </div>
                )}
                {data.player_of_the_match_id && (
                  <div>
                    <dt className="text-xs text-muted-foreground">{copy.recap.playerOfMatch}</dt>
                    <dd className="font-display text-lg font-semibold">{playerName(meta, data.player_of_the_match_id)}</dd>
                  </div>
                )}
              </dl>
              <Provenance factCheck={data.fact_check} source={data.source} />
            </>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

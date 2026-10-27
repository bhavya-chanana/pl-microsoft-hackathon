"use client";

import { Check, Circle, RotateCw, UserRoundPlus, XIcon } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { describeEvent, EVENT_ICON } from "@/features/companion/match-timeline";
import { RateSelect } from "@/features/fans/player-ratings";
import { MatchCard } from "@/features/fans/match-card";
import { useCoach } from "@/features/coach/coach-store";
import { useLive } from "@/features/live/live-store";
import { usePlayerEvents, usePlayerStats, useRatings } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import { useMediaQuery } from "@/hooks/use-media-query";
import type { MatchMeta, PlayerStats } from "@/lib/contract";
import { copy, eventLabels } from "@/lib/copy";
import { formatNumber, sideOf, sideTone, teamName } from "@/lib/match";
import { cn } from "@/lib/utils";

const C = copy.playerCard;
const RECENT = 5;
/** Match-level markers that name a player only because they took the kick. */
const MATCH_EVENTS = new Set(["kickoff", "half_time", "full_time"]);

function Stat({ label, value, unit, note }: { label: string; value: string; unit?: string; note?: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md bg-raised px-3 py-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="flex flex-col">
        <span className="font-display text-2xl leading-tight font-semibold tabular-nums">
          {value}
          {unit && <span className="ml-1 text-sm font-normal text-muted-foreground">{unit}</span>}
        </span>
        {note && <span className="text-xs text-muted-foreground">{note}</span>}
      </dd>
    </div>
  );
}

function StatsGrid({ stats }: { stats: PlayerStats }) {
  return (
    <dl className="grid grid-cols-2 gap-2">
      <Stat
        label={C.passes}
        value={String(stats.passes)}
        note={stats.pass_accuracy === null ? undefined : C.accurate(`${formatNumber(stats.pass_accuracy)}%`)}
      />
      <Stat
        label={C.distance}
        value={stats.distance_km === null ? "–" : stats.distance_km.toFixed(1)}
        unit={stats.distance_km === null ? undefined : "km"}
      />
      <Stat
        label={C.topSpeed}
        value={stats.top_speed_kmh === null ? "–" : formatNumber(stats.top_speed_kmh, 1)}
        unit={stats.top_speed_kmh === null ? undefined : "km/h"}
      />
      <Stat label={C.shots} value={String(stats.shots)} note={C.onTarget(stats.shots_on_target)} />
    </dl>
  );
}

function StatsSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-2" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-17 rounded-md" />
      ))}
    </div>
  );
}

function RecentMoments({ playerId, meta }: { playerId: string; meta: MatchMeta }) {
  const events = usePlayerEvents(playerId);
  return (
    <section aria-labelledby="player-recent" className="flex flex-col gap-2">
      <h3 id="player-recent" className="text-sm font-medium">
        {C.recent}
      </h3>
      {events.isPending ? (
        <div className="flex flex-col gap-2" aria-hidden>
          <Skeleton className="h-5 w-4/5" />
          <Skeleton className="h-5 w-3/5" />
        </div>
      ) : events.isError && !events.data ? (
        <Retry label={C.recentError} retry={() => void events.refetch()} />
      ) : !events.data.some((e) => !MATCH_EVENTS.has(e.type)) ? (
        <p className="text-sm text-muted-foreground">{C.noRecent}</p>
      ) : (
        <ol className="flex flex-col">
          {events.data
            .filter((e) => !MATCH_EVENTS.has(e.type))
            .slice(-RECENT)
            .reverse()
            .map((e) => {
              const Icon = EVENT_ICON[e.type] ?? Circle;
              const side = sideOf(meta, e.team_id);
              return (
                <li key={e.event_id} className="grid grid-cols-[2.75rem_1.25rem_1fr] items-start gap-2 border-b py-2 last:border-0">
                  <span className="font-display text-base leading-5 font-semibold tabular-nums">{e.minute_label}</span>
                  <Icon aria-hidden className={cn("mt-px size-4", side ? sideTone[side].text : "text-muted-foreground")} />
                  <p className="text-sm leading-5">
                    <span className="text-muted-foreground">{eventLabels[e.type] ?? e.type}</span> {describeEvent(e, meta)}
                  </p>
                </li>
              );
            })}
        </ol>
      )}
    </section>
  );
}

function Retry({ label, retry }: { label: string; retry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-2">
      <p className="text-sm">{label}</p>
      <Button variant="outline" className="min-h-11 px-4" onClick={retry}>
        <RotateCw data-icon="inline-start" aria-hidden />
        {copy.connection.retry}
      </Button>
    </div>
  );
}

function FollowButton({ playerId, name }: { playerId: string; name: string }) {
  const following = useProfile((s) => s.playerId === playerId && s.mode === "player_focus");
  const follow = useProfile((s) => s.follow);
  if (following) {
    return (
      <Button size="lg" variant="secondary" className="h-11 w-full" disabled focusableWhenDisabled>
        <Check data-icon="inline-start" aria-hidden />
        {C.following}
      </Button>
    );
  }
  return (
    <Button
      size="lg"
      className="h-11 w-full"
      onClick={() => {
        follow(playerId);
        toast.success(C.followed(name));
      }}
    >
      <UserRoundPlus data-icon="inline-start" aria-hidden />
      {C.follow}
    </Button>
  );
}

/** Fan mode only: one "Rate this player" control instead of vote columns. */
function VoteSection({ playerId }: { playerId: string }) {
  const coachMode = useCoach((s) => s.coachMode);
  const runId = useLive((s) => s.runId);
  const row = useRatings().data?.find((r) => r.player_id === playerId);
  if (coachMode || !row || !runId) return null;
  return (
    <section aria-labelledby="player-vote" className="flex flex-col gap-2">
      <h3 id="player-vote" className="text-sm font-medium">
        {copy.playersTable.vote}
      </h3>
      <p className="text-[13px] text-muted-foreground">
        {copy.playersTable.voteHelp}
        {row.fan_rating !== null && ` · ${copy.fans.fansColumn} ${formatNumber(row.fan_rating, 1)} (${copy.fans.votes(row.fan_votes)})`}
      </p>
      <RateSelect row={row} runId={runId} />
    </section>
  );
}

function PlayerBody({ playerId, meta }: { playerId: string; meta: MatchMeta }) {
  const info = meta.players[playerId];
  const stats = usePlayerStats(playerId);
  const teamId = stats.data?.team_id ?? playerId.split("-")[0];
  const side = sideOf(meta, teamId) ?? "home";
  const onPitch = stats.data?.on_pitch;
  return (
    <>
      <span aria-hidden className={cn("absolute inset-x-0 top-0 h-1", sideTone[side].bg)} />
      <header className="flex items-start gap-4 pr-12">
        <span className={cn("font-display text-score font-bold tabular-nums", sideTone[side].text)} aria-hidden>
          {info?.shirt}
        </span>
        <div className="flex min-w-0 flex-col gap-1">
          <SheetTitle className="font-display text-xl leading-tight font-semibold">
            <span className="sr-only">#{info?.shirt} </span>
            {info?.name ?? stats.data?.name}
          </SheetTitle>
          <SheetDescription>
            {info && `${copy.positions[info.position] ?? info.position}${info.role && info.role !== info.position ? ` (${info.role})` : ""}, `}
            {teamName(meta, teamId)}
          </SheetDescription>
          {onPitch !== undefined && (
            <Badge variant={onPitch ? "secondary" : "outline"}>{onPitch ? C.onPitch : C.offPitch}</Badge>
          )}
        </div>
      </header>
      {stats.isPending ? (
        <StatsSkeleton />
      ) : stats.data ? (
        <>
          {stats.data.card && <MatchCard card={stats.data.card} position={info?.position ?? ""} />}
          <StatsGrid stats={stats.data} />
        </>
      ) : (
        <Retry label={C.error} retry={() => void stats.refetch()} />
      )}
      <VoteSection playerId={playerId} />
      <RecentMoments playerId={playerId} meta={meta} />
      <div className="mt-auto pt-2">
        <FollowButton playerId={playerId} name={info?.name ?? ""} />
      </div>
    </>
  );
}

/**
 * Player card for a player picked on the pitch. Non-modal, so the fan can keep
 * picking other players while it is open; Esc or the close button dismisses it.
 */
export function PlayerSheet({
  playerId,
  onClose,
  returnFocus,
}: {
  playerId: string | null;
  onClose: () => void;
  /** Where focus goes when the card closes (the marker that opened it). */
  returnFocus: () => void;
}) {
  const meta = useLive((s) => s.meta);
  const wide = useMediaQuery("(min-width: 1024px)");
  return (
    <Sheet open={Boolean(playerId)} onOpenChange={(open) => !open && onClose()} modal={false} disablePointerDismissal>
      <SheetContent
        side={wide ? "right" : "bottom"}
        showOverlay={false}
        showCloseButton={false}
        finalFocus={() => {
          returnFocus();
          return false;
        }}
        className={cn(
          "gap-5 overflow-y-auto p-4 pt-6 shadow-floating",
          wide ? "w-full sm:max-w-sm" : "max-h-[70dvh] rounded-t-xl",
        )}
      >
        {playerId && meta && <PlayerBody key={playerId} playerId={playerId} meta={meta} />}
        <SheetClose
          render={<Button variant="ghost" size="icon" className="absolute top-3 right-3 size-11" aria-label={C.close} />}
        >
          <XIcon aria-hidden />
        </SheetClose>
      </SheetContent>
    </Sheet>
  );
}

"use client";

import { GitCompareArrows, LoaderCircle } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useRollout } from "@/features/live/queries";
import { ROLLOUT_VARIANTS, type RolloutVariant, type RolloutView, type Situation } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { GhostPitch, PlaybackControls, eventSentence, trackDuration, usePlayback } from "./ghost-pitch";
import { labelOf, rosterOf } from "./lib";

const t = copy.tactics.stage;

export type Branch = { id: string; title: string; highlightIds: string[] };

type QueryLike = { data: RolloutView | undefined; isPending: boolean; isError: boolean; refetch: () => unknown };

/** Pitch with a loading or error note laid over it; the starting positions show while the rollout loads. */
export function PitchBox({
  situation,
  rollout,
  query,
  time,
  branch,
  caption,
}: {
  situation: Situation;
  rollout: RolloutView | null;
  query: QueryLike;
  time: number;
  branch: Branch;
  caption?: string;
}) {
  return (
    <figure className="flex flex-col gap-2">
      {caption && <figcaption className="font-display text-lg leading-tight font-semibold">{caption}</figcaption>}
      <div className="relative aspect-[111/74] w-full overflow-hidden rounded-lg border bg-card shadow-raised">
        <GhostPitch
          situation={situation}
          rollout={rollout}
          time={time}
          highlightIds={branch.highlightIds}
          label={t.pitchLabel(branch.title, Math.round(trackDuration(rollout)))}
        />
        {query.isPending && (
          <p role="status" className="absolute bottom-2 left-2 flex items-center gap-2 rounded-md bg-graphic px-3 py-1.5 text-sm text-graphic-foreground">
            <LoaderCircle aria-hidden className="size-4 motion-safe:animate-spin" />
            {t.loading}
          </p>
        )}
        {query.isError && (
          <div role="alert" className="absolute inset-x-2 bottom-2 flex flex-wrap items-center gap-3 rounded-md bg-graphic px-3 py-2 text-sm text-graphic-foreground">
            {t.loadFailed}
            <Button variant="secondary" size="sm" className="h-11 px-3" onClick={() => void query.refetch()}>
              {t.retry}
            </Button>
          </div>
        )}
      </div>
    </figure>
  );
}

export function VariantPicker({ variant, onVariant }: { variant: RolloutVariant; onVariant: (v: RolloutVariant) => void }) {
  return (
    <div className="flex flex-col gap-1">
      <ToggleGroup aria-label={t.variantLabel} value={[variant]} onValueChange={(v) => v[0] && onVariant(v[0] as RolloutVariant)} variant="outline">
        {ROLLOUT_VARIANTS.map((v) => (
          <ToggleGroupItem key={v} value={v} title={t.variantHelp[v]} className="h-11 px-3 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
            {t.variants[v]}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}

function EventLog({ rollout, situation, time }: { rollout: RolloutView; situation: Situation; time: number }) {
  const roster = useMemo(() => rosterOf(situation), [situation]);
  const t0 = rollout.frames[0]?.elapsed ?? 0;
  const events = rollout.events.filter((e) => e.type !== "carry");
  return (
    <section aria-labelledby="events-heading" className="flex flex-col gap-1.5">
      <h3 id="events-heading" className="text-xs font-medium text-muted-foreground">
        {t.events}
      </h3>
      {events.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t.noEvents}</p>
      ) : (
        <ol className="flex flex-col gap-1 text-sm">
          {events.map((e) => {
            // Events can sit a hair before the first frame; never show a negative time.
            const at = Math.max(0, e.elapsed - t0);
            const happened = at <= time + 0.05;
            return (
              <li key={e.event_id} className={cn("flex gap-3 transition-opacity", !happened && "opacity-45")}>
                <span className="w-10 shrink-0 text-right font-display font-semibold tabular-nums">{at.toFixed(1)}s</span>
                <span>{eventSentence(e.type, e.player_id, e.related_player_id, roster)}</span>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function StageBody({
  situation,
  branch,
  variant,
  onVariant,
  onCompare,
  query,
}: {
  situation: Situation;
  branch: Branch;
  variant: RolloutVariant;
  onVariant: (v: RolloutVariant) => void;
  onCompare?: () => void;
  query: QueryLike;
}) {
  const rollout = query.data ?? null;
  const playback = usePlayback(trackDuration(rollout));
  const roster = useMemo(() => rosterOf(situation), [situation]);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="min-w-0 font-display text-xl leading-tight font-semibold">
          <span className="sr-only">{t.heading}: </span>
          {t.showing(branch.title)}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <VariantPicker variant={variant} onVariant={onVariant} />
          {onCompare && (
            <Button variant="outline" className="h-11 gap-2 px-3" onClick={onCompare}>
              <GitCompareArrows aria-hidden />
              {t.compare}
            </Button>
          )}
        </div>
      </div>
      <PitchBox situation={situation} rollout={rollout} query={query} time={playback.time} branch={branch} />
      <PlaybackControls playback={playback} rollout={rollout} roster={roster} />
      {rollout && (
        <p className="text-sm">
          <span className="text-muted-foreground">{t.terminal}: </span>
          <span className="font-medium">{labelOf(t.terminals, rollout.terminal)}</span>
        </p>
      )}
      {rollout && <EventLog rollout={rollout} situation={situation} time={playback.time} />}
    </div>
  );
}

/** One branch on one pitch, with playback, the three rollout variants and its event log. */
export function RolloutStage({
  boardId,
  situation,
  branch,
  variant,
  onVariant,
  onCompare,
}: {
  boardId: string;
  situation: Situation;
  branch: Branch;
  variant: RolloutVariant;
  onVariant: (v: RolloutVariant) => void;
  onCompare?: () => void;
}) {
  const query = useRollout(boardId, branch.id, variant);
  // Keyed so each branch and variant starts from the top.
  return <StageBody key={`${branch.id}:${variant}`} situation={situation} branch={branch} variant={variant} onVariant={onVariant} onCompare={onCompare} query={query} />;
}

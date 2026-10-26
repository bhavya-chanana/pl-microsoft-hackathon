"use client";

import { X } from "lucide-react";
import { useId, useMemo } from "react";

import { Button } from "@/components/ui/button";
import { useRollout } from "@/features/live/queries";
import type { OutcomeStats, RolloutVariant, Situation } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { PlaybackControls, trackDuration, usePlayback } from "./ghost-pitch";
import { TONE_CLASS, formatMetric, labelOf, liftText, liftTone, rosterOf } from "./lib";
import { metricRows } from "./outcome-bar";
import { type Branch, PitchBox, VariantPicker } from "./rollout-stage";

const t = copy.tactics.compare;

export type CompareBranch = Branch & { stats: OutcomeStats | null };
export type CompareOption = { id: string; title: string };

function DiffTable({ left, right }: { left: CompareBranch; right: CompareBranch }) {
  if (!left.stats || !right.stats) return null;
  const rows = metricRows(left.stats, right.stats);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-96 border-collapse text-sm">
        <caption className="sr-only">{t.heading}</caption>
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th scope="col" className="py-2 pr-3 font-medium">
              {t.metric}
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              {left.title}
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              {right.title}
            </th>
            <th scope="col" className="py-2 pl-3 text-right font-medium">
              {t.difference}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const tone = liftTone(r.metric, r.delta);
            return (
              <tr key={r.metric} className="border-b last:border-b-0">
                <th scope="row" className="py-2 pr-3 text-left font-normal">
                  {labelOf(copy.tactics.outcome.metrics, r.metric)}
                </th>
                <td className="px-3 py-2 text-right tabular-nums">{formatMetric(r.metric, r.a)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatMetric(r.metric, r.b)}</td>
                <td className={cn("py-2 pl-3 text-right font-medium tabular-nums", TONE_CLASS[tone])}>
                  {liftText(r.metric, r.delta)}
                  <span className="sr-only"> ({tone === "good" ? t.better : tone === "bad" ? t.worse : t.same})</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Two branches on two pitches that play in lockstep, with the measures side by side. */
export function BranchCompare({
  boardId,
  situation,
  left,
  right,
  options,
  onRight,
  variant,
  onVariant,
  onExit,
}: {
  boardId: string;
  situation: Situation;
  left: CompareBranch;
  right: CompareBranch;
  options: CompareOption[];
  onRight: (id: string) => void;
  variant: RolloutVariant;
  onVariant: (v: RolloutVariant) => void;
  onExit: () => void;
}) {
  const id = useId();
  const leftQuery = useRollout(boardId, left.id, variant);
  const rightQuery = useRollout(boardId, right.id, variant);
  const duration = Math.max(trackDuration(leftQuery.data), trackDuration(rightQuery.data));
  return <CompareBody key={`${left.id}:${right.id}:${variant}`} {...{ id, situation, left, right, options, onRight, variant, onVariant, onExit, leftQuery, rightQuery, duration }} />;
}

type Query = ReturnType<typeof useRollout>;

function CompareBody({
  id,
  situation,
  left,
  right,
  options,
  onRight,
  variant,
  onVariant,
  onExit,
  leftQuery,
  rightQuery,
  duration,
}: {
  id: string;
  situation: Situation;
  left: CompareBranch;
  right: CompareBranch;
  options: CompareOption[];
  onRight: (id: string) => void;
  variant: RolloutVariant;
  onVariant: (v: RolloutVariant) => void;
  onExit: () => void;
  leftQuery: Query;
  rightQuery: Query;
  duration: number;
}) {
  const playback = usePlayback(duration);
  const roster = useMemo(() => rosterOf(situation), [situation]);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 className="font-display text-xl font-semibold">{t.heading}</h2>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor={`${id}-right`} className="text-xs font-medium text-muted-foreground">
              {t.right}
            </label>
            <select
              id={`${id}-right`}
              value={right.id}
              onChange={(e) => onRight(e.target.value)}
              className="h-11 max-w-64 rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {options
                .filter((o) => o.id !== left.id)
                .map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.title}
                  </option>
                ))}
            </select>
          </div>
          <VariantPicker variant={variant} onVariant={onVariant} />
          <Button variant="outline" className="h-11 gap-2 px-3" onClick={onExit}>
            <X aria-hidden />
            {copy.tactics.stage.exitCompare}
          </Button>
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <PitchBox situation={situation} rollout={leftQuery.data ?? null} query={leftQuery} time={playback.time} branch={left} caption={left.title} />
        <PitchBox situation={situation} rollout={rightQuery.data ?? null} query={rightQuery} time={playback.time} branch={right} caption={right.title} />
      </div>
      <PlaybackControls playback={playback} rollout={leftQuery.data ?? rightQuery.data ?? null} roster={roster} />
      <DiffTable left={left} right={right} />
    </div>
  );
}

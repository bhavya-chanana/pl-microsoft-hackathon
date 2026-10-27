"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { postJson } from "@/lib/api";
import { PlayerRating } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { formatNumber } from "@/lib/match";

import { useFans } from "./fan-store";

const T = copy.fans;
const SCORES = Array.from({ length: 10 }, (_, i) => String(10 - i));
const SCORE_ITEMS = [{ value: null, label: copy.fans.you }, ...SCORES.map((s) => ({ value: s, label: s }))];

function why(row: PlayerRating): string {
  return row.parts
    .slice(0, 2)
    .map((p) => `${T.parts[p.key] ?? p.key} ${p.delta > 0 ? "+" : ""}${formatNumber(p.delta, 1)}`)
    .join(" · ");
}

/** Your 1-10 score for one player; posts to the ratings endpoint. */
export function RateSelect({ row, runId }: { row: PlayerRating; runId: string }) {
  const mine = useFans((s) => s.scores[runId]?.[row.player_id]);
  const client = useQueryClient();
  const rate = useMutation({
    mutationFn: (score: number) =>
      postJson("ratings", { voter_id: useFans.getState().ensureVoterId(), player_id: row.player_id, score }, PlayerRating),
    onSuccess: (_, score) => {
      useFans.getState().score(runId, row.player_id, score);
      void client.invalidateQueries({ queryKey: ["ratings"] });
    },
    onError: () => toast.error(T.rateFailed),
  });
  return (
    <Select
      items={SCORE_ITEMS}
      value={mine ? String(mine) : null}
      onValueChange={(value) => value && rate.mutate(Number(value))}
    >
      <SelectTrigger aria-label={T.rate(row.name)} className="h-11 w-full" disabled={rate.isPending}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {SCORES.map((s) => (
          <SelectItem key={s} value={s}>
            {s}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export { why as ratingWhy };


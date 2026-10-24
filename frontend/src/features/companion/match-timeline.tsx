"use client";

import { Circle, CircleDot, Flag, Hand, RectangleVertical, Repeat, Target } from "lucide-react";

import { useLive } from "@/features/live/live-store";
import type { MatchEvent, MatchMeta } from "@/lib/contract";
import { copy, eventLabels } from "@/lib/copy";
import { playerName, sideOf, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

export const EVENT_ICON: Record<string, typeof Circle> = {
  goal: CircleDot,
  shot: Target,
  save: Hand,
  card: RectangleVertical,
  substitution: Repeat,
  kickoff: Flag,
  half_time: Flag,
  full_time: Flag,
};

/** One line about an event, in plain words. */
export function describeEvent(e: MatchEvent, meta: MatchMeta | null): string {
  const who = playerName(meta, e.player_id);
  const other = playerName(meta, e.related_player_id);
  const xg = typeof e.attrs.xg === "number" ? ` (xG ${e.attrs.xg.toFixed(2)})` : "";
  switch (e.type) {
    case "goal":
      return other ? `${who}, assisted by ${other}` : who;
    case "shot":
      return `${who}, ${e.outcome.replaceAll("_", " ")}${xg}`;
    case "save":
      return other ? `${who} denies ${other}` : who;
    case "card":
      return `${who}, ${e.outcome.replaceAll("_", " ")}`;
    case "substitution":
      return `${other} on, ${who} off`;
    default:
      return eventLabels[e.type] ?? e.type;
  }
}

export function MatchTimeline() {
  const timeline = useLive((s) => s.timeline);
  const meta = useLive((s) => s.meta);
  const events = timeline.slice().reverse();
  if (!events.length) {
    return <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{copy.timeline.empty}</p>;
  }
  return (
    <ol aria-label={copy.timeline.heading} className="flex flex-col">
      {events.map((e) => {
        const Icon = EVENT_ICON[e.type] ?? Circle;
        const side = sideOf(meta, e.team_id);
        const isGoal = e.type === "goal";
        return (
          <li key={e.event_id} className="grid grid-cols-[3rem_1.5rem_1fr] items-start gap-2 border-b py-2.5 last:border-0">
            <span className="font-display text-lg leading-6 font-semibold tabular-nums">{e.minute_label}</span>
            <Icon
              aria-hidden
              className={cn(
                "mt-0.5 size-5",
                side ? sideTone[side].text : "text-muted-foreground",
                e.type === "card" && e.outcome !== "yellow" && "text-blocked",
                e.type === "card" && e.outcome === "yellow" && "text-rewritten",
              )}
            />
            <p className={cn("text-sm leading-6", isGoal && "font-semibold")}>
              <span className={cn(!isGoal && "text-muted-foreground")}>{eventLabels[e.type] ?? e.type}</span>
              {!["kickoff", "half_time", "full_time"].includes(e.type) && <> {describeEvent(e, meta)}</>}
              {isGoal && typeof e.attrs.home_score === "number" && meta && (
                <span className="ml-2 font-display text-base tabular-nums">
                  {String(e.attrs.home_score)}-{String(e.attrs.away_score)}
                </span>
              )}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

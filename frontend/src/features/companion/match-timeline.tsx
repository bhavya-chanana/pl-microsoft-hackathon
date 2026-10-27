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

const SEPARATORS = new Set(["kickoff", "half_time", "full_time"]);

/** What a side's cell says: the main line, then a quieter second line. */
function Cell({ e, meta, side }: { e: MatchEvent; meta: MatchMeta | null; side: "home" | "away" }) {
  const align = side === "home" ? "items-end text-right" : "items-start text-left";
  if (e.type === "substitution") {
    return (
      <div className={cn("flex min-w-0 flex-col text-sm", align)}>
        <span className="max-w-full truncate font-medium text-[var(--rating-good)]">{playerName(meta, e.related_player_id)}</span>
        <span className="max-w-full truncate text-[var(--rating-poor)]">{playerName(meta, e.player_id)}</span>
      </div>
    );
  }
  const goal = e.type === "goal";
  const assist = goal ? playerName(meta, e.related_player_id) : "";
  return (
    <div className={cn("flex min-w-0 flex-col text-sm", align)}>
      <span className={cn("max-w-full truncate", goal ? "text-base font-semibold" : "font-medium")}>{playerName(meta, e.player_id) || eventLabels[e.type] || e.type}</span>
      <span className="max-w-full truncate text-[13px] text-muted-foreground">
        {goal ? (assist ? `Assist ${assist}` : eventLabels.goal) : e.type === "card" ? `${eventLabels.card}, ${e.outcome.replaceAll("_", " ")}` : `${eventLabels[e.type] ?? e.type}${e.type === "shot" || e.type === "save" ? `, ${e.outcome.replaceAll("_", " ")}` : ""}`}
      </span>
    </div>
  );
}

/** A centre spine of minute bubbles: home events on the left, away on the right, newest first. */
export function MatchTimeline() {
  const timeline = useLive((s) => s.timeline);
  const meta = useLive((s) => s.meta);
  const events = timeline.slice().reverse();
  if (!events.length) {
    return <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{copy.timeline.empty}</p>;
  }
  return (
    <ol aria-label={copy.timeline.heading} className="flex flex-col py-2">
      {events.map((e) => {
        if (SEPARATORS.has(e.type)) {
          return (
            <li key={e.event_id} className="flex items-center gap-3 py-3 text-[13px] font-medium text-muted-foreground">
              <span aria-hidden className="h-px flex-1 bg-border" />
              {eventLabels[e.type] ?? e.type} {e.minute_label}
              <span aria-hidden className="h-px flex-1 bg-border" />
            </li>
          );
        }
        const Icon = EVENT_ICON[e.type] ?? Circle;
        const side = sideOf(meta, e.team_id);
        const goal = e.type === "goal";
        const score = goal && typeof e.attrs.home_score === "number" ? `${String(e.attrs.home_score)}-${String(e.attrs.away_score)}` : null;
        return (
          <li key={e.event_id} className="grid grid-cols-[minmax(0,1fr)_3.5rem_minmax(0,1fr)] items-center gap-3 py-2">
            <div>{side === "home" && meta && <Cell e={e} meta={meta} side="home" />}</div>
            <div className="relative flex flex-col items-center before:absolute before:inset-y-[-0.5rem] before:left-1/2 before:w-px before:bg-border before:content-['']">
              <span
                className={cn(
                  "relative grid size-10 place-items-center rounded-full bg-card text-[13px] font-semibold tabular-nums ring-1 ring-border",
                  goal && side && cn("ring-2", side === "home" ? "ring-home" : "ring-away"),
                )}
              >
                <span className="sr-only">{eventLabels[e.type] ?? e.type} </span>
                {e.minute_label}
              </span>
              <Icon
                aria-hidden
                className={cn(
                  "relative -mt-1.5 size-4 rounded-full bg-card",
                  side ? sideTone[side].text : "text-muted-foreground",
                  e.type === "card" && e.outcome !== "yellow" && "text-blocked",
                  e.type === "card" && e.outcome === "yellow" && "text-rewritten",
                )}
              />
              {score && <span className="relative mt-0.5 rounded-md bg-card px-1 font-display text-sm font-bold tabular-nums">{score}</span>}
            </div>
            <div>{side === "away" && meta && <Cell e={e} meta={meta} side="away" />}</div>
          </li>
        );
      })}
    </ol>
  );
}

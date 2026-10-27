"use client";

import { AnimatePresence, m, useReducedMotion } from "motion/react";

import { useLive } from "@/features/live/live-store";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { sideTone } from "@/lib/match";

/** The score bug: always on screen, top left, like a real broadcast. */
export function ScoreBug({ className }: { className?: string }) {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const reduce = useReducedMotion();
  if (!meta || !state) return null;
  const finished = state.status === "full_time";
  const halfTime = state.status === "half_time";
  const clock = finished ? copy.status.fullTime : halfTime ? copy.status.halfTime : state.minute_label;
  const home = meta.home;
  const away = meta.away;
  return (
    <div
      className={cn("graphic-cut inline-flex items-stretch bg-graphic text-graphic-foreground shadow-floating", className)}
      aria-label={`${home.name} ${state.score[home.team_id] ?? 0}, ${away.name} ${state.score[away.team_id] ?? 0}, ${clock}`}
      role="group"
    >
      {(["home", "away"] as const).map((side, i) => {
        const team = meta[side];
        const inPossession = state.possession_team === team.team_id;
        return (
          <div key={side} className={cn("flex items-center gap-1.5 px-2 py-0.5 @lg:gap-2 @lg:px-3 @lg:py-1", i === 1 && "flex-row-reverse")}>
            <span
              aria-hidden
              className={cn("h-4 rounded-full transition-[width] duration-300 @lg:h-6", sideTone[side].bg, inPossession ? "w-1.5" : "w-1")}
            />
            <span className="font-display text-sm font-semibold tracking-wide uppercase @lg:text-lg">{team.short_name}</span>
            <span className="relative inline-grid overflow-hidden font-display text-lg font-bold tabular-nums @lg:text-2xl">
              {/* A new goal rolls the number up like a stadium scoreboard. */}
              <AnimatePresence initial={false} mode="popLayout">
                <m.span
                  key={state.score[team.team_id] ?? 0}
                  initial={{ y: reduce ? 0 : "100%", opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ y: reduce ? 0 : "-100%", opacity: 0 }}
                  transition={{ duration: reduce ? 0 : 0.4, ease: [0.16, 1, 0.3, 1] }}
                >
                  {state.score[team.team_id] ?? 0}
                </m.span>
              </AnimatePresence>
            </span>
          </div>
        );
      })}
      <div className="flex items-center bg-graphic-foreground/10 px-2 pr-4 font-display text-sm font-semibold tabular-nums @lg:px-3 @lg:pr-5 @lg:text-lg">
        {clock}
      </div>
    </div>
  );
}

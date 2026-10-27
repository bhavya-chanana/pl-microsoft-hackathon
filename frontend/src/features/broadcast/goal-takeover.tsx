"use client";

import { AnimatePresence, m, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { useLive } from "@/features/live/live-store";
import { useProfile } from "@/features/profile/profile-store";
import type { Signal } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { playerName, sideOf, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

import { useReplay } from "./replay-store";

const TAKEOVER_MS = 2_800;

/**
 * Watches for goals that happen while the viewer is watching (not ones already
 * in the match when they joined), shows the takeover, then rolls the replay.
 */
export function useGoalCall(autoReplay: boolean): Signal | null {
  const signals = useLive((s) => s.signals);
  const runId = useLive((s) => s.runId);
  const play = useReplay((s) => s.play);
  const seen = useRef<{ runId: string | null; ids: Set<string> } | null>(null);
  const [goal, setGoal] = useState<Signal | null>(null);

  useEffect(() => {
    if (!seen.current || seen.current.runId !== runId) {
      // Joining (or a new match): everything already scored is history, not a call.
      seen.current = { runId, ids: new Set(signals.filter((s) => s.type === "goal").map((s) => s.signal_id)) };
      return;
    }
    const fresh = signals.find((s) => s.type === "goal" && !seen.current!.ids.has(s.signal_id));
    if (!fresh) return;
    seen.current.ids.add(fresh.signal_id);
    setGoal(fresh);
  }, [signals, runId]);

  useEffect(() => {
    if (!goal) return;
    const timer = setTimeout(() => {
      setGoal(null);
      if (autoReplay) play(goal.signal_id);
    }, TAKEOVER_MS);
    return () => clearTimeout(timer);
  }, [goal, autoReplay, play]);

  return goal;
}

/** Full-frame goal call in the scoring side's colour, then straight into the replay. */
export function GoalTakeover({ autoReplay = true }: { autoReplay?: boolean }) {
  const goal = useGoalCall(autoReplay);
  const meta = useLive((s) => s.meta);
  const language = useProfile((s) => s.language);
  const reduce = useReducedMotion();
  const side = sideOf(meta, goal?.team_id) ?? "home";
  const team = meta && goal?.team_id ? meta[side] : null;
  const scorer = playerName(meta, goal?.player_ids[0]);
  const word = copy.instantReplay.goal[language];

  return (
    <AnimatePresence>
      {goal && team && (
        <m.div
          key={goal.signal_id}
          role="status"
          aria-live="assertive"
          className="pointer-events-none absolute inset-0 z-20 grid place-items-center overflow-hidden"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reduce ? 0 : 0.2 }}
        >
          <m.div
            aria-hidden
            className={cn("absolute inset-y-[22%] -inset-x-[10%] -skew-y-3", sideTone[side].bg)}
            initial={{ x: reduce ? 0 : "-100%" }}
            animate={{ x: 0 }}
            exit={{ x: reduce ? 0 : "100%" }}
            transition={{ duration: reduce ? 0 : 0.45, ease: [0.16, 1, 0.3, 1] }}
          />
          <div className="relative flex flex-col items-center gap-1 text-center text-graphic">
            <m.p
              className="font-display text-[clamp(3rem,18cqw,11rem)] leading-[0.85] font-bold tracking-tight uppercase"
              initial={{ scale: reduce ? 1 : 1.6, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: reduce ? 0 : 0.5, delay: reduce ? 0 : 0.2, ease: [0.16, 1, 0.3, 1] }}
            >
              {word}
            </m.p>
            <m.p
              className="font-display text-[clamp(1rem,3.4cqw,2.25rem)] font-semibold"
              initial={{ opacity: 0, y: reduce ? 0 : 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: reduce ? 0 : 0.35, delay: reduce ? 0 : 0.5 }}
            >
              {scorer ? copy.goalTakeover.scorer(scorer, goal.minute_label) : team.name}
            </m.p>
            {scorer && <p className="text-[clamp(0.75rem,1.8cqw,1.125rem)] font-medium opacity-80">{team.name}</p>}
          </div>
        </m.div>
      )}
    </AnimatePresence>
  );
}

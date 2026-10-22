"use client";

import { useEffect, useRef, useState } from "react";

import { useLive } from "@/features/live/live-store";
import type { OverlaySlot } from "@/lib/contract";

const SLOTS: OverlaySlot[] = ["lower", "upper_right", "banner"];
/** A graphic stays up at least this long in wall time, so it can be read at any replay speed. */
const MIN_HOLD_MS = 5_000;
/** A higher-priority graphic may cut in after the current one has been up this long. */
const MIN_BEFORE_PREEMPT_MS = 1_500;
/** Queued graphics older than this (wall time) are dropped rather than aired late. */
const MAX_QUEUE_MS = 12_000;
/** When joining mid-match, only graphics from the last this-many match seconds may air. */
const JOIN_WINDOW_S = 20;
const TICK_MS = 200;

type OnAir = { id: string; since: number; priority: number };
type Queued = { id: string; queuedAt: number; priority: number; start: number };

/**
 * Producer-side playout. The backend schedules graphics in match time; this
 * turns that into on-air time for the viewer, holding each graphic long enough
 * to read even when the replay runs at 60x, and letting goals cut in.
 */
export function useOverlayDirector(): Record<OverlaySlot, string | null> {
  const [onAir, setOnAir] = useState<Record<OverlaySlot, string | null>>({ lower: null, upper_right: null, banner: null });
  const seen = useRef(new Set<string>());
  const air = useRef<Record<OverlaySlot, OnAir | null>>({ lower: null, upper_right: null, banner: null });
  const queue = useRef<Record<OverlaySlot, Queued[]>>({ lower: [], upper_right: [], banner: [] });
  const joinedAt = useRef<number | null>(null);
  const matchId = useRef<string | null>(null);

  useEffect(() => {
    const tick = () => {
      const s = useLive.getState();
      const elapsed = s.frame?.elapsed ?? s.state?.elapsed;
      if (elapsed === undefined || !s.meta) return;
      if (matchId.current !== s.meta.match_id) {
        matchId.current = s.meta.match_id;
        joinedAt.current = elapsed;
        seen.current.clear();
        air.current = { lower: null, upper_right: null, banner: null };
        queue.current = { lower: [], upper_right: [], banner: [] };
      }
      if (elapsed < (joinedAt.current ?? 0)) joinedAt.current = elapsed; // replay restarted
      const now = performance.now();
      const matchSpeed = 1000 / Math.max(16, s.frameIntervalMs); // match seconds per wall second

      for (const o of Object.values(s.overlays)) {
        if (seen.current.has(o.overlay_id) || o.stage === "retracted" || o.start_elapsed > elapsed) continue;
        seen.current.add(o.overlay_id);
        if (o.start_elapsed < (joinedAt.current ?? 0) - JOIN_WINDOW_S) continue;
        queue.current[o.slot].push({ id: o.overlay_id, queuedAt: now, priority: o.priority, start: o.start_elapsed });
      }

      let changed = false;
      for (const slot of SLOTS) {
        const q = queue.current[slot]
          .filter((item) => now - item.queuedAt < MAX_QUEUE_MS && s.overlays[item.id]?.stage !== "retracted")
          .sort((a, b) => a.priority - b.priority || a.start - b.start);
        queue.current[slot] = q;
        const current = air.current[slot];
        if (current) {
          const overlay = s.overlays[current.id];
          const held = now - current.since;
          const wantHold = overlay ? Math.max(MIN_HOLD_MS, (overlay.duration_s / matchSpeed) * 1000) : 0;
          const preempt = q[0] && q[0].priority < current.priority && held >= MIN_BEFORE_PREEMPT_MS;
          if (!overlay || overlay.stage === "retracted" || held >= wantHold || preempt) {
            air.current[slot] = null;
            changed = true;
          }
        }
        if (!air.current[slot] && q.length) {
          const next = q.shift()!;
          air.current[slot] = { id: next.id, since: now, priority: next.priority };
          changed = true;
        }
      }
      if (changed) {
        setOnAir({
          lower: air.current.lower?.id ?? null,
          upper_right: air.current.upper_right?.id ?? null,
          banner: air.current.banner?.id ?? null,
        });
      }
    };
    const timer = setInterval(tick, TICK_MS);
    return () => clearInterval(timer);
  }, []);

  return onAir;
}

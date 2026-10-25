"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { useMoment } from "@/features/live/queries";
import type { Point } from "@/features/live/live-store";
import { copy } from "@/lib/copy";
import type { Drawing, Frame, Moment } from "@/lib/contract";

import { useReplay } from "./replay-store";

/** Wall time per 1 Hz tracking frame: a little slower than the live feed at demo speed, so it reads as a replay. */
export const REPLAY_FRAME_MS = 700;
/** Extra steps on the last frame, with every stroke drawn, so the finish can be read before going back to live. */
const HOLD_STEPS = 6;
const TRAIL = 7;

export type InstantReplay = {
  moment: Moment;
  frame: Frame | null;
  trail: Point[];
  drawings: Drawing[];
  /** Match seconds since the replay window started, for timing strokes. */
  t: number;
  progress: number;
};

/**
 * Plays the requested moment frame by frame. Returns null while live (or while
 * the moment loads), so the monitor shows the live pitch.
 */
export function useInstantReplay(): InstantReplay | null {
  const request = useReplay((s) => s.request);
  const stop = useReplay((s) => s.stop);
  const moment = useMoment(request?.insightId ?? null);
  const [pos, setPos] = useState({ token: -1, step: 0 });
  const data = moment.data;
  const step = request && pos.token === request.token ? pos.step : 0;
  const total = data ? data.frames.length + HOLD_STEPS : 0;

  useEffect(() => {
    if (!request || !data) return;
    const id = setInterval(() => {
      setPos((p) => ({ token: request.token, step: (p.token === request.token ? p.step : 0) + 1 }));
    }, REPLAY_FRAME_MS);
    return () => clearInterval(id);
  }, [request, data]);

  useEffect(() => {
    if (request && data && step >= total) stop();
  }, [request, data, step, total, stop]);

  useEffect(() => {
    if (!request || !moment.isError) return;
    toast.error(copy.instantReplay.failed);
    stop();
  }, [request, moment.isError, stop]);

  return useMemo(() => {
    if (!request || !data || !data.frames.length) return null;
    const index = Math.min(step, data.frames.length - 1);
    const frame = data.frames[index];
    const trail: Point[] = [];
    for (const f of data.frames.slice(Math.max(0, index - TRAIL + 1), index + 1)) {
      const ball = f.entities.find((e) => e.entity_id === "ball");
      if (ball) trail.push({ x: ball.x, y: ball.y });
    }
    const holding = step >= data.frames.length - 1;
    const t = holding ? Infinity : frame.elapsed - data.start_elapsed;
    return {
      moment: data,
      frame,
      trail,
      drawings: data.drawings.filter((d) => d.at_s <= t),
      t,
      progress: Math.min(1, step / Math.max(1, total - 1)),
    };
  }, [request, data, step, total]);
}

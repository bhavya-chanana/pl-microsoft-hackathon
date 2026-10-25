"use client";

import { create } from "zustand";

export type ReplayRequest = { insightId: string; token: number };

type ReplayStore = {
  /** The moment being replayed on the monitor, or null for live. */
  request: ReplayRequest | null;
  /**
   * Replay a moment (a goal, a pin on the pulse, a moment the pundit cited).
   * `reveal` scrolls the monitor into view, for replays the fan asked for.
   */
  play: (insightId: string, options?: { reveal?: boolean }) => void;
  stop: () => void;
};

let token = 0;

export const useReplay = create<ReplayStore>()((set) => ({
  request: null,
  play: (insightId, options) => {
    set({ request: { insightId, token: ++token } });
    if (!options?.reveal) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.querySelector("[data-monitor]")?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  },
  stop: () => set({ request: null }),
}));

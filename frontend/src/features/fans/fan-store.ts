"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

/** Picks older than this many runs are forgotten. */
const KEEP_RUNS = 5;

type FanStore = {
  /** Anonymous, random, and only ever sent to the backend, which stores a salted hash of it. */
  voterId: string;
  /** Your poll picks and player scores, keyed by run, so a reload still shows them. */
  picks: Record<string, Record<string, string>>;
  scores: Record<string, Record<string, number>>;
  ensureVoterId: () => string;
  pick: (runId: string, pollId: string, optionId: string) => void;
  score: (runId: string, playerId: string, value: number) => void;
};

function keepRecent<T>(byRun: Record<string, T>, runId: string, value: T): Record<string, T> {
  const kept = Object.entries(byRun)
    .filter(([id]) => id !== runId)
    .slice(-(KEEP_RUNS - 1));
  return { ...Object.fromEntries(kept), [runId]: value };
}

export const useFans = create<FanStore>()(
  persist(
    (set, get) => ({
      voterId: "",
      picks: {},
      scores: {},
      ensureVoterId: () => {
        let id = get().voterId;
        if (!id) {
          id = crypto.randomUUID();
          set({ voterId: id });
        }
        return id;
      },
      pick: (runId, pollId, optionId) =>
        set((s) => ({ picks: keepRecent(s.picks, runId, { ...s.picks[runId], [pollId]: optionId }) })),
      score: (runId, playerId, value) =>
        set((s) => ({ scores: keepRecent(s.scores, runId, { ...s.scores[runId], [playerId]: value }) })),
    }),
    { name: "matchlens.fans.v1", version: 1, skipHydration: true },
  ),
);

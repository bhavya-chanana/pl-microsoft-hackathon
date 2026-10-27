"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { CoachBrief } from "@/lib/contract";

type Cached = { brief: CoachBrief; at: number; prev: CoachBrief | null };

type CoachState = {
  /** Which side the bench is coaching; null until the first match arrives. */
  teamId: string | null;
  /** Coach view: bench-level numbers, no fan votes. */
  coachMode: boolean;
  /** Last brief per team+language, shown instantly while a fresh one loads. */
  briefs: Record<string, Cached>;
  pinned: string[];
  dismissed: string[];
  /** Action cards opened to show Evidence and Trade-off. */
  expanded: string[];
  toggleExpanded: (id: string) => void;
  setTeam: (id: string) => void;
  setCoachMode: (on: boolean) => void;
  save: (key: string, brief: CoachBrief) => void;
  togglePin: (id: string) => void;
  dismiss: (id: string) => void;
  restore: () => void;
};

const sig = (b: CoachBrief) => b.items.map((i) => `${i.issue_id}:${i.rank}:${i.confidence}`).join("|");

export const useCoach = create<CoachState>()(
  persist(
    (set, get) => ({
      teamId: null,
      coachMode: false,
      briefs: {},
      pinned: [],
      dismissed: [],
      expanded: [],
      toggleExpanded: (id) => set({ expanded: get().expanded.includes(id) ? get().expanded.filter((p) => p !== id) : [...get().expanded, id] }),
      setTeam: (teamId) => set({ teamId }),
      setCoachMode: (coachMode) => set({ coachMode }),
      save: (key, brief) => {
        const old = get().briefs[key];
        if (old && sig(old.brief) === sig(brief)) {
          set({ briefs: { ...get().briefs, [key]: { ...old, brief, at: Date.now() } } });
          return;
        }
        set({ briefs: { ...get().briefs, [key]: { brief, at: Date.now(), prev: old?.brief ?? null } } });
      },
      togglePin: (id) => set({ pinned: get().pinned.includes(id) ? get().pinned.filter((p) => p !== id) : [...get().pinned, id] }),
      dismiss: (id) => set({ dismissed: [...get().dismissed, id] }),
      restore: () => set({ dismissed: [] }),
    }),
    {
      name: "matchlens.coach.v1",
      version: 1,
      partialize: (s) => ({ coachMode: s.coachMode, briefs: s.briefs, pinned: s.pinned, dismissed: s.dismissed, expanded: s.expanded }),
      skipHydration: true,
    },
  ),
);

export const briefKey = (runId: string, teamId: string, language: string) => `${runId}:${teamId}:${language}`;

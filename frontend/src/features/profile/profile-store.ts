"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";

import type { FanProfile, Language, Metric, Mode } from "@/lib/contract";

type ProfileStore = FanProfile & {
  setMode: (mode: Mode) => void;
  setLanguage: (language: Language) => void;
  setClub: (clubId: string | null) => void;
  setPlayer: (playerId: string | null) => void;
  setMetric: (metric: Metric | null) => void;
  /** Make this the favourite player and switch the feed to player focus. */
  follow: (playerId: string) => void;
};

/** The fan's personalisation choices; persisted so a returning fan keeps their view. */
export const useProfile = create<ProfileStore>()(
  persist(
    (set) => ({
      mode: "casual",
      language: "en",
      clubId: null,
      playerId: null,
      metric: null,
      setMode: (mode) => set({ mode }),
      setLanguage: (language) => set({ language }),
      setClub: (clubId) => set({ clubId }),
      setPlayer: (playerId) => set({ playerId }),
      setMetric: (metric) => set({ metric }),
      follow: (playerId) => set({ playerId, mode: "player_focus" }),
    }),
    { name: "matchlens.profile.v1", version: 1, skipHydration: true },
  ),
);

/** Just the profile fields, for query keys and request parameters. */
export function useFanProfile(): FanProfile {
  return useProfile(
    useShallow((s) => ({ mode: s.mode, language: s.language, clubId: s.clubId, playerId: s.playerId, metric: s.metric })),
  );
}

export function profileQuery(p: FanProfile) {
  return { mode: p.mode, language: p.language, club_id: p.clubId, player_id: p.playerId, metric: p.metric };
}

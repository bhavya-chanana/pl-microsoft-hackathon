"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { FanProfile, Language, Mode } from "@/lib/contract";

type ProfileStore = FanProfile & {
  setMode: (mode: Mode) => void;
  setLanguage: (language: Language) => void;
  setClub: (clubId: string | null) => void;
  setPlayer: (playerId: string | null) => void;
};

/** The fan's personalisation choices; persisted so a returning fan keeps their view. */
export const useProfile = create<ProfileStore>()(
  persist(
    (set) => ({
      mode: "casual",
      language: "en",
      clubId: null,
      playerId: null,
      setMode: (mode) => set({ mode }),
      setLanguage: (language) => set({ language }),
      setClub: (clubId) => set({ clubId }),
      setPlayer: (playerId) => set({ playerId }),
    }),
    { name: "matchlens.profile.v1", version: 1, skipHydration: true },
  ),
);

export function profileQuery(p: FanProfile) {
  return { mode: p.mode, language: p.language, club_id: p.clubId, player_id: p.playerId };
}

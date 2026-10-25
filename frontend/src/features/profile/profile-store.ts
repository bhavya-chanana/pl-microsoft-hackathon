"use client";

import { createContext, useContext } from "react";
import { createStore, type StoreApi, useStore } from "zustand";
import { persist } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";

import type { FanProfile, Language, Metric, Mode } from "@/lib/contract";

export type ProfileStore = FanProfile & {
  setMode: (mode: Mode) => void;
  setLanguage: (language: Language) => void;
  setClub: (clubId: string | null) => void;
  setPlayer: (playerId: string | null) => void;
  setMetric: (metric: Metric | null) => void;
  /** Make this the favourite player and switch the feed to player focus. */
  follow: (playerId: string) => void;
};

const DEFAULT_PROFILE: FanProfile = { mode: "casual", language: "en", clubId: null, playerId: null, metric: null };

function actions(set: StoreApi<ProfileStore>["setState"]) {
  return {
    setMode: (mode: Mode) => set({ mode }),
    setLanguage: (language: Language) => set({ language }),
    setClub: (clubId: string | null) => set({ clubId }),
    setPlayer: (playerId: string | null) => set({ playerId }),
    setMetric: (metric: Metric | null) => set({ metric }),
    follow: (playerId: string) => set({ playerId, mode: "player_focus" }),
  };
}

/** The fan's own choices; persisted so a returning fan keeps their view. */
export const profileStore = createStore<ProfileStore>()(
  persist((set) => ({ ...DEFAULT_PROFILE, ...actions(set) }), {
    name: "matchlens.profile.v1",
    version: 1,
    skipHydration: true,
  }),
);

/** A throwaway profile for one view (a Multiverse pane), never persisted. */
export function createProfileStore(initial: Partial<FanProfile>): StoreApi<ProfileStore> {
  return createStore<ProfileStore>()((set) => ({ ...DEFAULT_PROFILE, ...initial, ...actions(set) }));
}

/** Components below a provider read and change that view's profile instead of the fan's own. */
export const ProfileScopeContext = createContext<StoreApi<ProfileStore> | null>(null);

export function useProfileStore(): StoreApi<ProfileStore> {
  return useContext(ProfileScopeContext) ?? profileStore;
}

export function useProfile(): ProfileStore;
export function useProfile<T>(selector: (s: ProfileStore) => T): T;
export function useProfile<T>(selector?: (s: ProfileStore) => T) {
  return useStore(useProfileStore(), selector ?? ((s: ProfileStore) => s as unknown as T));
}

/** Just the profile fields, for query keys and request parameters. */
export function useFanProfile(): FanProfile {
  return useProfile(
    useShallow((s) => ({ mode: s.mode, language: s.language, clubId: s.clubId, playerId: s.playerId, metric: s.metric })),
  );
}

export function profileQuery(p: FanProfile) {
  return { mode: p.mode, language: p.language, club_id: p.clubId, player_id: p.playerId, metric: p.metric };
}

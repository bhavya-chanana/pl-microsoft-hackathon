"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

type Prefs = {
  /** Toast a goal when you are on another page. */
  notifyGoals: boolean;
  setNotifyGoals: (value: boolean) => void;
};

export const usePrefs = create<Prefs>()(
  persist(
    (set) => ({ notifyGoals: true, setNotifyGoals: (notifyGoals) => set({ notifyGoals }) }),
    { name: "matchlens.prefs.v1", version: 1, skipHydration: true },
  ),
);

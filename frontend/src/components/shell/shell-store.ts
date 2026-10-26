"use client";

import { create } from "zustand";

type ShellStore = {
  paletteOpen: boolean;
  devOpen: boolean;
  /** Developer tools are on by default in dev builds only. */
  showDev: boolean;
  setPalette: (open: boolean) => void;
  setDev: (open: boolean) => void;
  setShowDev: (show: boolean) => void;
};

export const useShell = create<ShellStore>()((set) => ({
  paletteOpen: false,
  devOpen: false,
  showDev: process.env.NODE_ENV !== "production",
  setPalette: (paletteOpen) => set({ paletteOpen }),
  setDev: (devOpen) => set({ devOpen }),
  setShowDev: (showDev) => set({ showDev }),
}));

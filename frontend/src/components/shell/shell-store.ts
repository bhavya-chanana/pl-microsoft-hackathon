"use client";

import { create } from "zustand";

type ShellStore = {
  paletteOpen: boolean;
  devOpen: boolean;
  /** Developer tools are on by default in dev builds only. */
  showDev: boolean;
  catchupOpen: boolean;
  recapOpen: boolean;
  punditOpen: boolean;
  setCatchup: (open: boolean) => void;
  setRecap: (open: boolean) => void;
  setPundit: (open: boolean) => void;
  setPalette: (open: boolean) => void;
  setDev: (open: boolean) => void;
  setShowDev: (show: boolean) => void;
};

export const useShell = create<ShellStore>()((set) => ({
  paletteOpen: false,
  devOpen: false,
  showDev: process.env.NODE_ENV !== "production",
  catchupOpen: false,
  recapOpen: false,
  punditOpen: false,
  setCatchup: (catchupOpen) => set({ catchupOpen }),
  setRecap: (recapOpen) => set({ recapOpen }),
  setPundit: (punditOpen) => set({ punditOpen }),
  setPalette: (paletteOpen) => set({ paletteOpen }),
  setDev: (devOpen) => set({ devOpen }),
  setShowDev: (showDev) => set({ showDev }),
}));

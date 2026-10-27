"use client";

import { create } from "zustand";

type ShellStore = {
  paletteOpen: boolean;
  devOpen: boolean;
  /** Developer tools show only when NEXT_PUBLIC_DEV_TOOLS=1. */
  showDev: boolean;
  catchupOpen: boolean;
  recapOpen: boolean;
  punditOpen: boolean;
  /** A player card opened from anywhere (the palette). */
  playerId: string | null;
  setPlayerId: (id: string | null) => void;
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
  showDev: process.env.NEXT_PUBLIC_DEV_TOOLS === "1",
  catchupOpen: false,
  recapOpen: false,
  punditOpen: false,
  playerId: null,
  setPlayerId: (playerId) => set({ playerId }),
  setCatchup: (catchupOpen) => set({ catchupOpen }),
  setRecap: (recapOpen) => set({ recapOpen }),
  setPundit: (punditOpen) => set({ punditOpen }),
  setPalette: (paletteOpen) => set({ paletteOpen }),
  setDev: (devOpen) => set({ devOpen }),
  setShowDev: (showDev) => set({ showDev }),
}));

"use client";

import { create } from "zustand";

type ShellStore = {
  paletteOpen: boolean;
  devOpen: boolean;
  /** Stream settings sheet, the restart confirmation, the chosen start minute and the commentary switch. */
  streamOpen: boolean;
  restartOpen: boolean;
  startMinute: number;
  listen: boolean;
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
  setStream: (open: boolean) => void;
  setRestartOpen: (open: boolean) => void;
  setStartMinute: (minute: number) => void;
  setListen: (on: boolean) => void;
  setShowDev: (show: boolean) => void;
};

export const useShell = create<ShellStore>()((set) => ({
  paletteOpen: false,
  devOpen: false,
  streamOpen: false,
  restartOpen: false,
  startMinute: 0,
  listen: false,
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
  setStream: (streamOpen) => set({ streamOpen }),
  setRestartOpen: (restartOpen) => set({ restartOpen }),
  setStartMinute: (startMinute) => set({ startMinute }),
  setListen: (listen) => set({ listen }),
  setShowDev: (showDev) => set({ showDev }),
}));

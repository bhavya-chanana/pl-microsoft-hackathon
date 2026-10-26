"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

import { cadence } from "./polling";
import type { Language } from "./i18n";
import type { MatchSummary, Persona } from "./types";

/** Mock sign-in: username + password kept in this browser only. Replace with real auth later. */
type SessionState = { username: string | null; signIn: (username: string) => void; signOut: () => void };
export const useSession = create<SessionState>()(
  persist((set) => ({ username: null, signIn: (username) => set({ username }), signOut: () => set({ username: null }) }),
    { name: "fi-session" }),
);

type PrefsState = {
  persona: Persona;
  /** Team short code (e.g. "NOR"), so it carries across fixtures. */
  favoriteTeam: string | null;
  followedPlayers: string[];
  spoilersVisible: boolean;
  language: Language;
  setLanguage: (l: Language) => void;
  /** Finished the first-run persona / club / players setup. */
  onboarded: boolean;
  setPersona: (p: Persona) => void;
  setFavoriteTeam: (team: string | null) => void;
  completeOnboarding: (choice: { persona: Persona; language: Language; favoriteTeam: string | null; followedPlayers: string[] }) => void;
  toggleFollow: (id: string) => void;
  setSpoilers: (v: boolean) => void;
};
export const usePrefs = create<PrefsState>()(
  persist((set) => ({
    persona: "casual",
    favoriteTeam: null,
    followedPlayers: [],
    spoilersVisible: false,
    onboarded: false,
    language: "en",
    setLanguage: (language) => set({ language }),
    setPersona: (persona) => set({ persona }),
    setFavoriteTeam: (favoriteTeam) => set({ favoriteTeam }),
    completeOnboarding: (choice) => set({ ...choice, onboarded: true }),
    toggleFollow: (id) => set((s) => ({
      followedPlayers: s.followedPlayers.includes(id) ? s.followedPlayers.filter((x) => x !== id) : [...s.followedPlayers, id],
    })),
    setSpoilers: (spoilersVisible) => set({ spoilersVisible }),
  }), {
    name: "fi-prefs",
    version: 3,
    // v1 stored the favourite as a side and never ran onboarding; ask again. v3 adds language.
    migrate: (old, version) => ({ ...(old as PrefsState), language: (old as Partial<PrefsState>).language ?? "en", ...(version < 2 ? { favoriteTeam: null, onboarded: false } : {}) }),
  }),
);

/** Which side of this match, if any, is the viewer's club. */
export const favoriteSide = (favoriteTeam: string | null, match: MatchSummary): "home" | "away" | null =>
  favoriteTeam === match.home.short ? "home" : favoriteTeam === match.away.short ? "away" : null;

export type ViewMode = "live" | "as_live" | "full_review";
export type WatchLayout = "immersive" | "scroll";

type PlaybackState = {
  matchId: string | null;
  mode: ViewMode;
  layout: WatchLayout;
  playheadMs: number;
  liveEdgeMs: number;
  durationMs: number;
  /** Viewing delay behind the live edge for this match. */
  delayMs: number;
  /** The backend owns the live edge (real-time clip); the client only interpolates between syncs. */
  serverClock: boolean;
  epoch: number;
  syncLive: (edgeMs: number, epoch: number) => void;
  playing: boolean;
  speed: number;
  open: (match: MatchSummary, mode?: ViewMode) => void;
  tick: (dtMs: number) => void;
  seek: (ms: number) => void;
  setPlaying: (v: boolean) => void;
  setSpeed: (v: number) => void;
  setLayout: (v: WatchLayout) => void;
  setMode: (v: ViewMode) => void;
  goLive: () => void;
};

/** One playhead per open match; Watch, Stats and player pages all read it. */
export const usePlayback = create<PlaybackState>()((set, get) => ({
  matchId: null,
  mode: "live",
  layout: "scroll",
  playheadMs: 0,
  liveEdgeMs: 0,
  durationMs: 0,
  delayMs: cadence.viewingDelayMs,
  serverClock: false,
  epoch: 0,
  playing: true,
  speed: 1,
  open: (match, mode) => {
    const s = get();
    const nextMode: ViewMode = mode ?? (match.status === "live" ? "live" : "as_live");
    if (s.matchId === match.id && (!mode || mode === s.mode) && (match.epoch ?? 0) === s.epoch) return;
    const delayMs = match.viewingDelayMs ?? cadence.viewingDelayMs;
    const clock = { delayMs, serverClock: !!match.serverClock, epoch: match.epoch ?? 0 };
    if (nextMode === "live") {
      const edge = match.liveEdgeMs ?? match.liveEdgeStartMs ?? 0;
      set({ matchId: match.id, mode: "live", liveEdgeMs: edge, playheadMs: Math.max(0, edge - delayMs),
        durationMs: match.durationMs, playing: true, ...clock });
    } else {
      set({ matchId: match.id, mode: nextMode, liveEdgeMs: match.durationMs,
        playheadMs: nextMode === "full_review" ? match.durationMs : 0, durationMs: match.durationMs, playing: nextMode === "as_live", ...clock });
    }
  },
  tick: (dt) => {
    const s = get();
    const step = dt * s.speed;
    if (s.mode === "live") {
      // A server-clock match's edge moves in real time; demo speed only lets the viewer catch up.
      const edge = Math.min(s.durationMs + s.delayMs, s.liveEdgeMs + (s.serverClock ? dt : step));
      const head = s.playing ? Math.min(edge - s.delayMs, s.playheadMs + step) : s.playheadMs;
      set({ liveEdgeMs: edge, playheadMs: Math.min(head, s.durationMs) });
    } else if (s.playing) {
      const head = Math.min(s.durationMs, s.playheadMs + step);
      set({ playheadMs: head, playing: head < s.durationMs });
    }
  },
  seek: (ms) => {
    const s = get();
    const max = s.mode === "live" ? s.liveEdgeMs - s.delayMs : s.durationMs;
    set({ playheadMs: Math.max(0, Math.min(max, ms)) });
  },
  setPlaying: (playing) => set({ playing }),
  setSpeed: (speed) => set({ speed }),
  setLayout: (layout) => set({ layout }),
  setMode: (mode) => set((s) => ({ mode, playheadMs: mode === "full_review" ? s.durationMs : s.playheadMs })),
  goLive: () => set((s) => ({ playheadMs: Math.max(0, s.liveEdgeMs - s.delayMs), playing: true })),
  syncLive: (edgeMs, epoch) => set((s) => {
    if (!s.serverClock || s.mode !== "live") return s;
    // The clip restarted on the server: start watching the new run from its beginning.
    if (epoch !== s.epoch) return { epoch, liveEdgeMs: edgeMs, playheadMs: Math.max(0, edgeMs - s.delayMs), playing: true };
    // Before the clip ends the edge keeps growing; afterwards it stays at durationMs while the delay plays out.
    const edge = edgeMs >= s.durationMs ? Math.max(s.liveEdgeMs, edgeMs) : edgeMs;
    return { liveEdgeMs: edge, playheadMs: Math.min(s.playheadMs, Math.max(0, edge - s.delayMs)) };
  }),
}));

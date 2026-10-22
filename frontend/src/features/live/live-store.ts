"use client";

import { create } from "zustand";

import type {
  Frame,
  Insight,
  MatchEvent,
  MatchMeta,
  MatchState,
  MomentumPoint,
  Overlay,
  Signal,
  StreamMessage,
} from "@/lib/contract";

export type ConnectionStatus = "connecting" | "live" | "finished" | "offline" | "no_match";

/** Event types that belong on the fan timeline (passes and carries stay in the engine). */
const KEY_EVENTS = new Set(["goal", "shot", "save", "card", "substitution", "kickoff", "half_time", "full_time"]);

type LiveStore = {
  status: ConnectionStatus;
  meta: MatchMeta | null;
  state: MatchState | null;
  timeline: MatchEvent[];
  insights: Record<string, Insight>;
  overlays: Record<string, Overlay>;
  signals: Signal[];
  momentum: MomentumPoint[];
  frame: Frame | null;
  /** Wall-clock milliseconds between the last two frames, used to tween player dots. */
  frameIntervalMs: number;
  lastFrameAt: number;
  /** Bumped whenever insights change, so the personalised feed can refresh. */
  insightsVersion: number;
  /** Bumped when a correction arrives, so the timeline can be reloaded. */
  correctionsVersion: number;
  apply: (message: StreamMessage) => void;
  setStatus: (status: ConnectionStatus) => void;
  setTimeline: (timeline: MatchEvent[]) => void;
};

const MOMENTUM_KEEP = 400;

export const useLive = create<LiveStore>()((set) => ({
  status: "connecting",
  meta: null,
  state: null,
  timeline: [],
  insights: {},
  overlays: {},
  signals: [],
  momentum: [],
  frame: null,
  frameIntervalMs: 1000,
  lastFrameAt: 0,
  insightsVersion: 0,
  correctionsVersion: 0,
  setStatus: (status) => set({ status }),
  setTimeline: (timeline) => set({ timeline: timeline.filter((e) => KEY_EVENTS.has(e.type)) }),
  apply: (message) =>
    set((s) => {
      switch (message.type) {
        case "hello":
          return {
            status: message.finished ? "finished" : "live",
            meta: message.meta,
            state: message.state,
            timeline: message.timeline.filter((e) => KEY_EVENTS.has(e.type)),
            insights: Object.fromEntries(message.insights.map((i) => [i.insight_id, i])),
            overlays: Object.fromEntries(message.overlays.map((o) => [o.overlay_id, o])),
            signals: message.signals,
            momentum: message.momentum.slice(-MOMENTUM_KEEP),
            frame: message.frame,
            insightsVersion: s.insightsVersion + 1,
          };
        case "frame": {
          const now = performance.now();
          const gap = s.lastFrameAt ? now - s.lastFrameAt : 1000;
          return {
            frame: message.frame,
            lastFrameAt: now,
            frameIntervalMs: Math.min(1000, Math.max(16, s.frameIntervalMs * 0.7 + gap * 0.3)),
          };
        }
        case "state": {
          const last = s.momentum.at(-1);
          const point = { elapsed: message.state.elapsed, index: message.state.momentum_index };
          const momentum =
            !last || point.elapsed - last.elapsed >= 30 ? [...s.momentum, point].slice(-MOMENTUM_KEEP) : s.momentum;
          return { state: message.state, momentum, status: s.status === "finished" ? "finished" : "live" };
        }
        case "event":
          return KEY_EVENTS.has(message.event.type) ? { timeline: [...s.timeline, message.event] } : {};
        case "correction":
          return { correctionsVersion: s.correctionsVersion + 1 };
        case "signal":
          return { signals: [...s.signals, message.signal] };
        case "insight":
          return {
            insights: { ...s.insights, [message.insight.insight_id]: message.insight },
            insightsVersion: s.insightsVersion + 1,
          };
        case "overlay":
          return { overlays: { ...s.overlays, [message.overlay.overlay_id]: message.overlay } };
        case "feed_status":
          if (message.status === "finished") return { status: "finished" };
          if (message.status === "no_match") return { status: "no_match" };
          if (message.status === "unreachable" || message.status === "error") return { status: "offline" };
          return {};
        case "ping":
          return {};
      }
    }),
}));

"use client";

import { useEffect, useRef, useState } from "react";

import { serverRoutes } from "@/lib/routes";

import { LIVE_POLL_MS, MOCK } from "./config";
import { LiveMessage, LiveStats, type Prediction, type TrackFrame } from "./contract";

export type FeedStatus = "connecting" | "live" | "polling";

/**
 * The live push channel for one match: the same-origin SSE bridge over the backend WebSocket. Until it delivers, and
 * whenever it drops, `status` is "polling" and the page falls back to refreshing every LIVE_POLL_MS.
 * EventSource reconnects by itself.
 */
export function useLiveFeed(matchId: string, enabled: boolean): { status: FeedStatus; frame: TrackFrame | null; prediction: Prediction | null; stats: LiveStats | null; pollMs: number | false } {
  const [status, setStatus] = useState<FeedStatus>(MOCK ? "polling" : "connecting");
  const [frame, setFrame] = useState<TrackFrame | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [stats, setStats] = useState<LiveStats | null>(null);
  const lastMessage = useRef(0);

  useEffect(() => {
    if (!enabled || MOCK) return;
    const source = new EventSource(serverRoutes.next10Live(matchId));
    source.onmessage = (event: MessageEvent<string>) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(event.data);
      } catch {
        return;
      }
      const message = LiveMessage.safeParse(parsed);
      const wrapped = parsed as { type?: string; doc?: unknown } | null;
      if (wrapped?.type === "stats" || wrapped?.type === "hello") {
        // Pipeline statistics ride on the same socket; "hello" carries none.
        const carried = wrapped.type === "stats" ? wrapped.doc : (wrapped.doc as { stats?: unknown } | null)?.stats;
        const s = carried ? LiveStats.safeParse(carried) : null;
        if (s?.success) setStats(s.data);
        if (wrapped.type === "hello") setStatus("live");
        return;
      }
      if (!message.success) {
        // A bridge status line ("unreachable") means the socket is down; hello and stats lines are not data.
        if ((parsed as { status?: string } | null)?.status === "unreachable") setStatus("polling");
        return;
      }
      lastMessage.current = Date.now();
      setStatus("live");
      if (message.data.type === "frame") setFrame(message.data.doc);
      else if (message.data.doc.kind === "next10") setPrediction(message.data.doc);
    };
    source.onerror = () => setStatus("polling");
    return () => source.close();
  }, [matchId, enabled]);

  return { status: enabled ? status : "polling", frame, prediction, stats, pollMs: enabled && status !== "live" ? LIVE_POLL_MS : false };
}

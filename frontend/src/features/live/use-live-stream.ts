"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getJson } from "@/lib/api";
import { MatchEvent, StreamMessage } from "@/lib/contract";
import { serverRoutes } from "@/lib/routes";

import { useLive } from "./live-store";

/**
 * Subscribes to the live match through the same-origin SSE bridge and feeds the
 * store. EventSource reconnects on its own; `reconnect` lets the user force it.
 */
export function useLiveStream(): { reconnect: () => void } {
  const [attempt, setAttempt] = useState(0);
  const apply = useLive((s) => s.apply);
  const setStatus = useLive((s) => s.setStatus);

  useEffect(() => {
    setStatus("connecting");
    const source = new EventSource(serverRoutes.stream);
    source.onmessage = (event: MessageEvent<string>) => {
      const parsed = StreamMessage.safeParse(JSON.parse(event.data));
      if (parsed.success) apply(parsed.data);
    };
    source.onerror = () => {
      if (useLive.getState().status !== "finished") setStatus("offline");
    };
    return () => source.close();
  }, [apply, setStatus, attempt]);

  // A correction can change who scored or what happened, so reload the timeline.
  const corrections = useLive((s) => s.correctionsVersion);
  const setTimeline = useLive((s) => s.setTimeline);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const controller = new AbortController();
    getJson("timeline", MatchEvent.array(), undefined, controller.signal)
      .then(setTimeline)
      .catch(() => undefined);
    return () => controller.abort();
  }, [corrections, setTimeline]);

  const reconnect = useCallback(() => setAttempt((n) => n + 1), []);
  return { reconnect };
}

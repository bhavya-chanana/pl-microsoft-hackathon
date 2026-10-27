"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { useShell } from "@/components/shell/shell-store";
import { useLive } from "@/features/live/live-store";
import { routes } from "@/lib/routes";

/** How long to wait for the stream to carry the new match before calling it a failure. */
const LOAD_MS = 20_000;

export type Loaded = { match_id: string; run_id?: string };

/** Resolves once the live store holds the match that was asked for; rejects if it never does. */
export function waitForMatch(target: Loaded, timeoutMs = LOAD_MS): Promise<void> {
  const before = useLive.getState().runId;
  const loaded = (s: ReturnType<typeof useLive.getState>) =>
    s.meta !== null && (target.run_id ? s.runId === target.run_id : s.meta.match_id === target.match_id && s.runId !== before);
  if (loaded(useLive.getState())) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      unsubscribe();
      reject(new Error("The match did not load"));
    }, timeoutMs);
    const unsubscribe = useLive.subscribe((s) => {
      if (!loaded(s)) return;
      clearTimeout(timer);
      unsubscribe();
      resolve();
    });
  });
}

/**
 * After a request that loads another match (a stored run, a recorded feed, a restart): wait until the
 * match is really on the stream, then close the settings and take the viewer to the Live page.
 * Throws, and goes nowhere, if it does not arrive.
 */
export function useFollowMatch() {
  const router = useRouter();
  const client = useQueryClient();
  return async (target: Loaded) => {
    await waitForMatch(target);
    const shell = useShell.getState();
    shell.setStream(false);
    shell.setRestartOpen(false);
    void client.invalidateQueries();
    router.push(routes.live);
  };
}

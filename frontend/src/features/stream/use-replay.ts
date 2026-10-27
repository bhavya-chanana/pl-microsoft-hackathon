"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { z } from "zod";

import { useShell } from "@/components/shell/shell-store";
import { useLive } from "@/features/live/live-store";
import { useFollowMatch } from "@/features/replay/follow-match";
import { postJson } from "@/lib/api";
import { ReplayStatus } from "@/lib/contract";
import { copy } from "@/lib/copy";

const KEY = ["replay-status"] as const;
const Restarted = z.object({ match_id: z.string(), run_id: z.string().optional(), speed: z.number(), source: z.string().optional() });

/** Current playback speed and pause state. An empty control call just reports them. */
export function useReplayStatus() {
  return useQuery({
    queryKey: KEY,
    queryFn: () => postJson("replay/control", {}, ReplayStatus),
    refetchInterval: 20_000,
    retry: false,
  });
}

export function useReplayControl() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { speed?: number; paused?: boolean }) => postJson("replay/control", body, ReplayStatus),
    onSuccess: (status) => client.setQueryData(KEY, status),
    onError: () => toast.error(copy.replay.failed),
  });
}

/** Restart whatever match is loaded (the demo, a Lab script, a recorded feed or a past run); follows the new run to the Live page. */
export function useRestart() {
  const client = useQueryClient();
  const follow = useFollowMatch();
  return useMutation({
    mutationFn: async (options?: { startMinute?: number }) => {
      // The speed of a finished run means nothing, so only a live match sends it (the backend then uses 15x).
      const live = useLive.getState().status === "live";
      const speed = live ? client.getQueryData<ReplayStatus>(KEY)?.speed : undefined;
      const started = await postJson("replay/restart", { speed, start_minute: options?.startMinute ?? useShell.getState().startMinute }, Restarted);
      await follow(started);
      return started;
    },
    onSuccess: () => toast.success(copy.replay.restarted),
    onError: () => toast.error(copy.replay.failed),
  });
}

/** The same state in two lengths: the short pill text and the full line in the settings sheet. */
export function useSourceLabel(): { pill: string | null; line: string } {
  const status = useLive((s) => s.status);
  const replay = useReplayStatus().data;
  if (status === "archived") return { pill: copy.connection.archived, line: copy.stream.pastMatch };
  if (status !== "live") return { pill: null, line: copy.connection.noMatch };
  if (replay?.paused) return { pill: copy.stream.paused, line: copy.stream.paused };
  if (replay && replay.speed !== 1) return { pill: copy.stream.pillReplay(replay.speed), line: copy.stream.replayAt(replay.speed) };
  return { pill: copy.connection.live, line: copy.stream.live };
}

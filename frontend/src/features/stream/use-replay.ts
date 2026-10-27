"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { z } from "zod";

import { useShell } from "@/components/shell/shell-store";
import { useLive } from "@/features/live/live-store";
import { postJson } from "@/lib/api";
import { ReplayStatus } from "@/lib/contract";
import { copy } from "@/lib/copy";

const KEY = ["replay-status"] as const;
const Started = z.object({ match_id: z.string(), speed: z.number() });

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

/** Restart the demo match from the chosen minute (the caller confirms first). */
export function useRestart() {
  const client = useQueryClient();
  const restart = useMutation({
    mutationFn: () => {
      const speed = client.getQueryData<ReplayStatus>(KEY)?.speed ?? 15;
      return postJson("replay", { seed: 7, speed, start_minute: useShell.getState().startMinute }, Started);
    },
    onSuccess: () => {
      useShell.getState().setRestartOpen(false);
      void client.invalidateQueries();
      toast.success(copy.replay.restarted);
    },
    onError: () => toast.error(copy.replay.failed),
  });
  return restart;
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

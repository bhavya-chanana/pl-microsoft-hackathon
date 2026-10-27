"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, RotateCcw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { postJson } from "@/lib/api";
import { ReplayStatus } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const SPEEDS = [1, 5, 15, 30, 60] as const;
const STARTS = [0, 20, 45, 60, 75] as const;
const Started = z.object({ match_id: z.string(), speed: z.number() });

/** Demo-only replay controls for the synthetic match. */
export function ReplayControls({ className }: { className?: string }) {
  const [speed, setSpeed] = useState<number>(15);
  const [start, setStart] = useState<number>(0);
  const [paused, setPaused] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const client = useQueryClient();

  const restart = useMutation({
    mutationFn: () => postJson("replay", { seed: 7, speed, start_minute: start }, Started),
    onSuccess: () => {
      setConfirming(false);
      setPaused(false);
      void client.invalidateQueries();
      toast.success(copy.replay.restarted);
    },
    onError: () => toast.error(copy.replay.failed),
  });
  const control = useMutation({
    mutationFn: (body: { speed?: number; paused?: boolean }) => postJson("replay/control", body, ReplayStatus),
    onSuccess: (status) => setPaused(status.paused),
    onError: () => toast.error(copy.replay.failed),
  });

  return (
    <section aria-labelledby="replay-heading" className={cn("flex flex-col gap-4 rounded-lg border bg-card p-4", className)}>
      <h2 id="replay-heading" className="font-display text-lg font-semibold">
        {copy.replay.heading}
      </h2>
      <div className="flex flex-col gap-1.5">
        <span id="speed-label" className="text-xs font-medium text-muted-foreground">
          {copy.replay.speed}
        </span>
        <ToggleGroup
          aria-labelledby="speed-label"
          value={[String(speed)]}
          onValueChange={(v: string[]) => {
            if (!v[0]) return;
            setSpeed(Number(v[0]));
            control.mutate({ speed: Number(v[0]) });
          }}
          variant="outline"
          spacing={0}
          className="w-full"
        >
          {SPEEDS.map((s) => (
            <ToggleGroupItem key={s} value={String(s)} className="h-11 flex-1 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
              {copy.replay.speedValue(s)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <div className="flex flex-col gap-1.5">
        <span id="start-label" className="text-xs font-medium text-muted-foreground">
          {copy.replay.from}
        </span>
        <ToggleGroup
          aria-labelledby="start-label"
          value={[String(start)]}
          onValueChange={(v: string[]) => v[0] && setStart(Number(v[0]))}
          variant="outline"
          spacing={0}
          className="w-full"
        >
          {STARTS.map((m) => (
            <ToggleGroupItem key={m} value={String(m)} className="h-11 flex-1 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
              {m}&apos;
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" className="h-11 text-destructive" onClick={() => setConfirming(true)} disabled={restart.isPending}>
          <RotateCcw aria-hidden className={cn(restart.isPending && "motion-safe:animate-spin")} />
          {restart.isPending ? copy.replay.restarting : copy.replay.restart}
        </Button>
        <Button
          variant="outline"
          className="h-11"
          onClick={() => control.mutate({ paused: !paused })}
          disabled={control.isPending}
        >
          {paused ? <Play aria-hidden /> : <Pause aria-hidden />}
          {paused ? copy.replay.resume : copy.replay.pause}
        </Button>
      </div>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{copy.replay.confirmTitle}</DialogTitle>
            <DialogDescription>{copy.replay.confirmBody}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" className="h-11" onClick={() => setConfirming(false)}>
              {copy.replay.cancel}
            </Button>
            <Button className="h-11" onClick={() => restart.mutate()} disabled={restart.isPending}>
              {restart.isPending ? copy.replay.restarting : copy.replay.restart}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

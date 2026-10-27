"use client";

import { Pause, Play, RotateCcw } from "lucide-react";

import { useShell } from "@/components/shell/shell-store";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ListenToggle, Narrator } from "@/features/companion/narration";
import { LanguageField } from "@/features/profile/profile-panel";
import { PastMatches } from "@/features/replay/past-matches";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { useReplayControl, useReplayStatus, useRestart, useSourceLabel } from "./use-replay";

const SPEEDS = [1, 5, 15, 30, 60] as const;
const STARTS = [0, 20, 45, 60, 75] as const;
const tile = "h-11 flex-1 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground";

function Section({ title, help, children }: { title: string; help?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t pt-4 first:border-t-0 first:pt-0">
      <div className="flex flex-col gap-0.5">
        <h3 className="font-display text-base font-semibold">{title}</h3>
        {help && <p className="text-[13px] text-muted-foreground">{help}</p>}
      </div>
      {children}
    </section>
  );
}

function Playback() {
  const status = useReplayStatus();
  const control = useReplayControl();
  const restart = useRestart();
  const startMinute = useShell((s) => s.startMinute);
  const setStartMinute = useShell((s) => s.setStartMinute);
  const speed = status.data?.speed;
  const paused = status.data?.paused ?? false;
  return (
    <div className="flex flex-col gap-4">
      {status.isError && <p className="rounded-lg border border-dashed p-3 text-[13px] text-muted-foreground">{copy.stream.unavailable}</p>}
      <div className="flex flex-col gap-1.5">
        <span id="speed-label" className="text-[13px] font-medium text-muted-foreground">{copy.replay.speed}</span>
        <ToggleGroup
          aria-labelledby="speed-label"
          value={speed === undefined ? [] : [String(speed)]}
          onValueChange={(v: string[]) => v[0] && control.mutate({ speed: Number(v[0]) })}
          variant="outline"
          spacing={0}
          className="w-full"
          disabled={status.isError}
        >
          {SPEEDS.map((s) => (
            <ToggleGroupItem key={s} value={String(s)} className={tile}>{copy.replay.speedValue(s)}</ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <div className="flex flex-col gap-1.5">
        <span id="start-label" className="text-[13px] font-medium text-muted-foreground">{copy.replay.from}</span>
        <ToggleGroup
          aria-labelledby="start-label"
          value={[String(startMinute)]}
          onValueChange={(v: string[]) => v[0] && setStartMinute(Number(v[0]))}
          variant="outline"
          spacing={0}
          className="w-full"
        >
          {STARTS.map((m) => (
            <ToggleGroupItem key={m} value={String(m)} className={tile}>{m}&apos;</ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Button variant="outline" className="h-11" onClick={() => control.mutate({ paused: !paused })} disabled={control.isPending || status.isError}>
          {paused ? <Play aria-hidden /> : <Pause aria-hidden />}
          {paused ? copy.replay.resume : copy.replay.pause}
        </Button>
        <Button variant="secondary" className="h-11 text-destructive" onClick={() => useShell.getState().setRestartOpen(true)} disabled={restart.isPending}>
          <RotateCcw aria-hidden className={cn(restart.isPending && "motion-safe:animate-spin")} />
          {restart.isPending ? copy.replay.restarting : copy.replay.restart}
        </Button>
      </div>
    </div>
  );
}

/** Every stream setting in one place: source, playback, commentary, other matches. Also hosts the restart confirmation and the narrator. */
export function StreamHost() {
  const open = useShell((s) => s.streamOpen);
  const setOpen = useShell((s) => s.setStream);
  const confirming = useShell((s) => s.restartOpen);
  const setConfirming = useShell((s) => s.setRestartOpen);
  const listen = useShell((s) => s.listen);
  const restart = useRestart();
  const { line } = useSourceLabel();
  return (
    <>
      <Narrator enabled={listen} />
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="gap-5 overflow-y-auto overscroll-contain p-4 max-sm:w-full! max-sm:max-w-none! sm:max-w-[480px]">
          <SheetHeader className="p-0">
            <SheetTitle className="font-display text-xl">{copy.stream.title}</SheetTitle>
            <SheetDescription className="text-[13px]">
              <span className="font-medium text-foreground">{copy.stream.source}:</span> {line}
            </SheetDescription>
          </SheetHeader>
          <Section title={copy.stream.playback}>
            <Playback />
          </Section>
          <Section title={copy.stream.commentary} help={copy.stream.commentaryHelp}>
            <ListenToggle />
            <LanguageField />
          </Section>
          <Section title={copy.stream.moreSources} help={copy.stream.moreSourcesHelp}>
            <PastMatches bare />
          </Section>
        </SheetContent>
      </Sheet>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{copy.replay.confirmTitle}</DialogTitle>
            <DialogDescription>{copy.replay.confirmBody}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" className="h-11" onClick={() => setConfirming(false)}>{copy.replay.cancel}</Button>
            <Button className="h-11" onClick={() => restart.mutate()} disabled={restart.isPending}>
              {restart.isPending ? copy.replay.restarting : copy.replay.restart}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

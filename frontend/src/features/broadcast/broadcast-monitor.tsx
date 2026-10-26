"use client";

import { Radio, RotateCw, Video } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { useReconnect } from "@/features/live/live-stream-context";
import { PlayerSheet } from "@/features/player/player-sheet";
import { useProfile } from "@/features/profile/profile-store";
import { copy, signalLabels } from "@/lib/copy";
import { playerName } from "@/lib/match";
import { cn } from "@/lib/utils";

import { GoalTakeover } from "./goal-takeover";
import { MomentumBar } from "./momentum-bar";
import { OverlayLayer, PassLane, usePassLane } from "./overlay-layer";
import { Pitch } from "./pitch";
import { useReplay } from "./replay-store";
import { ScoreBug } from "./score-bug";
import { TelestratorLayer } from "./telestrator";
import { type InstantReplay, REPLAY_FRAME_MS, useInstantReplay } from "./use-instant-replay";
import { useOverlayDirector } from "./use-overlay-director";

export function FeedNotice() {
  const status = useLive((s) => s.status);
  const hasMatch = useLive((s) => s.meta !== null);
  const reconnect = useReconnect();
  if (status === "connecting" && !hasMatch) {
    return (
      <div className="absolute inset-0 grid place-items-center">
        <Skeleton className="absolute inset-[6%] rounded-sm bg-graphic-foreground/5" />
        <p className="relative text-sm text-graphic-foreground/80">{copy.connection.connecting}</p>
      </div>
    );
  }
  if (status !== "offline" && status !== "no_match") return null;
  const offline = status === "offline";
  // Keep the last picture on screen; the header pill says we are reconnecting.
  if (offline && hasMatch) return null;
  return (
    <div className="absolute inset-0 grid place-items-center bg-graphic/80 p-6 text-center text-graphic-foreground">
      <div className="flex max-w-sm flex-col items-center gap-3">
        <p className="font-display text-display font-semibold">
          {offline ? copy.connection.offline : copy.connection.noMatch}
        </p>
        <p className="text-sm text-graphic-foreground/80">
          {offline ? copy.connection.offlineHelp : copy.connection.noMatchHelp}
        </p>
        {offline && (
          <Button variant="secondary" size="lg" className="min-h-11 px-4" onClick={reconnect}>
            <RotateCw aria-hidden />
            {copy.connection.retry}
          </Button>
        )}
      </div>
    </div>
  );
}

/** Which player card is open, and the marker to send focus back to when it closes. */
function usePlayerPick() {
  const [playerId, setPlayerId] = useState<string | null>(null);
  const opener = useRef<SVGGElement | null>(null);
  const select = useCallback((id: string, el: SVGGElement) => {
    opener.current = el;
    setPlayerId(id);
  }, []);
  const close = useCallback(() => setPlayerId(null), []);
  const returnFocus = useCallback(() => {
    if (opener.current?.isConnected) opener.current.focus();
  }, []);
  return { playerId, select, close, returnFocus };
}

/** Replay bug, caption and the way back to live, while a moment is being replayed. */
function ReplayChrome({ replay, interactive }: { replay: InstantReplay; interactive: boolean }) {
  const stop = useReplay((s) => s.stop);
  const language = useProfile((s) => s.language);
  const meta = useLive((s) => s.meta);
  const { moment } = replay;
  const who = playerName(meta, moment.player_ids[0], true);
  const label = signalLabels[moment.signal_type] ?? moment.signal_type;
  return (
    <>
      <div className="absolute top-[3%] right-[3%] flex flex-col items-end gap-1">
        <div className="graphic-cut-left flex items-center gap-2 bg-telestrator py-1 pr-3 pl-5 font-display text-sm font-bold tracking-wide text-graphic uppercase @lg:text-lg">
          <Video aria-hidden className="size-4" />
          {copy.instantReplay.bug[language]}
        </div>
        <div aria-hidden className="h-1 w-full overflow-hidden rounded-full bg-graphic/60">
          <div
            className="h-full bg-telestrator transition-[width] ease-linear"
            style={{ width: `${replay.progress * 100}%`, transitionDuration: `${REPLAY_FRAME_MS}ms` }}
          />
        </div>
      </div>
      <div className="absolute inset-x-[3%] bottom-[5%] flex items-end justify-between gap-3">
        <p className="graphic-cut max-w-[70%] bg-graphic px-3 py-1.5 pr-6 font-display text-base leading-tight font-semibold text-graphic-foreground shadow-floating @lg:text-xl">
          {label}
          {who && `, ${who}`}
          <span className="ml-2 font-medium text-graphic-foreground/70 tabular-nums">{moment.minute_label}</span>
        </p>
        {interactive && (
          <Button variant="secondary" className="min-h-11 shrink-0 gap-2 px-3" onClick={stop}>
            <Radio aria-hidden />
            {copy.instantReplay.back}
          </Button>
        )}
      </div>
    </>
  );
}

/**
 * The programme monitor: live pitch with the score bug and timed graphics, at
 * 16:9 like the real output. `fill` lets the /broadcast route use the whole screen.
 * `interactive` lets fans pick players for their card (off on the clean feed).
 */
export function BroadcastMonitor({
  className,
  fill = false,
  interactive = false,
  autoReplay = true,
  compact = false,
  camera = false,
}: {
  className?: string;
  fill?: boolean;
  interactive?: boolean;
  /** Roll the replay after a goal call (one monitor per page should). */
  autoReplay?: boolean;
  /** A small monitor in a wall of screens: no momentum strip or camera switch. */
  compact?: boolean;
  /** Start on the tilted broadcast camera. */
  camera?: boolean;
}) {
  const onAir = useOverlayDirector();
  const lane = usePassLane(onAir);
  const pick = usePlayerPick();
  const replay = useInstantReplay();
  const [tilt, setTilt] = useState(camera);
  return (
    <section
      aria-label={copy.nav.broadcast}
      data-monitor={autoReplay ? "main" : undefined}
      className={cn(
        "@container relative isolate overflow-hidden bg-graphic text-graphic-foreground",
        fill ? "h-dvh w-full" : "aspect-video w-full rounded-lg shadow-floating ring-1 ring-border",
        className,
      )}
    >
      <div className="absolute inset-0 grid place-items-center p-[3%] pt-[12%] @2xl:pt-[9%]">
        <Pitch
          tilt={tilt}
          onSelectPlayer={interactive && !replay ? pick.select : undefined}
          selectedPlayerId={interactive ? pick.playerId : null}
          {...(replay && { frame: replay.frame, ballTrail: replay.trail, tweenMs: REPLAY_FRAME_MS })}
          overlay={replay && <TelestratorLayer key={replay.moment.insight_id} drawings={replay.drawings} />}
        >
          {!replay && lane && <PassLane pass={lane} />}
        </Pitch>
      </div>
      <div className="absolute top-[3%] left-[3%] flex flex-col gap-1.5">
        <ScoreBug />
        {!compact && <MomentumBar compact className="hidden w-full max-w-56 @2xl:flex" />}
      </div>
      <AnimatePresence initial={false}>
        {replay ? (
          <motion.div key="replay" className="absolute inset-0" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <ReplayChrome replay={replay} interactive={interactive} />
          </motion.div>
        ) : (
          <motion.div key="live" className="pointer-events-none absolute inset-0" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <OverlayLayer onAir={onAir} />
          </motion.div>
        )}
      </AnimatePresence>
      {interactive && !compact && !replay && (
        <Button
          variant="secondary"
          size="sm"
          aria-pressed={tilt}
          title={copy.instantReplay.cameraHelp}
          className="absolute top-[3%] right-[3%] hidden min-h-9 gap-1.5 bg-graphic/80 text-graphic-foreground hover:bg-graphic @xl:inline-flex"
          onClick={() => setTilt((t) => !t)}
        >
          <Video aria-hidden />
          {copy.instantReplay.camera}
        </Button>
      )}
      <GoalTakeover autoReplay={autoReplay} />
      <FeedNotice />
      {interactive && <PlayerSheet playerId={pick.playerId} onClose={pick.close} returnFocus={pick.returnFocus} />}
    </section>
  );
}

"use client";

import { ArrowRight, Radio, Video } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { FeedNotice } from "@/features/broadcast/broadcast-monitor";
import { useGoalCall } from "@/features/broadcast/goal-takeover";
import { useOverlayDirector } from "@/features/broadcast/use-overlay-director";
import { PassLane, usePassLane } from "@/features/broadcast/overlay-layer";
import { Pitch } from "@/features/broadcast/pitch";
import { useReplay } from "@/features/broadcast/replay-store";
import { TelestratorLayer } from "@/features/broadcast/telestrator";
import { REPLAY_FRAME_MS, useInstantReplay } from "@/features/broadcast/use-instant-replay";
import { useLive } from "@/features/live/live-store";
import { PlayerSheet } from "@/features/player/player-sheet";
import { useProfile } from "@/features/profile/profile-store";
import { copy, signalLabels } from "@/lib/copy";
import { overlayText, playerName, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";
import { useCallback, useRef } from "react";

function usePick() {
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

/** What is on air as a caption under the pitch (never over the play). Reserves its height so nothing jumps. */
function Caption({ onAir }: { onAir: Record<string, string | null> }) {
  const overlays = useLive((s) => s.overlays);
  const meta = useLive((s) => s.meta);
  const language = useProfile((s) => s.language);
  const id = onAir.banner ?? onAir.lower ?? onAir.upper_right;
  const overlay = id ? overlays[id] : undefined;
  const text = overlay ? overlayText(overlay, language) : null;
  const side = overlay && meta ? (overlay.team_id === meta.home.team_id ? "home" : overlay.team_id === meta.away.team_id ? "away" : null) : null;
  return (
    <div className="min-h-14" aria-live="polite">
      <AnimatePresence mode="wait" initial={false}>
        {overlay && text && (
          <motion.div
            key={overlay.overlay_id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="flex items-center gap-3 rounded-xl bg-raised px-3 py-2"
          >
            <span aria-hidden className={cn("h-8 w-1 shrink-0 rounded-full", side ? sideTone[side].bg : "bg-subtle")} />
            <div className="min-w-0">
              <p className="truncate text-[15px] font-semibold">{text.headline}</p>
              <p className="flex flex-wrap gap-x-3 text-[13px] text-muted-foreground">
                {text.body && <span className="truncate">{text.body}</span>}
                {overlay.stats.slice(0, 3).map((st) => (
                  <span key={st.label} className="tnum">
                    {st.label} <b className="font-semibold text-foreground">{st.value}{st.unit ?? ""}</b>
                  </span>
                ))}
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** Goal chip: bottom-left of the pitch, clear of both penalty areas, in the scorer's colour. */
function GoalChip() {
  const goal = useGoalCall(true);
  const meta = useLive((s) => s.meta);
  const side = meta && goal?.team_id ? (goal.team_id === meta.home.team_id ? "home" : "away") : "home";
  const scorer = playerName(meta, goal?.player_ids[0]);
  return (
    <AnimatePresence>
      {goal && (
        <motion.div
          key={goal.signal_id}
          role="status"
          initial={{ opacity: 0, x: -16 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0 }}
          className={cn("absolute bottom-3 left-3 flex h-10 items-center gap-2 rounded-full px-4 font-display text-lg font-bold text-background", sideTone[side].bg)}
        >
          GOAL
          <span className="font-sans text-sm font-semibold">{scorer ? `${scorer} ${goal.minute_label}` : goal.minute_label}</span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/**
 * The Live pitch: a solid card with the pitch filling it. Broadcast graphics become a caption row
 * below; replays and goal calls stay out of the penalty areas.
 */
export function LivePitch() {
  const meta = useLive((s) => s.meta);
  const possession = useLive((s) => s.state?.possession_team ?? null);
  const onAir = useOverlayDirector();
  const lane = usePassLane(onAir);
  const pick = usePick();
  const replay = useInstantReplay();
  const stop = useReplay((s) => s.stop);
  const language = useProfile((s) => s.language);
  const [tilt, setTilt] = useState(false);
  const label = replay ? (signalLabels[replay.moment.signal_type] ?? replay.moment.signal_type) : null;
  return (
    <section aria-label={copy.pitch.label} data-monitor="main" className="@container flex flex-col gap-3 rounded-2xl border bg-card p-3 shadow-raised max-md:-mx-4 max-md:rounded-none max-md:border-x-0">
      <div className="flex min-h-9 items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-9 items-center rounded-full bg-raised px-3 text-[13px] font-medium ring-1 ring-border">{copy.pitch.players}</span>
          {replay && (
            <span className="inline-flex h-9 items-center gap-1.5 rounded-full bg-telestrator px-3 font-display text-sm font-bold tracking-wide text-graphic uppercase">
              <Video aria-hidden className="size-4" />
              {copy.instantReplay.bug[language]}
            </span>
          )}
        </div>
        {replay ? (
          <Button variant="secondary" size="sm" className="h-9 gap-1.5" onClick={stop}>
            <Radio aria-hidden />
            {copy.instantReplay.back}
          </Button>
        ) : (
          <Button variant="ghost" size="sm" aria-pressed={tilt} className="h-9 gap-1.5 text-muted-foreground" onClick={() => setTilt((t) => !t)}>
            <Video aria-hidden />
            {copy.instantReplay.camera}
          </Button>
        )}
      </div>
      <div className="relative isolate aspect-[111/74] w-full overflow-hidden rounded-xl bg-turf max-md:-mx-3 max-md:w-[calc(100%+1.5rem)] max-md:rounded-none">
        <Pitch
          tilt={tilt}
          className="absolute inset-0"
          onSelectPlayer={!replay ? pick.select : undefined}
          selectedPlayerId={pick.playerId}
          {...(replay && { frame: replay.frame, ballTrail: replay.trail, tweenMs: REPLAY_FRAME_MS })}
          overlay={replay && <TelestratorLayer key={replay.moment.insight_id} drawings={replay.drawings} />}
        >
          {!replay && lane && <PassLane pass={lane} />}
        </Pitch>
        <GoalChip />
        <FeedNotice />
      </div>
      <div className="flex items-center justify-between gap-3 text-[13px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          {meta && possession && (
            <>
              <span aria-hidden className={cn("size-2 rounded-full", possession === meta.home.team_id ? "bg-home" : "bg-away")} />
              {copy.pitch.inPossession(possession === meta.home.team_id ? meta.home.name : meta.away.name)}
            </>
          )}
        </span>
        <span className="inline-flex items-center gap-1.5">
          {meta?.home.name} {copy.pitch.attack}
          <ArrowRight aria-hidden className="size-4" />
        </span>
      </div>
      {replay ? (
        <p className="min-h-14 rounded-xl bg-raised px-3 py-2 text-[15px] font-semibold">
          {label}
          {playerName(meta, replay.moment.player_ids[0], true) && `, ${playerName(meta, replay.moment.player_ids[0], true)}`}
          <span className="ml-2 text-muted-foreground tnum">{replay.moment.minute_label}</span>
        </p>
      ) : (
        <Caption onAir={onAir} />
      )}
      <PlayerSheet playerId={pick.playerId} onClose={pick.close} returnFocus={pick.returnFocus} />
    </section>
  );
}

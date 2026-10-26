"use client";

import { Pause, Play, RotateCcw, SkipBack, SkipForward } from "lucide-react";
import { useReducedMotion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { arrowHead, linePath } from "@/features/broadcast/telestrator";
import { Markings } from "@/features/broadcast/pitch";
import type { Frame, RolloutView, Situation, TacticDrawing } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

import { type Roster, nameOf, rosterOf, surname } from "./lib";

const t = copy.tactics;
const W = 105;
const H = 68;
const SPEEDS = [0.5, 1, 2] as const;

/* ------------------------------ playback ------------------------------ */

export type Playback = {
  /** Seconds into the rollout. */
  time: number;
  playing: boolean;
  speed: number;
  duration: number;
  /** Motion is reduced: no autoplay, step and scrub instead. */
  still: boolean;
  setSpeed: (speed: number) => void;
  toggle: () => void;
  seek: (seconds: number) => void;
  step: (seconds: number) => void;
};

/** A clock for a rollout: starts by itself, plays in real time (scaled by speed) and stops at the end. */
export function usePlayback(duration: number): Playback {
  const reduced = useReducedMotion() ?? false;
  const [time, setTime] = useState(0);
  const [wantsPlay, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const clock = useRef(0);
  const playing = wantsPlay && !reduced && duration > 0;

  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    let frame = requestAnimationFrame(function tick(now) {
      clock.current += ((now - last) / 1000) * speed;
      last = now;
      if (clock.current >= duration) {
        clock.current = duration;
        setTime(duration);
        setPlaying(false);
        return;
      }
      setTime(clock.current);
      frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [playing, speed, duration]);

  const toggle = useCallback(() => {
    if (clock.current >= duration) {
      clock.current = 0;
      setTime(0);
    }
    setPlaying((p) => !p);
  }, [duration]);
  const seek = useCallback(
    (seconds: number) => {
      clock.current = Math.min(duration, Math.max(0, seconds));
      setPlaying(false);
      setTime(clock.current);
    },
    [duration],
  );
  const step = useCallback(
    (seconds: number) => {
      clock.current = Math.min(duration, Math.max(0, clock.current + seconds));
      setPlaying(false);
      setTime(clock.current);
    },
    [duration],
  );
  return { time, playing, speed, duration, still: reduced, setSpeed, toggle, seek, step };
}

/* ------------------------------- tracks ------------------------------- */

type Tracks = {
  start: number;
  step: number;
  duration: number;
  ids: string[];
  xs: Map<string, number[]>;
  ys: Map<string, number[]>;
};

/** Per-entity position arrays, so any moment between two 5 Hz frames can be drawn smoothly. */
function buildTracks(frames: Frame[]): Tracks | null {
  if (frames.length < 2) return null;
  const start = frames[0].elapsed;
  const duration = frames[frames.length - 1].elapsed - start;
  const xs = new Map<string, number[]>();
  const ys = new Map<string, number[]>();
  frames.forEach((frame, i) => {
    for (const e of frame.entities) {
      if (!xs.has(e.entity_id)) {
        xs.set(e.entity_id, new Array<number>(frames.length).fill(e.x));
        ys.set(e.entity_id, new Array<number>(frames.length).fill(e.y));
      }
      xs.get(e.entity_id)![i] = e.x;
      ys.get(e.entity_id)![i] = e.y;
    }
  });
  return { start, step: duration / (frames.length - 1), duration, ids: [...xs.keys()], xs, ys };
}

export function trackDuration(rollout: RolloutView | null | undefined): number {
  const frames = rollout?.frames;
  return frames && frames.length > 1 ? frames[frames.length - 1].elapsed - frames[0].elapsed : 0;
}

function sample(tracks: Tracks, id: string, time: number): { x: number; y: number } {
  const xs = tracks.xs.get(id)!;
  const ys = tracks.ys.get(id)!;
  const f = Math.min(xs.length - 1, Math.max(0, time / tracks.step));
  const i = Math.min(xs.length - 2, Math.floor(f));
  const k = f - i;
  return { x: xs[i] + (xs[i + 1] - xs[i]) * k, y: ys[i] + (ys[i + 1] - ys[i]) * k };
}

/* ----------------------------- drawings ------------------------------ */

type XY = { x: number; y: number };
const xy = (p: [number, number]): XY => ({ x: p[0], y: p[1] });
const HALO = { paintOrder: "stroke", stroke: "var(--graphic)", strokeWidth: 0.6, strokeLinejoin: "round" } as const;

function DrawingLabel({ at, text }: { at: XY; text: string }) {
  return (
    <text x={at.x} y={at.y} textAnchor="middle" className="fill-telestrator font-display font-semibold" style={{ fontSize: 2.1, ...HALO }}>
      {text}
    </text>
  );
}

function Stroke({ d, width, dashed, accent }: { d: string; width: number; dashed?: boolean; accent: boolean }) {
  return (
    <>
      <path d={d} className="fill-none stroke-graphic" strokeOpacity={0.45} strokeWidth={width + 0.5} strokeLinecap="round" strokeLinejoin="round" />
      <path
        d={d}
        className={cn("fill-none", accent ? "stroke-telestrator" : "stroke-graphic-foreground")}
        strokeWidth={width}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={dashed ? "1.2 1" : undefined}
      />
    </>
  );
}

function DrawingMark({ drawing }: { drawing: TacticDrawing }) {
  const pts = drawing.points.map(xy);
  const first = pts[0];
  const last = pts[pts.length - 1];
  if (!first || !last) return null;
  const mid = pts[Math.floor(pts.length / 2)];
  switch (drawing.kind) {
    case "zone": {
      if (pts.length < 3) {
        return (
          <g>
            <circle cx={first.x} cy={first.y} r={7} className="fill-telestrator stroke-telestrator" fillOpacity={0.14} strokeWidth={0.35} strokeDasharray="1.4 1" />
            {drawing.label && <DrawingLabel at={{ x: first.x, y: first.y - 8 }} text={drawing.label} />}
          </g>
        );
      }
      const top = pts.reduce((a, b) => (b.y < a.y ? b : a));
      const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
      return (
        <g>
          <polygon
            points={pts.map((p) => `${p.x},${p.y}`).join(" ")}
            className="fill-telestrator stroke-telestrator"
            fillOpacity={0.14}
            strokeWidth={0.35}
            strokeDasharray="1.4 1"
            strokeLinejoin="round"
          />
          {drawing.label && <DrawingLabel at={{ x: cx, y: top.y - 1 }} text={drawing.label} />}
        </g>
      );
    }
    case "shot":
    case "arrow":
    case "lane":
    case "path": {
      const prev = pts[pts.length - 2] ?? first;
      const accent = drawing.kind !== "lane";
      const width = drawing.kind === "shot" ? 0.75 : drawing.kind === "lane" ? 0.35 : 0.5;
      return (
        <g>
          <Stroke d={linePath(pts)} width={width} dashed={drawing.kind === "path"} accent={accent} />
          {pts.length > 1 && <Stroke d={arrowHead(prev, last, drawing.kind === "shot" ? 2.4 : 1.7)} width={width} accent={accent} />}
          {drawing.label && <DrawingLabel at={{ x: mid.x, y: mid.y - 1.8 }} text={drawing.label} />}
        </g>
      );
    }
  }
}

/** The intent: runs, passing lanes and target zones, in pitch metres. */
function IntentLayer({ drawings }: { drawings: TacticDrawing[] }) {
  return (
    <g aria-hidden className="pointer-events-none motion-safe:animate-in motion-safe:fade-in motion-safe:duration-500">
      {drawings.map((d, i) => (
        <DrawingMark key={`${d.kind}-${i}`} drawing={d} />
      ))}
    </g>
  );
}

/* ------------------------------- pitch -------------------------------- */

type GhostPitchProps = {
  situation: Situation;
  rollout: RolloutView | null;
  /** Seconds into the rollout. */
  time: number;
  /** Players the idea names; ringed and labelled. */
  highlightIds?: string[];
  label: string;
  className?: string;
};

const TRAIL_SECONDS = 1.2;

/**
 * The tactics pitch: players glide between 5 Hz frames, the moment they started from stays faded behind them,
 * and the idea's runs and lanes are drawn on top.
 */
export function GhostPitch({ situation, rollout, time, highlightIds = [], label, className }: GhostPitchProps) {
  const roster = useMemo<Roster>(() => rosterOf(situation), [situation]);
  const tracks = useMemo(() => (rollout ? buildTracks(rollout.frames) : null), [rollout]);
  const highlighted = useMemo(() => new Set(highlightIds), [highlightIds]);
  const coaching = situation.coaching_team_id;

  const starts = useMemo(() => {
    const m = new Map<string, XY>();
    for (const e of situation.frame.entities) m.set(e.entity_id, { x: e.x, y: e.y });
    return m;
  }, [situation]);

  const at = (id: string): XY | null => {
    if (tracks && tracks.xs.has(id)) return sample(tracks, id, Math.min(time, tracks.duration));
    return starts.get(id) ?? null;
  };

  const ids = tracks ? tracks.ids : [...starts.keys()];
  const players = ids.filter((id) => id !== "ball" && roster.has(id));
  const ball = at("ball");
  const trail: XY[] = [];
  if (tracks && tracks.xs.has("ball")) {
    const from = Math.max(0, time - TRAIL_SECONDS);
    for (let s = from; s < time; s += tracks.step * 2) trail.push(sample(tracks, "ball", s));
  }

  return (
    <svg viewBox={`-3 -3 ${W + 6} ${H + 6}`} role="img" aria-label={label} className={cn("block h-full w-full", className)} preserveAspectRatio="xMidYMid meet">
      <defs>
        <pattern id="ghost-turf-stripes" width={W / 6} height={H} patternUnits="userSpaceOnUse">
          <rect width={W / 12} height={H} className="fill-turf" />
          <rect x={W / 12} width={W / 12} height={H} className="fill-turf-stripe" />
        </pattern>
      </defs>
      <g aria-hidden>
        <rect x={-3} y={-3} width={W + 6} height={H + 6} rx={1} fill="url(#ghost-turf-stripes)" />
        <Markings />
        {/* Where everyone started: a faded ring, joined to the player once they have moved. */}
        {players.map((id) => {
          const from = starts.get(id);
          const now = at(id);
          if (!from || !now) return null;
          const tone = sideTone[roster.get(id)!.team_id === coaching ? "home" : "away"];
          const moved = Math.hypot(now.x - from.x, now.y - from.y) > 1.5;
          return (
            <g key={`start-${id}`} className="pointer-events-none">
              {moved && <line x1={from.x} y1={from.y} x2={now.x} y2={now.y} className={cn(tone.stroke)} strokeWidth={0.3} strokeDasharray="0.8 0.8" strokeOpacity={0.55} />}
              <circle cx={from.x} cy={from.y} r={1.55} className={cn("fill-none", tone.stroke)} strokeWidth={0.35} strokeDasharray="0.7 0.5" strokeOpacity={0.5} />
            </g>
          );
        })}
        {rollout && <IntentLayer drawings={rollout.drawings} />}
        {trail.length > 1 && (
          <polyline
            points={trail.map((p) => `${p.x},${p.y}`).join(" ")}
            className="fill-none stroke-graphic-foreground"
            strokeWidth={0.3}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeOpacity={0.4}
            strokeDasharray="0.6 0.6"
          />
        )}
        {players.map((id) => {
          const p = at(id);
          const spot = roster.get(id);
          if (!p || !spot) return null;
          const tone = sideTone[spot.team_id === coaching ? "home" : "away"];
          const named = highlighted.has(id);
          return (
            <g key={id} transform={`translate(${p.x} ${p.y})`}>
              {named && <circle r={2.8} className="fill-none stroke-telestrator" strokeWidth={0.5} />}
              <circle r={1.55} className={cn(tone.fill, "stroke-graphic")} strokeWidth={0.25} />
              {named && (
                <text y={-3.6} textAnchor="middle" className="fill-graphic-foreground font-display font-semibold" style={{ fontSize: 1.9, ...HALO }}>
                  {surname(spot.name)}
                </text>
              )}
            </g>
          );
        })}
        {ball && <circle cx={ball.x} cy={ball.y} r={0.75} className="fill-graphic-foreground stroke-graphic" strokeWidth={0.2} />}
      </g>
    </svg>
  );
}

/* ------------------------------ controls ------------------------------ */

export function PlaybackControls({ playback, rollout, roster }: { playback: Playback; rollout: RolloutView | null; roster: Roster }) {
  const p = playback;
  const t0 = rollout?.frames[0]?.elapsed ?? 0;
  const atEnd = p.time >= p.duration && p.duration > 0;
  const ticks = (rollout?.events ?? []).filter((e) => e.type === "shot" || e.type === "goal" || e.type === "interception" || e.type === "turnover");
  const disabled = p.duration === 0;
  return (
    <div role="group" aria-label={t.playback.label} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="secondary"
          className="h-11 min-w-11 gap-2 px-3"
          onClick={p.toggle}
          disabled={disabled || p.still}
          aria-label={p.playing ? t.playback.pause : atEnd ? t.playback.restart : t.playback.play}
        >
          {p.playing ? <Pause aria-hidden /> : atEnd ? <RotateCcw aria-hidden /> : <Play aria-hidden />}
          <span className="hidden sm:inline">{p.playing ? t.playback.pause : atEnd ? t.playback.restart : t.playback.play}</span>
        </Button>
        <Button variant="outline" size="icon" className="size-11" onClick={() => p.step(-1)} disabled={disabled} aria-label={t.playback.back}>
          <SkipBack aria-hidden />
        </Button>
        <Button variant="outline" size="icon" className="size-11" onClick={() => p.step(1)} disabled={disabled} aria-label={t.playback.forward}>
          <SkipForward aria-hidden />
        </Button>
        <span className="font-display text-base font-semibold tabular-nums" aria-hidden>
          {t.playback.time(p.time, p.duration)}
        </span>
        <ToggleGroup
          aria-label={t.playback.speed}
          value={[String(p.speed)]}
          onValueChange={(v) => v[0] && p.setSpeed(Number(v[0]))}
          variant="outline"
          className="ml-auto"
        >
          {SPEEDS.map((s) => (
            <ToggleGroupItem key={s} value={String(s)} className="h-11 min-w-11 px-2 tabular-nums aria-pressed:bg-primary aria-pressed:text-primary-foreground">
              {t.playback.speedValue(s)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <div className="relative">
        <input
          type="range"
          min={0}
          max={Math.max(p.duration, 0.1)}
          step={0.1}
          value={p.time}
          disabled={disabled}
          onChange={(e) => p.seek(Number(e.target.value))}
          aria-label={t.playback.scrub}
          aria-valuetext={t.playback.time(p.time, p.duration)}
          className="h-11 w-full cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-50"
        />
        {p.duration > 0 && (
          <div aria-hidden className="pointer-events-none absolute inset-x-1.5 bottom-0.5 h-1.5">
            {ticks.map((e) => (
              <span
                key={e.event_id}
                title={eventSentence(e.type, e.player_id, e.related_player_id, roster)}
                className={cn("absolute bottom-0 h-1.5 w-0.5 -translate-x-1/2 rounded-full", e.type === "shot" || e.type === "goal" ? "bg-telestrator" : "bg-blocked")}
                style={{ left: `${Math.min(100, Math.max(0, ((e.elapsed - t0) / p.duration) * 100))}%` }}
              />
            ))}
          </div>
        )}
      </div>
      {p.still && <p className="text-xs text-muted-foreground">{t.playback.reducedMotion}</p>}
    </div>
  );
}

export function eventSentence(type: string, playerId: string | null, relatedId: string | null, roster: Roster): string {
  const who = nameOf(roster, playerId) || t.events.unknownPlayer;
  const verb = t.events.types[type] ?? type.replace(/_/g, " ");
  const to = relatedId ? ` ${t.events.to} ${nameOf(roster, relatedId)}` : "";
  return `${who} ${verb}${to}`;
}

"use client";

import { memo, useEffect, useRef, useState } from "react";

import { type Point, useLive } from "@/features/live/live-store";
import { useProfile } from "@/features/profile/profile-store";
import type { Entity, Frame, MatchMeta } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { sideOf, sideTone, teamName } from "@/lib/match";
import { cn } from "@/lib/utils";

const W = 105;
const H = 68;
/** A tap this close (screen px) to a player picks them: a 44 px target even on a phone-sized pitch. */
const TAP_RADIUS_PX = 22;

/** Chalk markings for a 105 x 68 m pitch, drawn once. */
export const Markings = memo(function Markings() {
  return (
    <g className="fill-none stroke-chalk" strokeWidth={0.3}>
      <rect x={0} y={0} width={W} height={H} />
      <line x1={W / 2} y1={0} x2={W / 2} y2={H} />
      <circle cx={W / 2} cy={H / 2} r={9.15} />
      <circle cx={W / 2} cy={H / 2} r={0.4} className="fill-chalk" />
      {[0, 1].map((end) => {
        const flip = (x: number) => (end ? W - x : x);
        const box = end ? W - 16.5 : 0;
        const six = end ? W - 5.5 : 0;
        return (
          <g key={end}>
            <rect x={box} y={(H - 40.32) / 2} width={16.5} height={40.32} />
            <rect x={six} y={(H - 18.32) / 2} width={5.5} height={18.32} />
            <circle cx={flip(11)} cy={H / 2} r={0.4} className="fill-chalk" />
            <path
              d={end ? `M ${W - 16.5} ${H / 2 - 7.3} A 9.15 9.15 0 0 0 ${W - 16.5} ${H / 2 + 7.3}` : `M 16.5 ${H / 2 - 7.3} A 9.15 9.15 0 0 1 16.5 ${H / 2 + 7.3}`}
            />
            <rect x={end ? W : -2} y={(H - 7.32) / 2} width={2} height={7.32} />
          </g>
        );
      })}
    </g>
  );
});

type DotProps = {
  entity: Entity;
  meta: MatchMeta;
  tweenMs: number;
  favourite: boolean;
  selected: boolean;
  /** Marker size multiplier, so dots stay at least 16px across on a phone-sized pitch. */
  scale: number;
  onSelect?: (id: string, el: SVGGElement) => void;
  onHover?: (id: string | null) => void;
};

function Dot({ entity, meta, tweenMs, favourite, selected, scale, onSelect, onHover }: DotProps) {
  const transition = `transform ${tweenMs}ms linear`;
  if (entity.entity_id === "ball") {
    return (
      <circle
        r={0.75 * Math.max(1, scale * 0.8)}
        aria-hidden
        className="pointer-events-none fill-graphic-foreground stroke-graphic"
        strokeWidth={0.2}
        style={{ transform: `translate(${entity.x}px, ${entity.y}px)`, transition }}
      />
    );
  }
  const player = meta.players[entity.entity_id];
  const teamId = entity.entity_id.split("-")[0];
  const side = sideOf(meta, teamId) ?? "home";
  const id = entity.entity_id;
  const interactive = Boolean(onSelect && player);
  return (
    <g
      style={{ transform: `translate(${entity.x}px, ${entity.y}px)`, transition }}
      data-entity={id}
      className={cn("group outline-none", interactive && "cursor-pointer")}
      {...(interactive && {
        role: "button",
        tabIndex: 0,
        "aria-label": `#${player.shirt} ${player.name}, ${teamName(meta, teamId)}. ${copy.playerCard.open}`,
        "aria-haspopup": "dialog" as const,
        "aria-pressed": selected,
        onClick: (e: React.MouseEvent<SVGGElement>) => onSelect?.(id, e.currentTarget),
        onKeyDown: (e: React.KeyboardEvent<SVGGElement>) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect?.(id, e.currentTarget);
          }
        },
        onPointerEnter: () => onHover?.(id),
        onPointerLeave: () => onHover?.(null),
        onFocus: () => onHover?.(id),
        onBlur: () => onHover?.(null),
      })}
    >
      {interactive && <circle r={3.2 * scale} fill="transparent" />}
      <circle
        r={3.2 * scale}
        className="fill-none stroke-ring opacity-0 transition-opacity group-focus-visible:opacity-100"
        strokeWidth={0.5}
      />
      {selected && <circle r={2.7 * scale} className="fill-none stroke-graphic-foreground" strokeWidth={0.5} strokeDasharray="1 0.6" />}
      {favourite && !selected && <circle r={2.6 * scale} className="fill-none stroke-graphic-foreground" strokeWidth={0.35} />}
      <circle
        r={1.55 * scale}
        className={cn(sideTone[side].fill, "stroke-graphic transition-transform group-hover:scale-125")}
        strokeWidth={0.25}
      />
      {player && (
        <text
          y={0.55 * scale}
          textAnchor="middle"
          className="pointer-events-none fill-graphic font-display font-bold"
          style={{ fontSize: 1.5 * scale }}
        >
          {player.shirt}
        </text>
      )}
    </g>
  );
}

/** Name tag above a hovered or focused player; drawn last so no dot covers it. */
function NameTag({ entity, meta, tweenMs }: { entity: Entity; meta: MatchMeta; tweenMs: number }) {
  const player = meta.players[entity.entity_id];
  if (!player) return null;
  const label = player.short_name;
  const width = label.length * 0.95 + 2;
  return (
    <g
      aria-hidden
      className="pointer-events-none"
      style={{ transform: `translate(${entity.x}px, ${entity.y}px)`, transition: `transform ${tweenMs}ms linear` }}
    >
      <rect x={-width / 2} y={-5.6} width={width} height={2.6} rx={0.5} className="fill-graphic" />
      <text y={-3.75} textAnchor="middle" className="fill-graphic-foreground font-display font-semibold" style={{ fontSize: 1.7 }}>
        {label}
      </text>
    </g>
  );
}

/** Fading dots where the ball has just been; the newest point is where it is heading, so it is left out. */
function BallTrail({ trail }: { trail: Point[] }) {
  const past = trail.slice(0, -1);
  if (past.length < 2) return null;
  return (
    <g aria-hidden className="pointer-events-none">
      <polyline
        points={past.map((p) => `${p.x},${p.y}`).join(" ")}
        className="fill-none stroke-graphic-foreground"
        strokeWidth={0.25}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeOpacity={0.35}
        strokeDasharray="0.6 0.6"
      />
      {past.map((p, i) => (
        <circle
          key={i}
          cx={p.x}
          cy={p.y}
          r={0.35 + (0.3 * i) / past.length}
          className="fill-graphic-foreground"
          fillOpacity={0.15 + (0.45 * i) / past.length}
        />
      ))}
    </g>
  );
}

type PitchProps = {
  className?: string;
  /** Draw this frame instead of the live one (a replay). */
  frame?: Frame | null;
  /** The ball trail to draw with an overridden frame; the live trail otherwise. */
  ballTrail?: Point[];
  /** Tween time between overridden frames. */
  tweenMs?: number;
  /** Broadcast camera: the pitch tilted away from the viewer, like the gantry shot. */
  tilt?: boolean;
  /** Layers in pitch metres drawn over the players (the telestrator). */
  overlay?: React.ReactNode;
  /** Makes players selectable (click, Enter or Space). */
  onSelectPlayer?: (playerId: string, el: SVGGElement) => void;
  selectedPlayerId?: string | null;
  /** Extra layers in pitch coordinates (metres), drawn under the players. */
  children?: React.ReactNode;
};

/** Order players home then away by shirt number, ball last: a stable tab order and z-order. */
function entityOrder(meta: MatchMeta) {
  return (e: Entity) => {
    if (e.entity_id === "ball") return 1e6;
    const p = meta.players[e.entity_id];
    const away = e.entity_id.startsWith(meta.away.team_id) ? 1000 : 0;
    return away + (p?.shirt ?? 999);
  };
}

/** Live 2D pitch from 1 Hz tracking frames, tweened between frames. */
export function Pitch({
  className,
  frame: frameOverride,
  ballTrail,
  tweenMs: tweenOverride,
  tilt = false,
  overlay,
  onSelectPlayer,
  selectedPlayerId = null,
  children,
}: PitchProps) {
  const meta = useLive((s) => s.meta);
  const liveFrame = useLive((s) => s.frame);
  const liveTrail = useLive((s) => s.ballTrail);
  const liveTween = useLive((s) => s.frameIntervalMs);
  const replaying = frameOverride !== undefined;
  const frame = replaying ? frameOverride : liveFrame;
  const trail = replaying ? (ballTrail ?? []) : liveTrail;
  const tweenMs = tweenOverride ?? liveTween;
  const possession = useLive((s) => s.state?.possession_team ?? null);
  const favourite = useProfile((s) => s.playerId);
  const [hovered, setHovered] = useState<string | null>(null);
  const interactive = Boolean(onSelectPlayer);
  const label = meta
    ? `Live pitch view. ${possession ? `${teamName(meta, possession)} in possession.` : ""}${interactive ? ` ${copy.playerCard.hint}.` : ""}`
    : "Live pitch view";
  const order = meta ? entityOrder(meta) : null;
  const entities = order && frame ? frame.entities.slice().sort((a, b) => order(a) - order(b)) : [];
  const tagged = entities.find((e) => e.entity_id === hovered);
  const svgRef = useRef<SVGSVGElement>(null);
  // Keep markers at least 16px across: 1.55 pitch units is only ~11px on a 390px-wide pitch.
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const ro = new ResizeObserver(() => {
      const px = svg.getBoundingClientRect().width;
      if (px > 0) setScale(Math.min(1.8, Math.max(1, 8 / (1.55 * (px / (W + 6))))));
    });
    ro.observe(svg);
    return () => ro.disconnect();
  }, []);

  /** Taps that miss every marker go to the nearest player, if one is close enough. */
  const pickNearest = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    const ctm = svg?.getScreenCTM();
    // Under the tilted camera the screen matrix ignores the 3D transform, so only direct hits count.
    if (!svg || !ctm || !onSelectPlayer || tilt || (e.target as Element).closest("[role=button]")) return;
    const at = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
    let best: Entity | null = null;
    let bestDistance = Infinity;
    for (const entity of entities) {
      if (entity.entity_id === "ball") continue;
      const distance = Math.hypot(entity.x - at.x, entity.y - at.y);
      if (distance < bestDistance) [best, bestDistance] = [entity, distance];
    }
    if (!best || bestDistance * ctm.a > TAP_RADIUS_PX) return;
    const marker = svg.querySelector<SVGGElement>(`[data-entity="${best.entity_id}"]`);
    if (marker) onSelectPlayer(best.entity_id, marker);
  };

  return (
    <svg
      ref={svgRef}
      onClick={interactive ? pickNearest : undefined}
      viewBox={`-3 -3 ${W + 6} ${H + 6}`}
      role={interactive ? "group" : "img"}
      aria-label={label}
      className={cn(
        "block h-full w-full transition-transform duration-700 ease-broadcast",
        tilt && "[transform:perspective(900px)_rotateX(32deg)_scale(1.02)] origin-[50%_70%]",
        className,
      )}
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <pattern id="turf-stripes" width={W / 6} height={H} patternUnits="userSpaceOnUse">
          <rect width={W / 12} height={H} className="fill-turf" />
          <rect x={W / 12} width={W / 12} height={H} className="fill-turf-stripe" />
        </pattern>
      </defs>
      <g aria-hidden>
        <rect x={-3} y={-3} width={W + 6} height={H + 6} rx={1} fill="url(#turf-stripes)" />
        <Markings />
        {children}
        <BallTrail trail={trail} />
      </g>
      {meta &&
        entities.map((e) => (
          <Dot
            key={e.entity_id}
            entity={e}
            meta={meta}
            tweenMs={tweenMs}
            favourite={e.entity_id === favourite}
            selected={e.entity_id === selectedPlayerId}
            scale={scale}
            onSelect={onSelectPlayer}
            onHover={interactive ? setHovered : undefined}
          />
        ))}
      {overlay}
      {meta && tagged && <NameTag entity={tagged} meta={meta} tweenMs={tweenMs} />}
    </svg>
  );
}

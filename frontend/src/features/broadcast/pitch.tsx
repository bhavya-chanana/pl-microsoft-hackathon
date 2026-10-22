"use client";

import { memo } from "react";

import { useLive } from "@/features/live/live-store";
import { useProfile } from "@/features/profile/profile-store";
import type { Entity, MatchMeta } from "@/lib/contract";
import { cn } from "@/lib/utils";
import { sideOf, sideTone, teamName } from "@/lib/match";

const W = 105;
const H = 68;

/** Chalk markings for a 105 x 68 m pitch, drawn once. */
const Markings = memo(function Markings() {
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

function Dot({ entity, meta, tweenMs, favourite }: { entity: Entity; meta: MatchMeta; tweenMs: number; favourite: boolean }) {
  const transition = `transform ${tweenMs}ms linear`;
  if (entity.entity_id === "ball") {
    return (
      <circle
        r={0.75}
        className="fill-graphic-foreground stroke-graphic"
        strokeWidth={0.2}
        style={{ transform: `translate(${entity.x}px, ${entity.y}px)`, transition }}
      />
    );
  }
  const player = meta.players[entity.entity_id];
  const teamId = entity.entity_id.split("-")[0];
  const side = sideOf(meta, teamId) ?? "home";
  return (
    <g style={{ transform: `translate(${entity.x}px, ${entity.y}px)`, transition }}>
      {favourite && <circle r={2.6} className="fill-none stroke-graphic-foreground" strokeWidth={0.35} />}
      <circle r={1.55} className={cn(sideTone[side].fill, "stroke-graphic")} strokeWidth={0.25} />
      {player && (
        <text
          y={0.55}
          textAnchor="middle"
          className="fill-graphic font-display font-bold"
          style={{ fontSize: 1.5 }}
        >
          {player.shirt}
        </text>
      )}
    </g>
  );
}

/** Live 2D pitch from 1 Hz tracking frames, tweened between frames. */
export function Pitch({ className }: { className?: string }) {
  const meta = useLive((s) => s.meta);
  const frame = useLive((s) => s.frame);
  const tweenMs = useLive((s) => s.frameIntervalMs);
  const possession = useLive((s) => s.state?.possession_team ?? null);
  const favourite = useProfile((s) => s.playerId);
  const label = meta
    ? `Live pitch view. ${possession ? `${teamName(meta, possession)} in possession.` : ""}`
    : "Live pitch view";
  return (
    <svg
      viewBox={`-3 -3 ${W + 6} ${H + 6}`}
      role="img"
      aria-label={label}
      className={cn("block h-full w-full", className)}
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <pattern id="turf-stripes" width={W / 6} height={H} patternUnits="userSpaceOnUse">
          <rect width={W / 12} height={H} className="fill-turf" />
          <rect x={W / 12} width={W / 12} height={H} className="fill-turf-stripe" />
        </pattern>
      </defs>
      <rect x={-3} y={-3} width={W + 6} height={H + 6} rx={1} fill="url(#turf-stripes)" />
      <Markings />
      {meta &&
        frame?.entities
          .slice()
          .sort((a, b) => (a.entity_id === "ball" ? 1 : b.entity_id === "ball" ? -1 : 0))
          .map((e) => (
            <Dot key={e.entity_id} entity={e} meta={meta} tweenMs={tweenMs} favourite={e.entity_id === favourite} />
          ))}
    </svg>
  );
}

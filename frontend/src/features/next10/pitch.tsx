"use client";

import { memo } from "react";

import { Markings } from "@/features/broadcast/pitch";
import { sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

import { PITCH_X, PITCH_Y } from "./config";
import type { BallZone, Entity } from "./contract";

export type PathLine = {
  key: string;
  points: Array<[number, number]>;
  /** Relative weight 0..1: thickness and opacity. */
  weight: number;
  tone: "home" | "away" | "ball";
  dashed?: boolean;
};

export type ReceiverMark = { entityId: string; label: string; rank: number };

type Props = {
  entities: Entity[];
  zone?: BallZone | null;
  paths?: PathLine[];
  /** Real ball path to draw as a ghost trail. */
  ghost?: Array<[number, number]>;
  receivers?: ReceiverMark[];
  label: string;
  className?: string;
};

/** Heat cells, drawn under the players: opacity follows probability relative to the likeliest cell. */
const Zone = memo(function Zone({ zone }: { zone: BallZone }) {
  const w = (2 * PITCH_X) / zone.cols;
  const h = (2 * PITCH_Y) / zone.rows;
  const max = Math.max(...zone.p, 1e-9);
  return (
    <g aria-hidden>
      {zone.p.map((p, i) => {
        const row = Math.floor(i / zone.cols);
        const col = i % zone.cols;
        const k = p / max;
        if (k < 0.04) return null;
        return <rect key={i} x={zone.x0 + col * w} y={zone.y0 + row * h} width={w} height={h} className="fill-primary" fillOpacity={0.08 + 0.62 * k ** 0.8} />;
      })}
    </g>
  );
});

const R = 1.9;

function Player({ e, rank }: { e: Entity; rank?: ReceiverMark }) {
  const base = { transform: `translate(${e.x} ${e.y})` };
  if (e.team === "ball") {
    return (
      <g {...base} aria-hidden>
        <circle r={1.15} className="fill-graphic-foreground stroke-graphic" strokeWidth={0.3} />
        <circle r={2.1} className="fill-none stroke-graphic-foreground" strokeWidth={0.2} strokeOpacity={0.7} />
      </g>
    );
  }
  if (e.team === "ref") return <rect {...base} x={-1.1} y={-1.1} width={2.2} height={2.2} rx={0.3} className="fill-graphic-warn stroke-graphic" strokeWidth={0.25} />;
  if (e.team === "unk") {
    return <circle {...base} r={R * 0.85} className="fill-none stroke-muted-foreground" strokeWidth={0.35} strokeDasharray="0.8 0.6" />;
  }
  const tone = sideTone[e.team];
  const seen = e.observed;
  return (
    <g {...base} data-entity={e.id} data-observed={seen}>
      {rank && <circle r={R + 1.6} className="fill-none stroke-graphic-foreground" strokeWidth={0.5} />}
      {seen ? (
        <circle r={R} className={cn(tone.fill, "stroke-graphic")} strokeWidth={0.3} />
      ) : (
        <circle r={R} className={cn(tone.stroke, tone.fill)} fillOpacity={0.22} strokeWidth={0.45} strokeDasharray="0.9 0.6" />
      )}
    </g>
  );
}

/** The 2D pitch in metres (origin at the centre spot), with the Next-10s layers. Home attacks towards +x. */
export function Next10Pitch({ entities, zone, paths = [], ghost, receivers = [], label, className }: Props) {
  const byId = new Map(entities.map((e) => [e.id, e]));
  // Players first, then refs, with the ball last so nothing covers it.
  const order = (e: Entity) => (e.team === "ball" ? 3 : e.team === "ref" ? 2 : e.observed ? 1 : 0);
  const sorted = entities.slice().sort((a, b) => order(a) - order(b));
  return (
    <svg viewBox={`${-PITCH_X - 3} ${-PITCH_Y - 3} ${2 * PITCH_X + 6} ${2 * PITCH_Y + 6}`} role="img" aria-label={label} className={cn("block h-auto w-full", className)} preserveAspectRatio="xMidYMid meet">
      <defs>
        <pattern id="n10-stripes" x={-PITCH_X} y={-PITCH_Y} width={(2 * PITCH_X) / 6} height={2 * PITCH_Y} patternUnits="userSpaceOnUse">
          <rect width={PITCH_X / 6} height={2 * PITCH_Y} className="fill-turf" />
          <rect x={PITCH_X / 6} width={PITCH_X / 6} height={2 * PITCH_Y} className="fill-turf-stripe" />
        </pattern>
      </defs>
      <rect x={-PITCH_X - 3} y={-PITCH_Y - 3} width={2 * PITCH_X + 6} height={2 * PITCH_Y + 6} rx={1} fill="url(#n10-stripes)" />
      <g transform={`translate(${-PITCH_X} ${-PITCH_Y})`} aria-hidden>
        <Markings />
      </g>
      {zone && <Zone zone={zone} />}
      {ghost && ghost.length > 1 && (
        <polyline aria-hidden points={ghost.map((p) => p.join(",")).join(" ")} className="fill-none stroke-graphic-foreground" strokeWidth={0.45} strokeLinecap="round" strokeLinejoin="round" strokeOpacity={0.55} strokeDasharray="0.9 0.9" />
      )}
      <g aria-hidden>
        {paths.map((p) => {
          const stroke = p.tone === "ball" ? "stroke-graphic-foreground" : sideTone[p.tone].stroke;
          return (
            <g key={p.key}>
              <polyline points={p.points.map((q) => q.join(",")).join(" ")} className={cn("fill-none", stroke)} strokeWidth={0.35 + 0.7 * p.weight} strokeOpacity={0.25 + 0.65 * p.weight} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={p.dashed ? "1.2 0.9" : undefined} />
              {p.points.length > 0 && <circle cx={p.points[p.points.length - 1][0]} cy={p.points[p.points.length - 1][1]} r={0.6 + 0.5 * p.weight} className={p.tone === "ball" ? "fill-graphic-foreground" : sideTone[p.tone].fill} fillOpacity={0.3 + 0.6 * p.weight} />}
            </g>
          );
        })}
      </g>
      {sorted.map((e) => (
        <Player key={e.id} e={e} rank={receivers.find((r) => r.entityId === e.id)} />
      ))}
      {receivers.map((r) => {
        const e = byId.get(r.entityId);
        if (!e) return null;
        return (
          <g key={r.entityId} transform={`translate(${e.x} ${e.y})`} aria-hidden>
            <rect x={-3.4} y={-8.1} width={6.8} height={3.6} rx={0.8} className="fill-graphic" />
            <text y={-5.5} textAnchor="middle" className="fill-graphic-foreground font-display font-bold" style={{ fontSize: 2.6 }}>
              {r.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

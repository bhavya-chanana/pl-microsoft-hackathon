"use client";

import { m, useReducedMotion } from "motion/react";

import type { Drawing } from "@/lib/contract";
import { cn } from "@/lib/utils";

type XY = { x: number; y: number };

const ease = [0.16, 1, 0.3, 1] as const;

export function arrowHead(from: XY, to: XY, size: number) {
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const left = { x: to.x - size * Math.cos(angle - 0.45), y: to.y - size * Math.sin(angle - 0.45) };
  const right = { x: to.x - size * Math.cos(angle + 0.45), y: to.y - size * Math.sin(angle + 0.45) };
  return `M ${left.x} ${left.y} L ${to.x} ${to.y} L ${right.x} ${right.y}`;
}

export function linePath(points: XY[]) {
  return points.map((p, i) => `${i ? "L" : "M"} ${p.x} ${p.y}`).join(" ");
}

/** Chalk label with a dark halo, readable on any turf. */
function Label({ at, text, primary }: { at: XY; text: string; primary: boolean }) {
  return (
    <m.text
      x={at.x}
      y={at.y}
      textAnchor="middle"
      className={cn("font-display font-semibold", primary ? "fill-telestrator" : "fill-graphic-foreground")}
      style={{ fontSize: 2.1, paintOrder: "stroke", stroke: "var(--graphic)", strokeWidth: 0.6, strokeLinejoin: "round" }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3, delay: 0.25 }}
    >
      {text}
    </m.text>
  );
}

/** A stroke drawn on like a pen, with a darker halo underneath it. */
function Stroke({ d, width, dashed, primary, duration }: { d: string; width: number; dashed?: boolean; primary: boolean; duration: number }) {
  const reduce = useReducedMotion();
  const draw = reduce ? { duration: 0 } : { duration, ease };
  return (
    <>
      <path d={d} className="fill-none stroke-graphic" strokeOpacity={0.45} strokeWidth={width + 0.5} strokeLinecap="round" strokeLinejoin="round" />
      <m.path
        d={d}
        className={cn("fill-none", primary ? "stroke-telestrator" : "stroke-graphic-foreground")}
        strokeWidth={width}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={dashed ? "1.2 1" : undefined}
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={draw}
      />
    </>
  );
}

function Mark({ drawing }: { drawing: Drawing }) {
  const reduce = useReducedMotion();
  const primary = drawing.emphasis === "primary";
  const [a, b] = drawing.points;
  if (!a) return null;
  switch (drawing.kind) {
    case "lane":
    case "carry":
    case "shot": {
      if (!b) return null;
      const width = drawing.kind === "shot" ? 0.75 : primary ? 0.5 : 0.35;
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 1.6 };
      return (
        <g>
          <Stroke d={linePath([a, b])} width={width} dashed={drawing.kind === "carry"} primary={primary || drawing.kind === "shot"} duration={drawing.kind === "shot" ? 0.35 : 0.55} />
          <Stroke d={arrowHead(a, b, drawing.kind === "shot" ? 2.4 : 1.7)} width={width} primary={primary || drawing.kind === "shot"} duration={0.2} />
          {drawing.label && <Label at={drawing.kind === "shot" ? { x: b.x, y: b.y - 2.4 } : mid} text={drawing.label} primary />}
        </g>
      );
    }
    case "path":
      return drawing.points.length > 1 ? <Stroke d={linePath(drawing.points)} width={0.4} dashed primary={primary} duration={0.9} /> : null;
    case "ring": {
      const r = drawing.radius ?? 2.6;
      return (
        <g>
          <m.circle
            cx={a.x}
            cy={a.y}
            r={r}
            className={cn("fill-none", primary ? "stroke-telestrator" : "stroke-graphic-foreground")}
            strokeWidth={0.5}
            initial={{ scale: reduce ? 1 : 2.2, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={reduce ? { duration: 0 } : { duration: 0.45, ease }}
            style={{ transformOrigin: `${a.x}px ${a.y}px`, transformBox: "view-box" }}
          />
          {drawing.label && <Label at={{ x: a.x, y: a.y + r + 2.4 }} text={drawing.label} primary={primary} />}
        </g>
      );
    }
    case "zone": {
      const r = drawing.radius ?? 10;
      return (
        <g>
          <m.circle
            cx={a.x}
            cy={a.y}
            r={r}
            className="fill-telestrator stroke-telestrator"
            fillOpacity={0.14}
            strokeWidth={0.35}
            strokeDasharray="1.4 1"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5 }}
          />
          {drawing.label && <Label at={{ x: a.x, y: a.y - r - 1 }} text={drawing.label} primary />}
        </g>
      );
    }
    case "marker":
      return (
        <g>
          <m.circle
            cx={a.x}
            cy={a.y}
            r={0.8}
            className={primary ? "fill-telestrator" : "fill-graphic-foreground"}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.2 }}
          />
          {drawing.label && <Label at={{ x: a.x, y: a.y - 1.6 }} text={drawing.label} primary={primary} />}
        </g>
      );
  }
}

/** Telestrator strokes over the players, in pitch metres; each draws itself on as the replay reaches it. */
export function TelestratorLayer({ drawings }: { drawings: Drawing[] }) {
  return (
    <g aria-hidden className="pointer-events-none">
      {drawings.map((d, i) => (
        <Mark key={`${d.kind}-${i}`} drawing={d} />
      ))}
    </g>
  );
}

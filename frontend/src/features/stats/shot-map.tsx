"use client";

import { useState } from "react";

import { useLive } from "@/features/live/live-store";
import type { MatchEvent, MatchMeta } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { playerName, type Side, sideOf, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

const S = copy.shotMap;
const L = 105;
const W = 68;
const HALF = L / 2;
const LATEST = 6;

type PlottedShot = { event: MatchEvent; side: Side; x: number; y: number; xg: number | null };

/**
 * Turn a shot from match coordinates into the half-pitch view: attacking
 * upwards, goal at the top, the shooter's left on the left.
 */
function plot(e: MatchEvent, meta: MatchMeta): PlottedShot | null {
  if (!e.start) return null;
  const side = sideOf(meta, e.team_id);
  if (!side) return null;
  // The home side attacks x = 105 in both halves (docs/SCHEMA.md).
  const towardsRight = side === "home";
  const nx = towardsRight ? e.start.x : L - e.start.x;
  const ny = towardsRight ? e.start.y : W - e.start.y;
  const xg = typeof e.attrs.xg === "number" ? e.attrs.xg : null;
  return { event: e, side, x: ny, y: Math.min(HALF, L - nx), xg };
}

/** Outcome as a shape, so the map never relies on team colour alone. */
function Mark({ outcome, r, className }: { outcome: string; r: number; className?: string }) {
  switch (outcome) {
    case "goal":
      return (
        <g className={className}>
          <circle r={r} className="fill-current" />
          <circle r={r + 0.7} className="fill-none stroke-current" strokeWidth={0.35} />
        </g>
      );
    case "saved":
      return <rect x={-r} y={-r} width={r * 2} height={r * 2} className={cn("fill-none stroke-current", className)} strokeWidth={0.5} />;
    case "blocked":
      return (
        <path
          d={`M 0 ${-r * 1.15} L ${r} ${r * 0.75} L ${-r} ${r * 0.75} Z`}
          className={cn("fill-none stroke-current", className)}
          strokeWidth={0.5}
          strokeLinejoin="round"
        />
      );
    default:
      return (
        <path
          d={`M ${-r} ${-r} L ${r} ${r} M ${r} ${-r} L ${-r} ${r}`}
          className={cn("fill-none stroke-current", className)}
          strokeWidth={0.55}
          strokeLinecap="round"
        />
      );
  }
}

const markSize = (xg: number | null) => 0.8 + Math.sqrt(xg ?? 0.05) * 2;

function HalfPitch() {
  return (
    <g className="fill-none stroke-chalk" strokeWidth={0.3}>
      <rect width={W} height={HALF} />
      <rect x={(W - 40.32) / 2} width={40.32} height={16.5} />
      <rect x={(W - 18.32) / 2} width={18.32} height={5.5} />
      <rect x={(W - 7.32) / 2} y={-1.6} width={7.32} height={1.6} />
      <circle cx={W / 2} cy={11} r={0.4} className="fill-chalk" />
      <path d={`M ${W / 2 - 7.31} 16.5 A 9.15 9.15 0 0 0 ${W / 2 + 7.31} 16.5`} />
      <path d={`M ${W / 2 - 9.15} ${HALF} A 9.15 9.15 0 0 1 ${W / 2 + 9.15} ${HALF}`} />
    </g>
  );
}

function Legend() {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label={S.key}>
      {Object.entries(S.outcomes).map(([outcome, label]) => (
        <li key={outcome} className="inline-flex items-center gap-1.5">
          <svg viewBox="-2.5 -2.5 5 5" className="size-3.5 text-foreground" aria-hidden>
            <Mark outcome={outcome} r={1.5} />
          </svg>
          {label}
        </li>
      ))}
    </ul>
  );
}

function describeShot(shot: PlottedShot, meta: MatchMeta): string {
  const outcome = S.outcomes[shot.event.outcome] ?? shot.event.outcome.replaceAll("_", " ");
  const xg = shot.xg === null ? "" : `, ${S.xg(shot.xg)}`;
  return `${shot.event.minute_label} ${playerName(meta, shot.event.player_id)} (${meta[shot.side].short_name}), ${outcome}${xg}`;
}

/** Analyst view: every shot so far on one half-pitch, coloured by team, shaped by outcome. */
export function ShotMapPanel({ className }: { className?: string }) {
  const meta = useLive((s) => s.meta);
  const timeline = useLive((s) => s.timeline);
  const [active, setActive] = useState<string | null>(null);
  if (!meta) return null;
  const shots = timeline.filter((e) => e.type === "shot").flatMap((e) => plot(e, meta) ?? []);
  const latest = shots.slice(-LATEST).reverse();
  const goals = shots.filter((s) => s.event.outcome === "goal").length;
  const count = (side: Side) => shots.filter((s) => s.side === side).length;

  return (
    <section aria-labelledby="shot-map-heading" className={cn("flex flex-col gap-3 rounded-lg border bg-card p-4", className)}>
      <div className="flex flex-col gap-0.5">
        <h2 id="shot-map-heading" className="font-display text-lg font-semibold">
          {S.heading}
        </h2>
        <p className="max-w-prose text-xs text-muted-foreground">{S.help}</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
        <figure className="flex flex-col gap-2">
          <svg
            viewBox={`-3 -3 ${W + 6} ${HALF + 5}`}
            role="img"
            aria-label={`${S.heading}: ${meta.home.name} ${count("home")} shots, ${meta.away.name} ${count("away")} shots, ${goals} goals.`}
            className="w-full rounded-md bg-turf"
          >
            <HalfPitch />
            {shots.map((shot) => {
              const dim = active !== null && active !== shot.event.event_id;
              return (
                <g
                  key={shot.event.event_id}
                  transform={`translate(${shot.x} ${shot.y})`}
                  className={cn(sideTone[shot.side].text, "transition-opacity", dim && "opacity-25")}
                >
                  <Mark outcome={shot.event.outcome} r={markSize(shot.xg)} className="stroke-current" />
                  <title>{describeShot(shot, meta)}</title>
                </g>
              );
            })}
          </svg>
          <figcaption className="flex flex-col gap-1.5">
            <Legend />
            <p className="flex gap-4 text-xs">
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden className="size-2.5 rounded-full bg-home" />
                {meta.home.short_name} {count("home")}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden className="size-2.5 rounded-full bg-away" />
                {meta.away.short_name} {count("away")}
              </span>
            </p>
          </figcaption>
        </figure>
        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="text-sm font-medium">{S.latest}</h3>
          {latest.length === 0 ? (
            <p className="text-sm text-muted-foreground">{S.empty}</p>
          ) : (
            <ol className="flex flex-col" onPointerLeave={() => setActive(null)}>
              {latest.map((shot) => (
                <li
                  key={shot.event.event_id}
                  onPointerEnter={() => setActive(shot.event.event_id)}
                  className="grid grid-cols-[2.75rem_1rem_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted"
                >
                  <span className="font-display font-semibold tabular-nums">{shot.event.minute_label}</span>
                  <svg viewBox="-2.5 -2.5 5 5" className={cn("size-3.5", sideTone[shot.side].text)} aria-hidden>
                    <Mark outcome={shot.event.outcome} r={1.5} />
                  </svg>
                  <span className="truncate">
                    {playerName(meta, shot.event.player_id, true)}
                    <span className="sr-only"> ({meta[shot.side].short_name})</span>
                    <span className="text-muted-foreground">
                      {" "}
                      {S.outcomes[shot.event.outcome] ?? shot.event.outcome.replaceAll("_", " ")}
                    </span>
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {shot.xg === null ? "" : S.xg(shot.xg)}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </section>
  );
}

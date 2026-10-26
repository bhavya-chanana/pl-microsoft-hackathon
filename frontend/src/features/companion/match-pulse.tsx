"use client";

import { useId, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useReplay } from "@/features/broadcast/replay-store";
import { useLive } from "@/features/live/live-store";
import { usePulse } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import type { MatchMeta, Pulse, PulsePin, PulseRow } from "@/lib/contract";
import { copy, signalLabels } from "@/lib/copy";
import { playerName, sideOf, sideTone, teamName } from "@/lib/match";
import { Info } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

const VW = 1000;
const MID = 46;
const AMP = 38;
const BAND_Y = 92;
const BAND_H = 10;
const VH = BAND_Y + BAND_H + 2;
/** Draw the full ninety (plus stoppage) from the start, so the strip fills in as the match goes. */
const MIN_SPAN_S = 95 * 60;

const bandTone = { control: "fill-pulse-control", balanced: "fill-muted-foreground/25", chaos: "fill-pulse-chaos" } as const;

function scaleX(span: number) {
  return (elapsed: number) => (Math.min(elapsed, span) / span) * VW;
}

function momentumPath(rows: PulseRow[], x: (e: number) => number) {
  if (!rows.length) return "";
  const pts = rows.map((r) => `${x(r.elapsed).toFixed(1)},${(MID - (Math.max(-100, Math.min(100, r.momentum)) / 100) * AMP).toFixed(1)}`);
  return `M ${x(rows[0].elapsed).toFixed(1)},${MID} L ${pts.join(" L ")} L ${x(rows.at(-1)!.elapsed).toFixed(1)},${MID} Z`;
}

function describe(row: PulseRow, meta: MatchMeta): string {
  const lead = Math.abs(row.momentum) < 10 ? null : row.momentum > 0 ? meta.home.team_id : meta.away.team_id;
  const team = row.state === "control" ? row.controlling_team : lead;
  return copy.pulse.casual(row.minute_label, team ? teamName(meta, team) : null, row.state);
}

function Readout({ row, meta, analyst }: { row: PulseRow; meta: MatchMeta; analyst: boolean }) {
  return (
    <p className="flex min-h-5 flex-wrap items-baseline gap-x-4 gap-y-0.5 text-sm" aria-live="polite">
      <span className="font-medium">{describe(row, meta)}</span>
      {analyst && (
        <>
          <span className="text-muted-foreground tabular-nums">
            {copy.pulse.pressure}: {meta.home.short_name} {row.pressure[meta.home.team_id] ?? 0}, {meta.away.short_name}{" "}
            {row.pressure[meta.away.team_id] ?? 0}
          </span>
          <span className="text-muted-foreground tabular-nums">
            {copy.pulse.turnovers}: {row.turnovers_per_min.toFixed(1)}
          </span>
        </>
      )}
    </p>
  );
}

function PinMark({ pin, meta }: { pin: PulsePin; meta: MatchMeta }) {
  const side = sideOf(meta, pin.team_id);
  if (pin.type === "card") {
    return <span className={cn("block h-3.5 w-2.5 rounded-[2px] ring-1 ring-background", pin.card === "red" ? "bg-destructive" : "bg-graphic-warn")} />;
  }
  if (pin.type === "goal") {
    return (
      <span className={cn("block size-4 rounded-full ring-2 ring-background", side ? sideTone[side].bg : "bg-foreground")}>
        <span className="m-auto mt-[5px] block size-1.5 rounded-full bg-background" />
      </span>
    );
  }
  return <span className={cn("block size-2.5 rotate-45 rounded-[1px] ring-1 ring-background", side ? sideTone[side].bg : "bg-muted-foreground")} />;
}

function Pins({ pulse, meta, x }: { pulse: Pulse; meta: MatchMeta; x: (e: number) => number }) {
  const play = useReplay((s) => s.play);
  // Goals and cards on top, so the moments that matter win any overlap.
  const order = (p: PulsePin) => (p.type === "goal" ? 2 : p.type === "card" ? 1 : 0);
  const pins = pulse.pins.slice().sort((a, b) => order(a) - order(b));
  return (
    <div className="relative h-7">
      {pins.map((pin) => {
        const label = signalLabels[pin.type] ?? pin.type;
        const who = playerName(meta, pin.player_ids[0], true);
        const text = copy.pulse.pin(who ? `${label}, ${who}` : label, pin.minute_label);
        return (
          <button
            key={pin.insight_id}
            type="button"
            title={text}
            aria-label={text}
            onClick={() => play(pin.insight_id, { reveal: true })}
            className="group absolute top-1/2 grid size-6 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full after:absolute after:-inset-2.5 hover:z-10 focus-visible:z-10"
            style={{ left: `${(x(pin.elapsed) / VW) * 100}%` }}
          >
            <span className="transition-transform group-hover:scale-125">
              <PinMark pin={pin} meta={meta} />
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** The match's heartbeat: momentum each minute, how the game felt, and the moments to replay. */
export function MatchPulse({ className }: { className?: string }) {
  const meta = useLive((s) => s.meta);
  const pulse = usePulse();
  const analyst = useProfile((s) => s.mode === "analyst");
  const [hover, setHover] = useState<number | null>(null);
  const clip = useId().replace(/:/g, "");
  const data = pulse.data;

  let body: React.ReactNode;
  if (!meta || pulse.isPending) {
    body = <Skeleton className="h-28 w-full" aria-label={copy.pulse.loading} />;
  } else if (pulse.isError || !data) {
    body = <p className="text-sm text-muted-foreground">{copy.pulse.error}</p>;
  } else if (!data.rows.length) {
    body = <p className="text-sm text-muted-foreground">{copy.pulse.empty}</p>;
  } else {
    const span = Math.max(MIN_SPAN_S, data.rows.at(-1)!.elapsed + 60);
    const x = scaleX(span);
    const step = VW / (span / 60);
    const half = data.rows.find((r) => r.period === 2);
    const row = data.rows[hover ?? data.rows.length - 1];
    const pick = (e: React.PointerEvent<SVGSVGElement>) => {
      const box = e.currentTarget.getBoundingClientRect();
      const at = ((e.clientX - box.left) / box.width) * span;
      let best = 0;
      data.rows.forEach((r, i) => {
        if (Math.abs(r.elapsed - at) < Math.abs(data.rows[best].elapsed - at)) best = i;
      });
      setHover(best);
    };
    body = (
      <div className="flex flex-col gap-1">
        <Readout row={row} meta={meta} analyst={analyst} />
        <Pins pulse={data} meta={meta} x={x} />
        <svg
          viewBox={`0 0 ${VW} ${VH}`}
          preserveAspectRatio="none"
          className="block h-24 w-full touch-none sm:h-28"
          role="img"
          aria-label={`${copy.pulse.heading}. ${describe(data.rows.at(-1)!, meta)}`}
          onPointerMove={pick}
          onPointerDown={pick}
          onPointerLeave={() => setHover(null)}
        >
          <defs>
            <clipPath id={`${clip}-top`}>
              <rect x={0} y={0} width={VW} height={MID} />
            </clipPath>
            <clipPath id={`${clip}-bottom`}>
              <rect x={0} y={MID} width={VW} height={MID} />
            </clipPath>
          </defs>
          <path d={momentumPath(data.rows, x)} className="fill-home" fillOpacity={0.85} clipPath={`url(#${clip}-top)`} />
          <path d={momentumPath(data.rows, x)} className="fill-away" fillOpacity={0.85} clipPath={`url(#${clip}-bottom)`} />
          <line x1={0} x2={VW} y1={MID} y2={MID} className="stroke-foreground/30" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          {half && (
            <line
              x1={x(half.elapsed)}
              x2={x(half.elapsed)}
              y1={0}
              y2={VH}
              className="stroke-foreground/30"
              strokeDasharray="3 3"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {data.rows.map((r) => (
            <rect
              key={r.elapsed}
              x={x(r.elapsed) - step}
              y={BAND_Y}
              width={step + 0.5}
              height={BAND_H}
              className={bandTone[r.state]}
            />
          ))}
          {hover !== null && (
            <line
              x1={x(row.elapsed)}
              x2={x(row.elapsed)}
              y1={0}
              y2={VH}
              className="stroke-foreground"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
        <div className="flex items-center justify-between gap-3 text-[13px] text-muted-foreground">
          <span className={sideTone.home.text}>{meta.home.short_name}</span>
          <span className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1">
            {(["control", "balanced", "chaos"] as const).map((state) => (
              <span key={state} className="inline-flex items-center gap-1">
                <svg aria-hidden viewBox="0 0 10 10" className="size-2.5">
                  <rect width={10} height={10} rx={2} className={bandTone[state]} />
                </svg>
                {copy.pulse.states[state]}
              </span>
            ))}
          </span>
          <span className={sideTone.away.text}>{meta.away.short_name}</span>
        </div>
      </div>
    );
  }

  return (
    <section aria-labelledby="pulse-heading" className={cn("flex flex-col gap-2 rounded-lg border bg-card p-4 shadow-raised", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="pulse-heading" className="font-display text-lg font-semibold">
          {copy.pulse.heading}
        </h2>
        {data && <span className="text-xs text-muted-foreground tabular-nums">{data.now.minute_label}</span>}
      </div>
      <p className="flex items-center gap-1 text-[13px] text-muted-foreground">
        {copy.pulse.short}
        <Popover>
          <PopoverTrigger render={<Button variant="ghost" size="icon-sm" className="size-8 rounded-full" aria-label={copy.pulse.more} />}>
            <Info aria-hidden className="size-4" />
          </PopoverTrigger>
          <PopoverContent className="w-72 text-sm">{copy.pulse.help}</PopoverContent>
        </Popover>
      </p>
      {body}
    </section>
  );
}

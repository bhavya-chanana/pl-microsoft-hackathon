"use client";

import { ArrowUpRight, Clapperboard, FlaskConical, History, LayoutGrid, Timer } from "lucide-react";
import Link from "next/link";
import type { ComponentType, ReactNode } from "react";

import { useShell } from "@/components/shell/shell-store";
import { useLive } from "@/features/live/live-store";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

const T = copy.explorePage;

const tileClass =
  "group relative flex h-full min-h-40 w-full items-stretch gap-4 overflow-hidden rounded-2xl border bg-card p-5 text-left shadow-raised transition duration-200 hover:-translate-y-0.5 hover:border-foreground/20 hover:shadow-floating focus-visible:-translate-y-0.5";

function spot(e: React.PointerEvent<HTMLElement>) {
  const r = e.currentTarget.getBoundingClientRect();
  e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
  e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
}

function Body({
  icon: Icon,
  title,
  subtitle,
  body,
  preview,
  wide,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  subtitle?: string;
  body: string;
  preview: ReactNode;
  wide?: boolean;
}) {
  return (
    <>
      <span aria-hidden className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-200 group-hover:opacity-100" style={{ background: "radial-gradient(260px circle at var(--mx, 50%) var(--my, 0%), oklch(1 0 0 / 0.07), transparent 70%)" }} />
      <div className="relative flex min-w-0 flex-1 flex-col justify-between gap-3">
        <span aria-hidden className="grid size-10 place-items-center rounded-xl bg-primary/15 text-primary">
          <Icon className="size-5" />
        </span>
        <span>
          <span className="flex items-center gap-1.5 text-xl font-semibold">
            {title}
            <ArrowUpRight aria-hidden className="size-4 -translate-x-1 text-muted-foreground opacity-0 transition-[opacity,transform] duration-200 group-hover:translate-x-0 group-hover:opacity-100 group-focus-visible:translate-x-0 group-focus-visible:opacity-100" />
          </span>
          {subtitle && <span className="block text-[13px] text-subtle">{subtitle}</span>}
          <span className="mt-1 block text-[15px] text-muted-foreground">{body}</span>
        </span>
      </div>
      <div aria-hidden className={cn("relative h-28 min-w-0 shrink-0 self-center overflow-hidden max-sm:hidden", wide ? "w-1/2" : "w-2/5 max-w-48")}>
        {preview}
      </div>
    </>
  );
}

const PITCH = (
  <svg aria-hidden viewBox="0 0 120 80" className="absolute inset-0 size-full text-foreground/25" fill="none" stroke="currentColor" strokeWidth={2} preserveAspectRatio="none">
    <rect x={2} y={2} width={116} height={76} rx={4} />
    <line x1={60} y1={2} x2={60} y2={78} />
    <circle cx={60} cy={40} r={12} />
  </svg>
);

function ScoreBug({ h, a }: { h: number; a: number }) {
  return (
    <div className="absolute top-1.5 left-1.5 flex items-stretch overflow-hidden rounded-sm bg-background/90 ring-1 ring-border">
      <span className="bg-home px-[3px]" />
      <span className="px-1.5 font-display text-[11px] leading-4 font-bold tnum">{h}-{a}</span>
      <span className="bg-away px-[3px]" />
    </div>
  );
}

function MiniScoreboard() {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const h = meta && state ? (state.score[meta.home.team_id] ?? 0) : 0;
  const a = meta && state ? (state.score[meta.away.team_id] ?? 0) : 0;
  return (
    <div className="relative h-full w-full overflow-hidden rounded-lg bg-turf">
      {PITCH}
      <ScoreBug h={h} a={a} />
    </div>
  );
}

/** Four mini broadcast frames in four different tints: one match, four fans. */
function Panels() {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const h = meta && state ? (state.score[meta.home.team_id] ?? 0) : 0;
  const a = meta && state ? (state.score[meta.away.team_id] ?? 0) : 0;
  return (
    <div className="grid h-full grid-cols-4 gap-2">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className={cn("relative h-full overflow-hidden rounded-md bg-turf ring-1 ring-border", i % 2 ? "after:bg-away/20" : "after:bg-home/20", "after:absolute after:inset-0")}>
          {PITCH}
          <ScoreBug h={h} a={a} />
        </div>
      ))}
    </div>
  );
}

const Spark = () => (
  <svg viewBox="0 0 100 40" className="h-full w-full fill-none" preserveAspectRatio="none">
    <path d="M0 28 L12 24 L24 30 L36 18 L48 22 L60 10 L72 16 L84 6 L100 12" className="stroke-primary" strokeWidth={2} vectorEffect="non-scaling-stroke" />
    <path d="M0 34 L20 32 L40 34 L60 28 L80 30 L100 26" className="stroke-foreground/25" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
  </svg>
);

function Rows() {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const rows = [
    meta && state
      ? [`${meta.home.short_name} v ${meta.away.short_name}`, `${state.score[meta.home.team_id] ?? 0}-${state.score[meta.away.team_id] ?? 0}`]
      : ["This match", "-"],
    ["Playback speed", "1×–60×"],
    ["Past matches", "Browse"],
  ];
  return (
    <ul className="flex flex-col divide-y rounded-lg bg-raised/60 px-2.5 text-[13px] ring-1 ring-border">
      {rows.map(([name, score]) => (
        <li key={name} className="flex h-8 items-center justify-between gap-2">
          <span className="truncate">{name}</span>
          <span className="font-display font-semibold tnum">{score}</span>
        </li>
      ))}
    </ul>
  );
}

/** A heat patch over a pitch outline: where the ball may be in ten seconds. */
function Next10Preview() {
  return (
    <div className="relative h-full w-full overflow-hidden rounded-lg bg-turf">
      {PITCH}
      <span aria-hidden className="absolute top-1/2 left-[62%] size-16 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/40 blur-xl" />
      <span aria-hidden className="absolute top-[44%] left-[52%] size-2.5 rounded-full bg-graphic-foreground ring-2 ring-graphic" />
    </div>
  );
}

/** Things to try beyond following the match. */
export function ExplorePage() {
  const setStream = useShell((s) => s.setStream);
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 xl:px-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{T.heading}</h1>
        <p className="text-muted-foreground">{T.intro}</p>
      </header>
      <ul className="grid auto-rows-fr gap-4 md:grid-cols-2">
        <li className="md:col-span-2">
          <Link href={routes.multiverse} className={tileClass} onPointerMove={spot}>
            <Body wide icon={LayoutGrid} title={T.multiverse.title} subtitle={T.multiverse.subtitle} body={T.multiverse.body} preview={<Panels />} />
          </Link>
        </li>
        <li>
          <Link href={routes.broadcast} className={tileClass} onPointerMove={spot}>
            <Body icon={Clapperboard} title={T.broadcast.title} body={T.broadcast.body} preview={<MiniScoreboard />} />
          </Link>
        </li>
        <li>
          <Link href={routes.lab} className={tileClass} onPointerMove={spot}>
            <Body icon={FlaskConical} title={T.lab.title} body={T.lab.body} preview={<Spark />} />
          </Link>
        </li>
        <li className="md:col-span-2">
          <Link href={routes.next10} className={tileClass} onPointerMove={spot}>
            <Body wide icon={Timer} title={T.next10.title} subtitle={T.next10.subtitle} body={T.next10.body} preview={<Next10Preview />} />
          </Link>
        </li>
        <li className="md:col-span-2">
          <button type="button" className={tileClass} onClick={() => setStream(true)} onPointerMove={spot}>
            <Body wide icon={History} title={T.replay.title} body={T.replay.body} preview={<Rows />} />
          </button>
        </li>
      </ul>
    </div>
  );
}

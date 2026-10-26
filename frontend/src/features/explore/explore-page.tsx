"use client";

import { Clapperboard, FlaskConical, History, LayoutGrid } from "lucide-react";
import Link from "next/link";
import type { ComponentType, ReactNode } from "react";

import { useShell } from "@/components/shell/shell-store";
import { useLive } from "@/features/live/live-store";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

const T = copy.explorePage;

const tileClass =
  "group relative flex h-full min-h-48 w-full flex-col justify-between gap-5 overflow-hidden rounded-2xl border bg-card p-5 text-left shadow-raised transition duration-200 hover:-translate-y-0.5 hover:border-foreground/20 hover:shadow-floating focus-visible:-translate-y-0.5";

function spot(e: React.PointerEvent<HTMLElement>) {
  const r = e.currentTarget.getBoundingClientRect();
  e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
  e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
}

function Body({ icon: Icon, title, subtitle, body, preview }: { icon: ComponentType<{ className?: string }>; title: string; subtitle?: string; body: string; preview: ReactNode }) {
  return (
    <>
      <span aria-hidden className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-200 group-hover:opacity-100" style={{ background: "radial-gradient(260px circle at var(--mx, 50%) var(--my, 0%), oklch(1 0 0 / 0.07), transparent 70%)" }} />
      <div className="relative flex items-start justify-between gap-4">
        <span aria-hidden className="grid size-10 place-items-center rounded-xl bg-primary/15 text-primary">
          <Icon className="size-5" />
        </span>
        <div aria-hidden className="h-14 min-w-0 flex-1 max-w-48 opacity-90">
          {preview}
        </div>
      </div>
      <span className="relative">
        <span className="block text-xl font-semibold">{title}</span>
        {subtitle && <span className="block text-[13px] text-subtle">{subtitle}</span>}
        <span className="mt-1 block text-muted-foreground">{body}</span>
      </span>
    </>
  );
}

function MiniScoreboard() {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const h = meta && state ? (state.score[meta.home.team_id] ?? 0) : 0;
  const a = meta && state ? (state.score[meta.away.team_id] ?? 0) : 0;
  return (
    <div className="flex h-full items-center justify-end">
      <div className="flex items-stretch overflow-hidden rounded-md bg-background ring-1 ring-border">
        <span className="bg-home px-1" />
        <span className="px-2 py-1 font-display text-lg font-bold tnum">{h} - {a}</span>
        <span className="bg-away px-1" />
      </div>
    </div>
  );
}

const Panels = () => (
  <div className="grid h-full grid-cols-4 gap-1.5">
    {[0, 1, 2, 3].map((i) => (
      <span key={i} className={cn("rounded-md ring-1 ring-border", i % 2 ? "bg-away/25" : "bg-home/25")} />
    ))}
  </div>
);

const Spark = () => (
  <svg viewBox="0 0 100 40" className="h-full w-full fill-none" preserveAspectRatio="none">
    <path d="M0 28 L12 24 L24 30 L36 18 L48 22 L60 10 L72 16 L84 6 L100 12" className="stroke-primary" strokeWidth={2} vectorEffect="non-scaling-stroke" />
  </svg>
);

const Rows = () => (
  <div className="flex h-full flex-col justify-center gap-1.5">
    {[80, 60, 70].map((w) => (
      <span key={w} className="h-2 rounded-full bg-foreground/15" style={{ width: `${w}%` }} />
    ))}
  </div>
);

/** Things to try beyond following the match. */
export function ExplorePage() {
  const setDev = useShell((s) => s.setDev);
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 md:px-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{T.heading}</h1>
        <p className="text-muted-foreground">{T.intro}</p>
      </header>
      <ul className="grid auto-rows-fr gap-4 md:grid-cols-2">
        <li className="md:col-span-2">
          <Link href={routes.multiverse} className={tileClass} onPointerMove={spot}>
            <Body icon={LayoutGrid} title={T.multiverse.title} subtitle={T.multiverse.subtitle} body={T.multiverse.body} preview={<Panels />} />
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
          <button type="button" className={tileClass} onClick={() => setDev(true)} onPointerMove={spot}>
            <Body icon={History} title={T.replay.title} body={T.replay.body} preview={<Rows />} />
          </button>
        </li>
      </ul>
    </div>
  );
}

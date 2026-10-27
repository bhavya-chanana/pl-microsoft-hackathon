"use client";

import { AnimatePresence, m, useMotionTemplate, useMotionValueEvent, useReducedMotion, useScroll, useTransform } from "motion/react";
import { useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import type { Team } from "@/lib/contract";
import { cn } from "@/lib/utils";

import { copy } from "@/lib/copy";

import { ConnectionPill } from "./connection-pill";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 3)
    .toUpperCase();
}

function Crest({ team, side, className }: { team: Team; side: "home" | "away"; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid shrink-0 place-items-center rounded-full font-display font-bold text-background",
        side === "home" ? "bg-home" : "bg-away",
        className,
      )}
    >
      {initials(team.short_name || team.name)}
    </span>
  );
}

/** A score digit that rolls up on a goal, with a short pulse of the scorer's colour behind it. */
function ScoreDigit({ value, side }: { value: number; side: "home" | "away" }) {
  const reduce = useReducedMotion();
  const [first] = useState(value);
  const changed = value !== first;
  return (
    <span className="relative inline-grid overflow-hidden px-1">
      {changed && !reduce && (
        <m.span
          key={`glow-${value}`}
          aria-hidden
          className={cn("absolute inset-0 -z-10 rounded-xl blur-md", side === "home" ? "bg-home" : "bg-away")}
          initial={{ opacity: 0.7 }}
          animate={{ opacity: 0 }}
          transition={{ duration: 0.8 }}
        />
      )}
      <AnimatePresence initial={false} mode="popLayout">
        <m.span
          key={value}
          initial={{ y: reduce ? 0 : "100%", opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: reduce ? 0 : "-100%", opacity: 0 }}
          transition={{ duration: reduce ? 0 : 0.4, ease: [0.16, 1, 0.3, 1] }}
        >
          {value}
        </m.span>
      </AnimatePresence>
    </span>
  );
}

const collapseRange = () => (typeof window !== "undefined" && window.innerWidth < 768 ? 96 : 72);

/**
 * The match header on every primary page. On the Live page it opens as a hero scoreboard and
 * collapses on scroll to a slim glass bar; elsewhere it starts collapsed. The page below reserves
 * the hero height up front, so nothing jumps. Height is scroll-linked on a fixed element only.
 */
export function MatchHeader({ hero }: { hero: boolean }) {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const { scrollY } = useScroll();
  const progress = useTransform(scrollY, (y) => (hero ? Math.min(1, Math.max(0, y / collapseRange())) : 1));
  const height = useMotionTemplate`calc(var(--hdr-max) - (var(--hdr-max) - var(--hdr-min)) * ${progress})`;
  const heroOpacity = useTransform(progress, [0, 0.6], [1, 0]);
  const barOpacity = useTransform(progress, [0.4, 1], [0, 1]);

  // Only one of the two layers is exposed at a time, so the score and clock are announced once.
  const [collapsed, setCollapsed] = useState(!hero);
  useMotionValueEvent(progress, "change", (v) => setCollapsed(v > 0.5));

  const home = meta?.home;
  const away = meta?.away;
  const hs = meta && state ? (state.score[meta.home.team_id] ?? 0) : null;
  const as = meta && state ? (state.score[meta.away.team_id] ?? 0) : null;

  return (
    <m.div
      style={{ height }}
      className="fixed inset-x-0 top-[var(--topbar-h)] z-40 overflow-hidden"
      data-match-header
    >
      <m.div
        aria-hidden
        style={{ opacity: barOpacity }}
        className="glass absolute inset-0 border-x-0 border-t-0 shadow-none"
      />
      {/* Hero state */}
      {hero && (
        <m.div
          style={{ opacity: heroOpacity }}
          className="pointer-events-none absolute inset-x-0 top-0 mx-auto flex h-[var(--hdr-max)] max-w-7xl items-center justify-center px-4 pt-11 md:pt-0"
          aria-hidden={collapsed || undefined}
          role={collapsed ? undefined : "group"}
          aria-label={collapsed ? undefined : "Match score"}
        >
          <span className="absolute top-0 left-4 flex h-11 items-center font-display text-xl font-bold tracking-tight uppercase md:hidden">
            {copy.product}
          </span>
          {meta && home && away && hs !== null && as !== null ? (
            <div className="flex items-center gap-4 md:gap-10">
              <div className="flex flex-col items-center gap-1 md:flex-row md:gap-3">
                <Crest team={home} side="home" className="size-8 text-xs md:size-11 md:text-lg" />
                <span className="max-w-24 truncate text-[13px] font-medium text-muted-foreground md:max-w-none md:text-base md:text-foreground">
                  {home.name}
                </span>
              </div>
              <div className="flex flex-col items-center">
                <p className="font-display text-[48px] leading-[46px] font-bold tnum md:text-[64px] md:leading-[60px]">
                  <ScoreDigit value={hs} side="home" />
                  <span className="px-1 text-muted-foreground">-</span>
                  <ScoreDigit value={as} side="away" />
                </p>
                <div className="mt-0.5 flex items-center gap-2">
                  <span className="font-display text-base font-semibold tnum md:text-lg">{state?.minute_label}</span>
                  <ConnectionPill className="pointer-events-auto" />
                </div>
              </div>
              <div className="flex flex-col items-center gap-1 md:flex-row-reverse md:gap-3">
                <Crest team={away} side="away" className="size-8 text-xs md:size-11 md:text-lg" />
                <span className="max-w-24 truncate text-[13px] font-medium text-muted-foreground md:max-w-none md:text-base md:text-foreground">
                  {away.name}
                </span>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-8" aria-hidden>
              <Skeleton className="size-12 rounded-full" />
              <Skeleton className="h-14 w-40" />
              <Skeleton className="size-12 rounded-full" />
            </div>
          )}
        </m.div>
      )}
      {/* Collapsed state */}
      <m.div
        style={{ opacity: barOpacity }}
        aria-hidden={!collapsed || undefined}
        className="absolute inset-x-0 top-0 mx-auto flex h-[var(--hdr-min)] max-w-7xl items-center justify-center gap-3 px-4 pr-28 md:pr-4"
      >
        {meta && home && away && hs !== null && as !== null ? (
          <>
            <Crest team={home} side="home" className="size-6 text-[10px]" />
            <span className="font-display text-xl font-bold tnum">
              <span className="sr-only">{home.name} </span>
              <span aria-hidden>{home.short_name}</span> {hs} - {as} <span aria-hidden>{away.short_name}</span>
              <span className="sr-only"> {away.name}</span>
            </span>
            <Crest team={away} side="away" className="size-6 text-[10px]" />
            <span className="font-display text-base font-semibold text-muted-foreground tnum">{state?.minute_label}</span>
            <ConnectionPill className="hidden sm:flex" />
          </>
        ) : (
          <Skeleton className="h-6 w-48" aria-hidden />
        )}
      </m.div>
    </m.div>
  );
}

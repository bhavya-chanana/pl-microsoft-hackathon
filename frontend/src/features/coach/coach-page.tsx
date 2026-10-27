"use client";

import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { LayoutGroup, m } from "motion/react";
import { RotateCw } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useId, useRef, useState } from "react";

import { StablePanel } from "@/components/ui/stable-panel";
import { Button } from "@/components/ui/button";
import { useLive } from "@/features/live/live-store";
import { MatchReport } from "@/features/report/match-report";
import { StatsTab } from "@/features/coach/stats-tab";
import { TacticsRoute } from "@/features/tactics/tactics-board";
import { copy } from "@/lib/copy";
import { spring } from "@/lib/motion";
import { cn } from "@/lib/utils";

import { LineupTab } from "./lineup-tab";
import { Overview } from "./overview";
import { useCoach } from "./coach-store";

export type Tab = "overview" | "lineup" | "stats" | "tactics" | "report";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: copy.coachPage.overview },
  { id: "lineup", label: copy.coachPage.lineup },
  { id: "stats", label: copy.coachPage.stats },
  { id: "tactics", label: copy.coachPage.tactics },
  { id: "report", label: copy.coachPage.report },
];

/** Underline tab strip: 15px labels, a 2px team-colour line that slides to the active tab. */
function CoachTabs({ tab, onChange }: { tab: Tab; onChange: (t: Tab) => void }) {
  const id = useId();
  return (
    <LayoutGroup id={id}>
      <div role="tablist" aria-label={copy.coachPage.tabs} className="-mb-px flex max-w-full overflow-x-auto overscroll-x-contain [scrollbar-width:none]">
        {TABS.map((t) => {
          const active = t.id === tab;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`coach-tab-${t.id}`}
              aria-selected={active}
              aria-controls="coach-panel"
              tabIndex={active ? 0 : -1}
              onClick={() => onChange(t.id)}
              onKeyDown={(e) => {
                const i = TABS.findIndex((x) => x.id === tab);
                const next = e.key === "ArrowRight" ? TABS[(i + 1) % TABS.length] : e.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length] : null;
                if (next) {
                  e.preventDefault();
                  onChange(next.id);
                  document.getElementById(`coach-tab-${next.id}`)?.focus();
                }
              }}
              className={cn(
                "relative h-14 shrink-0 px-4 text-base font-medium whitespace-nowrap outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
              {active && (
                <m.span layoutId="coach-underline" transition={spring.snappy} className="absolute inset-x-2 bottom-0 h-[3px] rounded-t-[3px]" style={{ background: "var(--accent-team)" }} />
              )}
            </button>
          );
        })}
      </div>
    </LayoutGroup>
  );
}

function Header({ tab, onChange }: { tab: Tab; onChange: (t: Tab) => void }) {
  const meta = useLive((s) => s.meta);
  const state = useLive((s) => s.state);
  const finished = useLive((s) => s.status === "finished" || s.status === "archived");
  const picked = useCoach((s) => s.teamId);
  const client = useQueryClient();
  const fetching = useIsFetching({ queryKey: ["coach"] }) > 0;
  const sentinel = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const offset = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--stack-h")) || 52;
    const io = new IntersectionObserver(([entry]) => setStuck(!entry.isIntersecting), { rootMargin: `-${offset}px 0px 0px 0px`, threshold: 0 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  const teamId = meta ? (picked === meta.away.team_id ? meta.away.team_id : meta.home.team_id) : null;
  const half = state?.status === "half_time" && !finished;
  const phase = finished ? copy.report.phase.full : half ? copy.report.phase.half : state ? copy.coachPage.liveAt(state.minute_label) : "";
  return (
    <>
    <div ref={sentinel} aria-hidden className="h-px" />
    <div className={cn("sticky top-[var(--stack-h)] z-30 border-b transition-colors print:hidden", stuck ? "glass-strong" : "bg-background/90")}>
      <div className="mx-auto flex max-w-7xl flex-col px-4 md:px-6 min-[1100px]:flex-row min-[1100px]:items-end min-[1100px]:justify-between min-[1100px]:gap-x-4 xl:px-8">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
        {meta && teamId && (
          <div role="radiogroup" aria-label={copy.coachPage.team} className="flex gap-2">
            {(["home", "away"] as const).map((side) => {
              const t = meta[side];
              const on = teamId === t.team_id;
              return (
                <button
                  key={side}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  onClick={() => useCoach.getState().setTeam(t.team_id)}
                  className={cn(
                    "flex h-11 items-center gap-2 rounded-full px-3.5 text-[15px] font-medium ring-1 outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    on ? (side === "home" ? "bg-home/15 ring-home" : "bg-away/15 ring-away") : "text-muted-foreground ring-border hover:text-foreground",
                  )}
                >
                  <span aria-hidden className={cn("size-2.5 rounded-full", side === "home" ? "bg-home" : "bg-away")} />
                  {t.short_name}
                </button>
              );
            })}
          </div>
        )}
        {phase && <span className="rounded-full bg-raised px-3 py-1.5 text-[13px] font-medium ring-1 ring-border tabular-nums">{phase}</span>}
        <Button variant="ghost" size="sm" className="ml-auto h-11 gap-2" disabled={fetching || !teamId} onClick={() => void client.invalidateQueries({ queryKey: ["coach"] })}>
          <RotateCw aria-hidden className={cn(fetching && "motion-safe:animate-spin")} />
          {copy.coach.refresh}
        </Button>
      </div>
      <CoachTabs tab={tab} onChange={onChange} />
      </div>
    </div>
    </>
  );
}

function Body() {
  const param = useSearchParams().get("tab");
  const initial = (TABS.some((t) => t.id === param) ? param : "overview") as Tab; // ?tab=brief lands on Overview
  const [tab, setTab] = useState<Tab>(initial);
  const meta = useLive((s) => s.meta);
  const picked = useCoach((s) => s.teamId);
  const side = meta && picked === meta.away.team_id ? "away" : "home";
  const change = (next: Tab) => {
    setTab(next);
    window.history.replaceState(null, "", next === "overview" ? "/coach" : `/coach?tab=${next}`);
  };
  return (
    <div className="flex flex-col gap-4 pb-8" style={{ ["--accent-team" as string]: side === "home" ? "var(--team-home)" : "var(--team-away)" }}>
      <Header tab={tab} onChange={change} />
      <div className="mx-auto w-full max-w-7xl px-4 md:px-6 xl:px-8">
        <StablePanel panelKey={tab}>
          <div id="coach-panel" role="tabpanel" aria-labelledby={`coach-tab-${tab}`}>
            {tab === "overview" ? (
              <Overview onReport={() => change("report")} onLineup={() => change("lineup")} />
            ) : tab === "lineup" ? (
              <LineupTab />
            ) : tab === "stats" ? (
              <StatsTab />
            ) : tab === "tactics" ? (
              <TacticsRoute />
            ) : (
              <MatchReport />
            )}
          </div>
        </StablePanel>
      </div>
    </div>
  );
}

export function CoachPage() {
  return (
    <Suspense>
      <Body />
    </Suspense>
  );
}

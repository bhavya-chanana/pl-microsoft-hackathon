"use client";

import { useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Segmented } from "@/components/ui/segmented";
import { StickyBar } from "@/components/ui/sticky-bar";
import { StablePanel } from "@/components/ui/stable-panel";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { useLive } from "@/features/live/live-store";
import { MatchReport } from "@/features/report/match-report";
import { TacticsRoute } from "@/features/tactics/tactics-board";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { CoachRoom } from "./coach-room";
import { useCoach } from "./coach-store";

type Tab = "brief" | "tactics" | "report";
const TABS: Tab[] = ["brief", "tactics", "report"];

function Body() {
  const param = useSearchParams().get("tab");
  const [tab, setTab] = useState<Tab>(TABS.includes(param as Tab) ? (param as Tab) : "brief");
  const meta = useLive((s) => s.meta);
  const picked = useCoach((s) => s.teamId);
  const coachMode = useCoach((s) => s.coachMode);
  const client = useQueryClient();
  const teamId = picked ?? meta?.home.team_id ?? null;
  const change = (next: Tab) => {
    setTab(next);
    window.history.replaceState(null, "", next === "brief" ? "/coach" : `/coach?tab=${next}`);
  };
  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-6 md:px-6 xl:px-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{copy.coach.heading.replace(" brief", "")}</h1>
        <div className="flex flex-wrap items-center gap-3">
          {meta && teamId && (
            <Segmented
              label={copy.coachPage.team}
              value={teamId}
              onChange={(id) => useCoach.getState().setTeam(id)}
              className="min-w-0"
              options={[meta.home, meta.away].map((t, i) => ({
                value: t.team_id,
                label: (
                  <>
                    <span aria-hidden className={cn("size-2.5 rounded-full", i === 0 ? "bg-home" : "bg-away")} />
                    {t.short_name}
                  </>
                ),
              }))}
            />
          )}
          <Button
            variant="outline"
            size="icon"
            className="size-11"
            aria-label={copy.coach.refresh}
            onClick={() => void client.invalidateQueries({ queryKey: ["coach"] })}
          >
            <RotateCw aria-hidden />
          </Button>
          <label className="flex min-h-11 items-center gap-2 text-sm" title={copy.coachPage.coachViewHelp}>
            <Switch checked={coachMode} onCheckedChange={(v) => useCoach.getState().setCoachMode(Boolean(v))} />
            {copy.coachPage.coachView}
          </label>
        </div>
      </header>
      <StickyBar>
        <Segmented
          label={copy.coachPage.tabs}
          value={tab}
          onChange={change}
          className="max-w-md"
          options={[
            { value: "brief", label: copy.coachPage.brief },
            { value: "tactics", label: copy.coachPage.tactics },
            { value: "report", label: copy.coachPage.report },
          ]}
        />
      </StickyBar>
      <StablePanel panelKey={tab}>
        {tab === "brief" ? <CoachRoom onReport={() => change("report")} /> : tab === "tactics" ? <TacticsRoute /> : <MatchReport />}
      </StablePanel>
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

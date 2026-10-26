"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Segmented } from "@/components/ui/segmented";
import { StablePanel } from "@/components/ui/stable-panel";
import { TacticsRoute } from "@/features/tactics/tactics-board";
import { copy } from "@/lib/copy";

import { CoachRoom } from "./coach-room";

type Tab = "brief" | "tactics";

function Body() {
  const param = useSearchParams().get("tab");
  const [tab, setTab] = useState<Tab>(param === "tactics" ? "tactics" : "brief");
  const change = (next: Tab) => {
    setTab(next);
    window.history.replaceState(null, "", next === "brief" ? "/coach" : `/coach?tab=${next}`);
  };
  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 xl:px-8">
      <Segmented
        label={copy.coachPage.tabs}
        value={tab}
        onChange={change}
        className="max-w-sm"
        options={[
          { value: "brief", label: copy.coachPage.brief },
          { value: "tactics", label: copy.coachPage.tactics },
        ]}
      />
      <StablePanel panelKey={tab}>{tab === "brief" ? <CoachRoom /> : <TacticsRoute />}</StablePanel>
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

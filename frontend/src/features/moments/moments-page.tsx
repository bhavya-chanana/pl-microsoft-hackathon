"use client";

import { History } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { StablePanel } from "@/components/ui/stable-panel";
import { useShell } from "@/components/shell/shell-store";
import { InsightFeed } from "@/features/companion/insight-feed";
import { MatchTimeline } from "@/features/companion/match-timeline";
import { useLive } from "@/features/live/live-store";
import type { FeedItem } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

type View = "highlights" | "timeline";
type Kind = "all" | "goals" | "cards" | "shots" | "insights";
type Team = "both" | "home" | "away";

const SHOT_SIGNALS = new Set(["big_chance", "big_save", "shot_speed_badge"]);
const KINDS: Kind[] = ["all", "goals", "cards", "shots", "insights"];

function matches(kind: Kind, item: FeedItem): boolean {
  switch (kind) {
    case "all":
      return true;
    case "goals":
      return item.signal_type === "goal";
    case "cards":
      return item.signal_type === "card";
    case "shots":
      return SHOT_SIGNALS.has(item.signal_type);
    case "insights":
      return !["goal", "card"].includes(item.signal_type) && !SHOT_SIGNALS.has(item.signal_type);
  }
}

function Chip({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "h-11 shrink-0 rounded-full px-4 text-sm font-medium ring-1 transition-colors",
        active ? "bg-foreground text-background ring-foreground" : "bg-raised text-muted-foreground ring-border hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/** Moments: big cards for goals and cards, quiet rows for the rest, with filters and the full timeline. */
export function MomentsPage() {
  const [view, setView] = useState<View>("highlights");
  const [kind, setKind] = useState<Kind>("all");
  const [team, setTeam] = useState<Team>("both");
  const meta = useLive((s) => s.meta);
  const { setCatchup, setRecap } = useShell.getState();
  const filter = (item: FeedItem) => matches(kind, item) && (team === "both" || item.team_id === meta?.[team].team_id);
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 pt-6 pb-8 md:px-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{copy.momentsPage.heading}</h1>
        <p className="text-muted-foreground">{copy.momentsPage.intro}</p>
      </header>
      <div className="flex flex-wrap gap-3">
        <Button className="h-11 gap-2 px-5" onClick={() => setCatchup(true)} disabled={!meta}>
          <History aria-hidden />
          {copy.catchup.cta}
        </Button>
        <Button variant="secondary" className="h-11 px-5" onClick={() => setRecap(true)} disabled={!meta}>
          {copy.recap.cta}
        </Button>
      </div>
      <div className="sticky top-[var(--stack-h)] z-30 -mx-4 flex flex-col gap-2 bg-background px-4 py-2 md:-mx-6 md:px-6">
        <Segmented
          label={copy.momentsPage.heading}
          value={view}
          onChange={setView}
          options={[
            { value: "highlights", label: copy.momentsPage.highlights },
            { value: "timeline", label: copy.tabs.timeline },
          ]}
        />
        {view === "highlights" && (
          <div role="group" aria-label={copy.momentsPage.filters} className="-mx-4 flex gap-2 overflow-x-auto overscroll-x-contain px-4 md:mx-0 md:px-0">
            {KINDS.map((k) => (
              <Chip key={k} active={kind === k} onClick={() => setKind(k)}>
                {copy.momentsPage.kinds[k]}
              </Chip>
            ))}
            {meta && (
              <>
                <span aria-hidden className="mx-1 w-px shrink-0 bg-border" />
                <Chip active={team === "home"} onClick={() => setTeam(team === "home" ? "both" : "home")}>
                  {meta.home.short_name}
                </Chip>
                <Chip active={team === "away"} onClick={() => setTeam(team === "away" ? "both" : "away")}>
                  {meta.away.short_name}
                </Chip>
              </>
            )}
          </div>
        )}
      </div>
      <StablePanel panelKey={view}>
        {view === "highlights" ? (
          <InsightFeed filter={filter} />
        ) : (
          <div className="rounded-2xl border bg-card px-4">
            <MatchTimeline />
          </div>
        )}
      </StablePanel>
    </div>
  );
}

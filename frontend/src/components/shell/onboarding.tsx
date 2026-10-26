"use client";

import { useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Segmented } from "@/components/ui/segmented";
import { useLive } from "@/features/live/live-store";
import { useProfile } from "@/features/profile/profile-store";
import type { Mode } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const KEY = "matchlens.onboarded.v1";

const subscribe = () => () => {};

function seen(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return true;
  }
}

/** First visit only: pick a club and how you like it told. Everything else lives in the profile menu. */
export function OnboardingSheet() {
  const meta = useLive((s) => s.meta);
  const clubId = useProfile((s) => s.clubId);
  const mode = useProfile((s) => s.mode);
  const setClub = useProfile((s) => s.setClub);
  const setMode = useProfile((s) => s.setMode);
  const [dismissed, setDismissed] = useState(false);
  const alreadySeen = useSyncExternalStore(subscribe, seen, () => true);
  const open = Boolean(meta) && !alreadySeen && !dismissed;

  const done = () => {
    try {
      localStorage.setItem(KEY, "1");
    } catch {
      /* storage can be blocked */
    }
    setDismissed(true);
  };
  if (!meta) return null;
  const clubs = [
    { id: meta.home.team_id, name: meta.home.name, tone: "bg-home" },
    { id: meta.away.team_id, name: meta.away.name, tone: "bg-away" },
    { id: null, name: copy.profile.noClub, tone: "bg-subtle" },
  ];
  return (
    <Dialog open={open} onOpenChange={(o) => !o && done()}>
      <DialogContent
        showCloseButton={false}
        className="gap-5 p-6 sm:max-w-md max-sm:top-auto max-sm:bottom-0 max-sm:max-w-none max-sm:translate-y-0 max-sm:rounded-b-none max-sm:rounded-t-[28px] max-sm:pb-[calc(1.5rem+env(safe-area-inset-bottom))]"
      >
        <div className="flex flex-col gap-1">
          <DialogTitle className="text-2xl font-semibold">{copy.onboarding.title}</DialogTitle>
          <DialogDescription>{copy.onboarding.help}</DialogDescription>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {clubs.map((c) => (
            <button
              key={String(c.id)}
              type="button"
              aria-pressed={clubId === c.id}
              onClick={() => setClub(c.id)}
              className={cn(
                "flex min-h-24 flex-col items-center justify-center gap-2 rounded-2xl p-2 text-center text-sm font-medium ring-1 transition-colors",
                clubId === c.id ? "bg-raised ring-2 ring-primary" : "ring-border hover:bg-raised",
              )}
            >
              <span aria-hidden className={cn("size-9 rounded-full", c.tone)} />
              {c.name}
            </button>
          ))}
        </div>
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">{copy.onboarding.style}</p>
          <Segmented<Mode>
            label={copy.onboarding.style}
            value={mode === "analyst" ? "analyst" : "casual"}
            onChange={setMode}
            options={[
              { value: "casual", label: copy.modes.casual },
              { value: "analyst", label: copy.modes.analyst },
            ]}
          />
          <p className="text-[13px] text-muted-foreground">{copy.modeHelp[mode === "analyst" ? "analyst" : "casual"]}</p>
        </div>
        <Button size="lg" className="h-12 text-base" onClick={done}>
          {copy.onboarding.start}
        </Button>
      </DialogContent>
    </Dialog>
  );
}

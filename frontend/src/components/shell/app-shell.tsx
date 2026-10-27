"use client";

import { usePathname, useRouter } from "next/navigation";
import { toast } from "sonner";
import { useGoalCall } from "@/features/broadcast/goal-takeover";
import { playerName } from "@/lib/match";
import { OnboardingSheet } from "./onboarding";
import { useEffect, type ReactNode } from "react";

import { usePrefs } from "@/features/profile/prefs-store";
import { useLive } from "@/features/live/live-store";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";

import { CatchupDialog, RecapDialog } from "@/features/companion/catchup-dialog";
import { PlayerSheet } from "@/features/player/player-sheet";
import { PunditPanel } from "@/features/pundit/pundit-panel";
import { useShell } from "./shell-store";
import { CommandPalette } from "./command-palette";
import { StreamHost } from "@/features/stream/stream-host";

import { DevDrawer } from "./dev-drawer";
import { MatchHeader } from "./match-header";
import { TabBar } from "./tab-bar";
import { TopBar } from "./top-bar";
import { ProfileMenu } from "./profile-menu";
import { CommandChip } from "./command-palette";
import { StreamButton } from "./stream-button";

/** Team colours from the match data, normalised to a readable lightness on dark. */
function useTeamColours() {
  const meta = useLive((s) => s.meta);
  useEffect(() => {
    const root = document.documentElement;
    const ok = typeof CSS !== "undefined" && CSS.supports("color", "oklch(from red l c h)");
    if (!meta || !ok) return;
    const norm = (c: string) => `oklch(from ${c} clamp(0.66, l, 0.8) min(c, 0.17) h)`;
    root.style.setProperty("--team-home", norm(meta.home.colour));
    root.style.setProperty("--team-away", norm(meta.away.colour));
    return () => {
      root.style.removeProperty("--team-home");
      root.style.removeProperty("--team-away");
    };
  }, [meta]);
}

/** A toast when a goal goes in while you are on another page. The Live page has its own goal chip. */
function GoalToaster() {
  const goal = useGoalCall(false);
  const pathname = usePathname();
  const router = useRouter();
  const meta = useLive((s) => s.meta);
  const id = goal?.signal_id;
  const notify = usePrefs((s) => s.notifyGoals);
  useEffect(() => {
    if (!goal || pathname === routes.live || !notify) return;
    const scorer = playerName(meta, goal.player_ids[0]);
    toast(`GOAL ${goal.minute_label}${scorer ? ` ${scorer}` : ""}`, { action: { label: copy.toast.view, onClick: () => router.push(routes.live) } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  return null;
}

/** Catch me up, Full recap and Ask the pundit, opened from anywhere (pill, Moments page, palette). */
function Drawers() {
  const catchup = useShell((s) => s.catchupOpen);
  const recap = useShell((s) => s.recapOpen);
  const pundit = useShell((s) => s.punditOpen);
  const playerId = useShell((s) => s.playerId);
  const { setCatchup, setRecap, setPundit } = useShell.getState();
  return (
    <>
      <CatchupDialog open={catchup} onOpenChange={setCatchup} />
      <RecapDialog open={recap} onOpenChange={setRecap} />
      <PunditPanel open={pundit} onOpenChange={setPundit} />
      <PlayerSheet playerId={playerId} onClose={() => useShell.getState().setPlayerId(null)} returnFocus={() => {}} />
    </>
  );
}

/**
 * Fixed chrome (top bar, match header, tab bar, palette, drawers) as siblings of the page, so the
 * document is the only scroll container. The wrapper reserves the space the chrome covers.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  useTeamColours();
  if (pathname === routes.broadcast) return <main>{children}</main>;
  const hero = pathname === routes.live;
  return (
    <>
      <a
        href="#main"
        className="sr-only z-[100] rounded-full bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        {copy.nav.skip}
      </a>
      <div aria-hidden className="match-ambient fixed inset-0 -z-10" />
      <TopBar />
      <MatchHeader hero={hero} />
      {/* Mobile has no top bar: search and profile sit in the header's top-right corner. */}
      <div className="fixed top-0 right-2 z-50 flex h-11 items-center gap-1 md:hidden">
        <StreamButton />
        <CommandChip />
        <ProfileMenu />
      </div>
      <main
        id="main"
        className="min-h-dvh pb-[var(--tabbar-h)] md:pb-0"
        style={{ paddingTop: `calc(var(--topbar-h) + var(${hero ? "--hdr-max" : "--hdr-min"}))` }}
      >
        {children}
      </main>
      <div aria-hidden className="pointer-events-none fixed inset-x-0 bottom-0 z-40 h-32 bg-gradient-to-t from-background via-background/85 to-transparent md:hidden" />
      <TabBar />
      <CommandPalette />
      <DevDrawer />
      <StreamHost />
      <Drawers />
      <GoalToaster />
      <OnboardingSheet />
    </>
  );
}

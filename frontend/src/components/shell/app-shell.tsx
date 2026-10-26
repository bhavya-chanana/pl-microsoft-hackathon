"use client";

import { usePathname } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useLive } from "@/features/live/live-store";
import { routes } from "@/lib/routes";

import { CommandPalette } from "./command-palette";
import { DevDrawer } from "./dev-drawer";
import { MatchHeader } from "./match-header";
import { TabBar } from "./tab-bar";
import { TopBar } from "./top-bar";
import { ProfileMenu } from "./profile-menu";
import { CommandChip } from "./command-palette";

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
      <div aria-hidden className="match-ambient fixed inset-0 -z-10" />
      <TopBar />
      <MatchHeader hero={hero} />
      {/* Mobile has no top bar: search and profile sit in the header's top-right corner. */}
      <div className="fixed top-1 right-2 z-50 flex items-center gap-1 md:hidden">
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
      <TabBar />
      <CommandPalette />
      <DevDrawer />
    </>
  );
}

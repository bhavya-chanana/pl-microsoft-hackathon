"use client";

import { Code2, Moon, Sun, User } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ListenToggle } from "@/features/companion/narration";
import { ProfilePanel } from "@/features/profile/profile-panel";
import { copy } from "@/lib/copy";

import { useShell } from "./shell-store";

/** The avatar chip: your style, language, club, theme and the developer tools. Replaces the sidebar dropdowns. */
export function ProfileMenu() {
  const { resolvedTheme, setTheme } = useTheme();
  const showDev = useShell((s) => s.showDev);
  const setDev = useShell((s) => s.setDev);
  const dark = resolvedTheme !== "light";
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button variant="ghost" size="icon" className="size-11 rounded-full bg-foreground/10 ring-1 ring-foreground/10 hover:bg-foreground/15" aria-label={copy.profile.menu} />
        }
      >
        <User aria-hidden className="size-5" />
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={10} className="max-h-[min(80dvh,40rem)] w-[min(22rem,calc(100vw-1.5rem))] gap-4 overflow-y-auto overscroll-contain p-4">
        <ProfilePanel />
        <div className="border-t pt-3">
          <ListenToggle />
        </div>
        <div className="flex flex-col gap-1 border-t pt-3">
          <Button variant="ghost" className="h-11 justify-start gap-3 px-2" onClick={() => setTheme(dark ? "light" : "dark")}>
            {dark ? <Sun aria-hidden /> : <Moon aria-hidden />}
            {dark ? copy.theme.light : copy.theme.dark}
          </Button>
          {showDev && (
            <Button variant="ghost" className="h-11 justify-start gap-3 px-2" onClick={() => setDev(true)}>
              <Code2 aria-hidden />
              {copy.nav.dev}
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

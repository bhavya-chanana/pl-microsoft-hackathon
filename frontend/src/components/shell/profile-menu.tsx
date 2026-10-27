"use client";

import { ChevronDown, Code2, SlidersHorizontal, User } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useCoach } from "@/features/coach/coach-store";
import { ClubChip, Field, MoreFields, StyleAndLanguage } from "@/features/profile/profile-panel";
import { usePrefs } from "@/features/profile/prefs-store";
import { copy } from "@/lib/copy";

import { useShell } from "./shell-store";

function SwitchRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm font-medium">
      {label}
      <Switch checked={checked} onCheckedChange={onChange} aria-label={label} />
    </label>
  );
}

/** The avatar chip. One opaque panel: club, style, language, theme, coach view, notifications, then More. */
export function ProfileMenu() {
  const { resolvedTheme, setTheme } = useTheme();
  const showDev = useShell((s) => s.showDev);
  const setDev = useShell((s) => s.setDev);
  const coachMode = useCoach((s) => s.coachMode);
  const setCoachMode = useCoach((s) => s.setCoachMode);
  const notify = usePrefs((s) => s.notifyGoals);
  const setNotify = usePrefs((s) => s.setNotifyGoals);
  const theme = resolvedTheme === "light" ? "light" : "dark";
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button variant="ghost" size="icon" className="size-11 rounded-full bg-foreground/10 ring-1 ring-foreground/10 hover:bg-foreground/15" aria-label={copy.profile.menu} />
        }
      >
        <User aria-hidden className="size-5" />
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={10}
        className="max-h-[min(80dvh,40rem)] w-[min(20rem,calc(100vw-1.5rem))] gap-3 overflow-y-auto overscroll-contain bg-card! p-4 backdrop-blur-none!"
      >
        <div className="flex items-center gap-3">
          <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-full bg-foreground/10 ring-1 ring-foreground/10">
            <User className="size-5" />
          </span>
          <ClubChip />
        </div>
        <StyleAndLanguage />
        <Field label={copy.profile.theme} id="theme-label">
          <ToggleGroup
            aria-labelledby="theme-label"
            value={[theme]}
            onValueChange={(value: string[]) => value[0] && setTheme(value[0])}
            spacing={0}
            variant="outline"
            className="w-full"
          >
            <ToggleGroupItem value="dark" className="h-10 flex-1 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
              Dark
            </ToggleGroupItem>
            <ToggleGroupItem value="light" className="h-10 flex-1 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
              Light
            </ToggleGroupItem>
          </ToggleGroup>
        </Field>
        <Button variant="secondary" className="h-11 justify-start gap-3 px-3" onClick={() => useShell.getState().setStream(true)}>
          <SlidersHorizontal aria-hidden />
          {copy.stream.open}
        </Button>
        <div className="flex flex-col border-t pt-1">
          <SwitchRow label={copy.profile.coachView} checked={coachMode} onChange={setCoachMode} />
          <SwitchRow label={copy.profile.notifications} checked={notify} onChange={setNotify} />
        </div>
        <details className="group border-t pt-2">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between text-sm font-medium [&::-webkit-details-marker]:hidden">
            {copy.profile.more}
            <ChevronDown aria-hidden className="size-4 transition-transform group-open:rotate-180" />
          </summary>
          <div className="flex flex-col gap-3 pt-2">
            {!coachMode && <MoreFields />}
            {showDev && (
              <Button variant="ghost" className="h-11 justify-start gap-3 px-2" onClick={() => setDev(true)}>
                <Code2 aria-hidden />
                {copy.nav.dev}
              </Button>
            )}
          </div>
        </details>
      </PopoverContent>
    </Popover>
  );
}

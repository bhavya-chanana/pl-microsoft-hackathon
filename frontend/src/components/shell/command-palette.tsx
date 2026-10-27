"use client";

import { Code2, Compass, History, MessageCircleQuestion, Moon, Newspaper, Radio, Search, Sparkles, ChartColumn, ClipboardList, Route } from "lucide-react";
import { useTheme } from "next-themes";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";

import { PaletteSearch } from "./palette-search";
import { useShell } from "./shell-store";

/** The ⌘K chip in the top bar (icon only below 1024px). */
export function CommandChip() {
  const open = useShell((s) => s.setPalette);
  return (
    <Button
      variant="ghost"
      className="h-10 gap-2 rounded-full bg-foreground/5 px-3 text-muted-foreground ring-1 ring-foreground/10 hover:bg-foreground/10"
      onClick={() => open(true)}
      aria-label={copy.palette.open}
    >
      <Search aria-hidden className="size-4" />
      <span className="hidden lg:inline">{copy.palette.placeholder}</span>
      <kbd className="hidden rounded-md bg-foreground/10 px-1.5 py-0.5 font-mono text-xs lg:inline">⌘K</kbd>
    </Button>
  );
}

/** Go-to and do-this commands. Opens with ⌘K or Ctrl-K. */
export function CommandPalette() {
  const router = useRouter();
  const open = useShell((s) => s.paletteOpen);
  const setOpen = useShell((s) => s.setPalette);
  const setDev = useShell((s) => s.setDev);
  const showDev = useShell((s) => s.showDev);
  const { resolvedTheme, setTheme } = useTheme();
  const [query, setQuery] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        useShell.getState().setPalette(!useShell.getState().paletteOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = (href: string) => () => {
    setOpen(false);
    router.push(href);
  };
  const run = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };
  return (
    <CommandDialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQuery(""); }} title={copy.palette.title} description={copy.palette.placeholder} className="glass-strong sm:max-w-xl">
      <Command className="bg-transparent">
      <CommandInput placeholder={copy.palette.placeholder} value={query} onValueChange={setQuery} />
      <CommandList className="max-h-[60dvh] overscroll-contain">
        {query.trim().length >= 2 && <PaletteSearch close={() => setOpen(false)} />}
        <CommandEmpty>{copy.palette.empty}</CommandEmpty>
        <CommandGroup heading={copy.palette.do}>
          <CommandItem onSelect={run(() => useShell.getState().setCatchup(true))}>
            <History aria-hidden />
            {copy.catchup.cta}
          </CommandItem>
          <CommandItem onSelect={run(() => useShell.getState().setPundit(true))}>
            <MessageCircleQuestion aria-hidden />
            {copy.pundit.open}
          </CommandItem>
          <CommandItem onSelect={run(() => useShell.getState().setRecap(true))}>
            <Newspaper aria-hidden />
            {copy.recap.cta}
          </CommandItem>
          <CommandItem onSelect={go(routes.coachTactics)}>
            <Route aria-hidden />
            {copy.palette.buildIdeas}
          </CommandItem>
          <CommandItem onSelect={run(() => setTheme(resolvedTheme === "light" ? "dark" : "light"))}>
            <Moon aria-hidden />
            {copy.palette.theme}
          </CommandItem>
          {showDev && (
            <CommandItem onSelect={run(() => setDev(true))}>
              <Code2 aria-hidden />
              {copy.nav.dev}
            </CommandItem>
          )}
        </CommandGroup>
        <CommandGroup heading={copy.palette.go}>
          <CommandItem onSelect={go(routes.live)}><Radio aria-hidden />{copy.nav.live}</CommandItem>
          <CommandItem onSelect={go(routes.moments)}><Sparkles aria-hidden />{copy.nav.moments}</CommandItem>
          <CommandItem onSelect={go(routes.stats)}><ChartColumn aria-hidden />{copy.nav.stats}</CommandItem>
          <CommandItem onSelect={go(routes.coach)}><ClipboardList aria-hidden />{copy.nav.coach}</CommandItem>
          <CommandItem onSelect={go(routes.coachTactics)}><Route aria-hidden />{copy.nav.tactics}</CommandItem>
          <CommandItem onSelect={go(routes.explore)}><Compass aria-hidden />{copy.nav.explore}</CommandItem>
        </CommandGroup>
      </CommandList>
      </Command>
    </CommandDialog>
  );
}

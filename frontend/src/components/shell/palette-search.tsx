"use client";

import { ChartColumn, Sparkles, User } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { CommandGroup, CommandItem } from "@/components/ui/command";
import { describeEvent } from "@/features/companion/match-timeline";
import { useLive } from "@/features/live/live-store";
import { copy, eventLabels } from "@/lib/copy";
import { routes } from "@/lib/routes";

import { useShell } from "./shell-store";

/** Players, moments and stat names from the live store, shown once the fan has typed something. */
export function PaletteSearch({ close }: { close: () => void }) {
  const router = useRouter();
  const meta = useLive((s) => s.meta);
  const timeline = useLive((s) => s.timeline);
  const insights = useLive((s) => s.insights);

  const players = useMemo(() => {
    if (!meta) return [];
    return [meta.home, meta.away].flatMap((team) =>
      [...team.starting_xi, ...team.bench].flatMap((id) => {
        const p = meta.players[id];
        return p ? [{ id, label: p.name, detail: `#${p.shirt} ${team.name}`, value: `${p.name} ${p.short_name} ${p.shirt} ${team.name} ${p.position}` }] : [];
      }),
    );
  }, [meta]);

  const moments = useMemo(() => {
    const events = timeline
      .filter((e) => ["goal", "card", "substitution", "save"].includes(e.type))
      .slice()
      .reverse()
      .map((e) => {
        const text = describeEvent(e, meta);
        const label = `${eventLabels[e.type] ?? e.type}: ${text}`;
        return { id: e.event_id, minute: e.minute_label, label, value: `${e.minute_label} ${label} ${e.outcome}` };
      });
    const insightRows = Object.values(insights)
      .filter((i) => i.stage !== "retracted")
      .sort((a, b) => b.elapsed - a.elapsed)
      .map((i) => ({ id: i.insight_id, minute: i.minute_label, label: i.headline, value: `${i.minute_label} ${i.headline}` }));
    return [...insightRows, ...events].slice(0, 80);
  }, [timeline, insights, meta]);

  const stats = useMemo(() => {
    if (!meta) return [];
    const count = (type: string, outcome?: string) => {
      const pick = (id: string) => timeline.filter((e) => e.type === type && e.team_id === id && (!outcome || e.outcome === outcome)).length;
      return `${meta.home.short_name} ${pick(meta.home.team_id)}, ${meta.away.short_name} ${pick(meta.away.team_id)}`;
    };
    const names = [
      ["Goals", count("goal")],
      ["Yellow cards", count("card", "yellow")],
      ["Red cards", count("card", "red")],
      ["Substitutions", count("substitution")],
      ["Shots", ""],
      ["Shots on target", ""],
      ["Possession", ""],
      ["Chances created (xG)", ""],
      ["Passes", ""],
      ["Corners", ""],
      ["Fouls", ""],
    ] as const;
    return names.map(([label, detail]) => ({ label, detail, value: `${label} ${detail} stat stats` }));
  }, [meta, timeline]);

  const go = (href: string) => () => {
    close();
    router.push(href);
  };
  return (
    <>
      {players.length > 0 && (
        <CommandGroup heading={copy.palette.players}>
          {players.map((p) => (
            <CommandItem
              key={p.id}
              value={p.value}
              onSelect={() => {
                close();
                useShell.getState().setPlayerId(p.id);
              }}
            >
              <User aria-hidden />
              {p.label}
              <span className="ml-auto text-xs text-muted-foreground">{p.detail}</span>
            </CommandItem>
          ))}
        </CommandGroup>
      )}
      {moments.length > 0 && (
        <CommandGroup heading={copy.palette.moments}>
          {moments.map((m) => (
            <CommandItem key={m.id} value={m.value} onSelect={go(routes.moments)}>
              <Sparkles aria-hidden />
              <span className="truncate">{m.label}</span>
              <span className="ml-auto shrink-0 text-xs text-muted-foreground tnum">{m.minute}</span>
            </CommandItem>
          ))}
        </CommandGroup>
      )}
      {stats.length > 0 && (
        <CommandGroup heading={copy.palette.stats}>
          {stats.map((s) => (
            <CommandItem key={s.label} value={s.value} onSelect={go(routes.stats)}>
              <ChartColumn aria-hidden />
              {s.label}
              {s.detail && <span className="ml-auto text-xs text-muted-foreground">{s.detail}</span>}
            </CommandItem>
          ))}
        </CommandGroup>
      )}
    </>
  );
}

"use client";

import { useId } from "react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { BoardSource } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

const t = copy.tactics.moment;

export type MomentOption = { insight_id: string; minute_label: string; headline: string; elapsed: number };
export type TeamOption = { team_id: string; name: string; side: "home" | "away" };

type Props = {
  source: BoardSource;
  onSource: (source: BoardSource) => void;
  moments: MomentOption[];
  /** Latest match time in seconds; the slider runs from kick-off to here. */
  maxElapsed: number;
  teams: TeamOption[];
  teamId: string | null;
  onTeam: (teamId: string) => void;
};

/** Which moment to freeze (now, a key moment, or any minute) and which side the ideas are for. */
export function MomentPicker({ source, onSource, moments, maxElapsed, teams, teamId, onTeam }: Props) {
  const id = useId();
  const minute = Math.round((source.elapsed ?? maxElapsed) / 60);
  const maxMinute = Math.max(1, Math.ceil(maxElapsed / 60));
  return (
    <fieldset className="flex min-w-0 flex-col gap-4">
      <legend className="mb-1 font-display text-lg font-semibold">{t.heading}</legend>

      <ToggleGroup
        aria-label={t.heading}
        value={[source.kind]}
        onValueChange={(v) => {
          const kind = v[0] as BoardSource["kind"] | undefined;
          if (!kind) return;
          if (kind === "live") onSource({ kind });
          if (kind === "moment") onSource({ kind, insight_id: moments[0]?.insight_id });
          if (kind === "elapsed") onSource({ kind, elapsed: source.elapsed ?? maxElapsed });
        }}
        variant="outline"
        className="w-full"
      >
        <ToggleGroupItem value="live" className="h-11 flex-1 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
          {t.now}
        </ToggleGroupItem>
        <ToggleGroupItem value="moment" className="h-11 flex-1 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
          {t.key}
        </ToggleGroupItem>
        <ToggleGroupItem value="elapsed" className="h-11 flex-1 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
          {t.time}
        </ToggleGroupItem>
      </ToggleGroup>

      {source.kind === "live" && <p className="text-sm text-muted-foreground">{t.nowHelp}</p>}

      {source.kind === "moment" &&
        (moments.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t.noKeyMoments}</p>
        ) : (
          <div role="radiogroup" aria-label={t.key} className="flex max-h-60 flex-col gap-1.5 overflow-y-auto pr-1">
            {moments.map((m) => {
              const checked = source.insight_id === m.insight_id;
              return (
                <label
                  key={m.insight_id}
                  className={cn(
                    "flex min-h-11 cursor-pointer items-baseline gap-3 rounded-md border px-3 py-2 text-sm transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ring",
                    checked ? "border-ring bg-muted" : "hover:bg-muted",
                  )}
                >
                  <input
                    type="radio"
                    name={`${id}-moment`}
                    checked={checked}
                    onChange={() => onSource({ kind: "moment", insight_id: m.insight_id })}
                    className="sr-only"
                  />
                  <span className="w-9 shrink-0 text-right font-display font-semibold tabular-nums">{m.minute_label}</span>
                  <span className="min-w-0">{m.headline}</span>
                </label>
              );
            })}
          </div>
        ))}

      {source.kind === "elapsed" && (
        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-minute`} className="flex items-baseline justify-between text-sm font-medium">
            {t.slider}
            <span className="font-display text-base tabular-nums">{t.minute(minute)}</span>
          </label>
          <input
            id={`${id}-minute`}
            type="range"
            min={0}
            max={maxMinute}
            step={1}
            value={Math.min(minute, maxMinute)}
            onChange={(e) => onSource({ kind: "elapsed", elapsed: Number(e.target.value) * 60 })}
            aria-valuetext={t.minute(minute)}
            className="h-11 w-full cursor-pointer accent-primary"
          />
        </div>
      )}

      {teams.length > 0 && (
        <div className="flex flex-col gap-2">
          <span id={`${id}-team`} className="text-sm font-medium">
            {t.team}
          </span>
          <ToggleGroup aria-labelledby={`${id}-team`} value={teamId ? [teamId] : []} onValueChange={(v) => v[0] && onTeam(v[0])} variant="outline" className="w-full">
            {teams.map((team) => (
              <ToggleGroupItem key={team.team_id} value={team.team_id} className="h-11 flex-1 gap-2 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
                <span aria-hidden className={cn("size-2.5 shrink-0 rounded-full", sideTone[team.side].bg)} />
                <span className="truncate">{team.name}</span>
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <p className="text-xs text-muted-foreground">{t.teamHelp}</p>
        </div>
      )}
    </fieldset>
  );
}

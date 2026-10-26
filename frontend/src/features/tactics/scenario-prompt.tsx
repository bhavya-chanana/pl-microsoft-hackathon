"use client";

import { useId } from "react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { BOARD_RISKS, type BoardRisk } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const t = copy.tactics.prompt;
export const MAX_PROMPT = 400;
export const IDEA_COUNTS = [3, 4, 5] as const;
export const HORIZON_RANGE = { min: 5, max: 10 } as const;

export type PromptSettings = {
  prompt: string;
  risk: BoardRisk;
  n: number;
  horizon: number;
};

type Props = PromptSettings & {
  onChange: (next: Partial<PromptSettings>) => void;
};

/** The brief for the board: free text, quick starts, how bold to be, how many ideas and how far ahead. */
export function ScenarioPrompt({ prompt, risk, n, horizon, onChange }: Props) {
  const id = useId();
  const tooLong = prompt.length > MAX_PROMPT;
  return (
    <fieldset className="flex min-w-0 flex-col gap-4">
      <legend className="mb-1 font-display text-lg font-semibold">{t.heading}</legend>

      <div className="flex flex-col gap-2">
        <label htmlFor={`${id}-prompt`} className="text-sm font-medium">
          {t.label}
        </label>
        <textarea
          id={`${id}-prompt`}
          rows={3}
          value={prompt}
          maxLength={MAX_PROMPT + 100}
          onChange={(e) => onChange({ prompt: e.target.value })}
          placeholder={t.placeholder}
          aria-invalid={tooLong}
          aria-describedby={`${id}-help ${id}-count`}
          className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <div className="flex items-start justify-between gap-3">
          <p id={`${id}-help`} className="text-xs text-muted-foreground">
            {t.help}
          </p>
          <span id={`${id}-count`} className={cn("shrink-0 text-xs tabular-nums", tooLong ? "text-destructive" : "text-muted-foreground")}>
            {t.length(prompt.length, MAX_PROMPT)}
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <p id={`${id}-presets`} className="text-xs font-medium text-muted-foreground">
          {t.presets}
        </p>
        <ul aria-labelledby={`${id}-presets`} className="flex flex-wrap gap-2">
          {t.presetList.map((p) => (
            <li key={p.label}>
              <button
                type="button"
                onClick={() => onChange({ prompt: p.text })}
                aria-pressed={prompt === p.text}
                className={cn(
                  "min-h-11 rounded-full border px-3 text-left text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                  "hover:bg-muted aria-pressed:border-ring aria-pressed:bg-muted",
                )}
              >
                {p.label}
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="flex flex-col gap-2">
        <span id={`${id}-risk`} className="text-sm font-medium">
          {t.risk}
        </span>
        <ToggleGroup aria-labelledby={`${id}-risk`} value={[risk]} onValueChange={(v) => v[0] && onChange({ risk: v[0] as BoardRisk })} variant="outline" className="w-full">
          {BOARD_RISKS.map((r) => (
            <ToggleGroupItem key={r} value={r} className="h-11 flex-1 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
              {t.risks[r]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
        <div className="flex flex-col gap-2">
          <span id={`${id}-n`} className="text-sm font-medium">
            {t.ideas}
          </span>
          <ToggleGroup aria-labelledby={`${id}-n`} value={[String(n)]} onValueChange={(v) => v[0] && onChange({ n: Number(v[0]) })} variant="outline">
            {IDEA_COUNTS.map((c) => (
              <ToggleGroupItem key={c} value={String(c)} className="h-11 min-w-11 px-2 tabular-nums aria-pressed:bg-primary aria-pressed:text-primary-foreground">
                {c}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <div className="flex min-w-40 flex-1 flex-col gap-1">
          <label htmlFor={`${id}-horizon`} className="flex items-baseline justify-between text-sm font-medium">
            {t.horizon}
            <span className="font-display text-base tabular-nums">{t.horizonValue(horizon)}</span>
          </label>
          <input
            id={`${id}-horizon`}
            type="range"
            min={HORIZON_RANGE.min}
            max={HORIZON_RANGE.max}
            step={1}
            value={horizon}
            onChange={(e) => onChange({ horizon: Number(e.target.value) })}
            aria-valuetext={t.horizonValue(horizon)}
            className="h-11 w-full cursor-pointer accent-primary"
          />
        </div>
      </div>
    </fieldset>
  );
}

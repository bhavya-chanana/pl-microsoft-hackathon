"use client";

import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useLive } from "@/features/live/live-store";
import { LANGUAGES, type Language, METRICS, type Metric, MODES, type Mode } from "@/lib/contract";
import { copy } from "@/lib/copy";

import { useProfile } from "./profile-store";

const NONE = "none";

export function Field({ label, id, children }: { label: string; id: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span id={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </span>
      {children}
    </div>
  );
}

export const trigger = "h-11 w-full min-w-0 data-[size=default]:h-11";

/** The "More" fields: favourite player and headline metric. */
export function MoreFields() {
  const meta = useLive((s) => s.meta);
  const { playerId, metric, setPlayer, setMetric } = useProfile();
  const metricItems: Record<string, string> = { [NONE]: copy.profile.noMetric, ...copy.metrics };
  const teams = meta ? [meta.home, meta.away] : [];
  const playerItems: Record<string, string> = { [NONE]: copy.profile.noPlayer };
  for (const team of teams) {
    for (const id of [...team.starting_xi, ...team.bench]) {
      const p = meta?.players[id];
      if (p) playerItems[id] = `#${p.shirt} ${p.name}`;
    }
  }
  return (
    <div className="flex flex-col gap-3">
      <Field label={copy.profile.player} id="player-label">
        <Select items={playerItems} value={playerId ?? NONE} onValueChange={(value) => setPlayer(!value || value === NONE ? null : String(value))}>
          <SelectTrigger aria-labelledby="player-label" className={trigger} disabled={!meta}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="max-h-80">
            <SelectItem value={NONE}>{copy.profile.noPlayer}</SelectItem>
            {teams.map((team) => (
              <SelectGroup key={team.team_id}>
                <SelectLabel>{team.name}</SelectLabel>
                {[...team.starting_xi, ...team.bench].map((id) => (
                  <SelectItem key={id} value={id}>
                    {playerItems[id]}
                  </SelectItem>
                ))}
              </SelectGroup>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field label={copy.profile.metric} id="metric-label">
        <Select items={metricItems} value={metric ?? NONE} onValueChange={(value) => setMetric(!value || value === NONE ? null : (value as Metric))}>
          <SelectTrigger aria-labelledby="metric-label" className={trigger}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value={NONE}>{copy.profile.noMetric}</SelectItem>
              {METRICS.map((m) => (
                <SelectItem key={m} value={m}>
                  {copy.metrics[m]}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </Field>
    </div>
  );
}

/** Club picker, shown as a compact chip. */
export function ClubChip() {
  const meta = useLive((s) => s.meta);
  const clubId = useProfile((s) => s.clubId);
  const setClub = useProfile((s) => s.setClub);
  const items: Record<string, string> = { [NONE]: copy.profile.noClub };
  if (meta) {
    items[meta.home.team_id] = meta.home.name;
    items[meta.away.team_id] = meta.away.name;
  }
  return (
    <Select items={items} value={clubId ?? NONE} onValueChange={(value) => setClub(!value || value === NONE ? null : String(value))}>
      <SelectTrigger aria-label={copy.profile.club} className="h-9 w-auto min-w-0 gap-2 rounded-full px-3 text-sm" disabled={!meta}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {Object.entries(items).map(([value, label]) => (
          <SelectItem key={value} value={value}>
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Style (Casual or Analyst) and language. */
export function StyleAndLanguage() {
  const { mode, language, setMode, setLanguage } = useProfile();
  return (
    <>
      <Field label={copy.profile.style} id="mode-label">
        <ToggleGroup
          aria-labelledby="mode-label"
          value={[mode]}
          onValueChange={(value: string[]) => value[0] && setMode(value[0] as Mode)}
          spacing={0}
          variant="outline"
          className="w-full"
        >
          {MODES.filter((m) => m !== "player_focus").map((m) => (
            <ToggleGroupItem key={m} value={m} title={copy.modeHelp[m]} className="h-10 flex-1 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground">
              {copy.modes[m]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </Field>
      <Field label={copy.profile.language} id="language-label">
        <Select items={copy.languages} value={language} onValueChange={(value) => value && setLanguage(value as Language)}>
          <SelectTrigger aria-labelledby="language-label" className={trigger}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {LANGUAGES.map((l) => (
              <SelectItem key={l} value={l} lang={l}>
                {copy.languages[l]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
    </>
  );
}

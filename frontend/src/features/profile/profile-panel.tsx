"use client";

import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useLive } from "@/features/live/live-store";
import { LANGUAGES, type Language, METRICS, type Metric, MODES, type Mode } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { useProfile } from "./profile-store";

const NONE = "none";

function Field({ label, id, children }: { label: string; id: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span id={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </span>
      {children}
    </div>
  );
}

const trigger = "h-11 w-full min-w-0 data-[size=default]:h-11";

/** Fan personalisation: mode, language, club and favourite player. */
export function ProfilePanel({ className }: { className?: string }) {
  const meta = useLive((s) => s.meta);
  const { mode, language, clubId, playerId, metric, setMode, setLanguage, setClub, setPlayer, setMetric } = useProfile();
  const metricItems: Record<string, string> = { [NONE]: copy.profile.noMetric, ...copy.metrics };

  const clubItems: Record<string, string> = { [NONE]: copy.profile.noClub };
  if (meta) {
    clubItems[meta.home.team_id] = meta.home.name;
    clubItems[meta.away.team_id] = meta.away.name;
  }
  const teams = meta ? [meta.home, meta.away] : [];
  const playerItems: Record<string, string> = { [NONE]: copy.profile.noPlayer };
  for (const team of teams) {
    for (const id of [...team.starting_xi, ...team.bench]) {
      const p = meta?.players[id];
      if (p) playerItems[id] = `#${p.shirt} ${p.name}`;
    }
  }

  return (
    <section aria-labelledby="profile-heading" className={cn("flex flex-col gap-4", className)}>
      <h2 id="profile-heading" className="font-display text-lg font-semibold">
        {copy.profile.heading}
      </h2>
      <Field label={copy.profile.mode} id="mode-label">
        <ToggleGroup
          aria-labelledby="mode-label"
          value={[mode]}
          onValueChange={(value: string[]) => value[0] && setMode(value[0] as Mode)}
          spacing={0}
          variant="outline"
          className="w-full"
        >
          {MODES.map((m) => (
            <ToggleGroupItem
              key={m}
              value={m}
              title={copy.modeHelp[m]}
              className="h-11 flex-1 px-2 aria-pressed:bg-primary aria-pressed:text-primary-foreground"
            >
              {copy.modes[m]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p className="text-xs text-muted-foreground">{copy.modeHelp[mode]}</p>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label={copy.profile.language} id="language-label">
          <Select
            items={copy.languages}
            value={language}
            onValueChange={(value) => value && setLanguage(value as Language)}
          >
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
        <Field label={copy.profile.club} id="club-label">
          <Select
            items={clubItems}
            value={clubId ?? NONE}
            onValueChange={(value) => setClub(!value || value === NONE ? null : String(value))}
          >
            <SelectTrigger aria-labelledby="club-label" className={trigger} disabled={!meta}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(clubItems).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </div>
      <Field label={copy.profile.player} id="player-label">
        <Select
          items={playerItems}
          value={playerId ?? NONE}
          onValueChange={(value) => setPlayer(!value || value === NONE ? null : String(value))}
        >
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
        <Select
          items={metricItems}
          value={metric ?? NONE}
          onValueChange={(value) => setMetric(!value || value === NONE ? null : (value as Metric))}
        >
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
    </section>
  );
}

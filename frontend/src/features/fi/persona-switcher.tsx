"use client";

import { Languages, UserRound } from "lucide-react";

import { CLUB_COLORS } from "./clubs";
import { LANGUAGE_NAMES, personaText, type Language } from "./i18n";
import { PERSONA_ORDER } from "./personas";
import { useSession, usePrefs } from "./stores";
import type { Persona } from "./types";

export function PersonaSwitcher() {
  const username = useSession((s) => s.username);
  const { persona, language, favoriteTeam, setPersona, setLanguage } = usePrefs();
  if (!username) return null;
  const club = favoriteTeam ? CLUB_COLORS[favoriteTeam] : undefined;
  return (
    <div className="flex items-center gap-2">
      {club && (
        <span title="Your club" className="hidden h-9 items-center rounded-full bg-tally px-3 font-display text-sm font-semibold text-tally-foreground sm:flex">
          {favoriteTeam}
        </span>
      )}
      <label className="flex min-h-11 items-center gap-2 rounded-md border border-[var(--persona)] bg-[color-mix(in_oklch,var(--persona)_12%,var(--card))] px-2 text-sm">
        <UserRound aria-hidden className="size-4 text-[var(--persona)]" />
        <span className="sr-only">Viewing as</span>
        <select value={persona} onChange={(e) => setPersona(e.target.value as Persona)}
          className="h-9 bg-transparent pr-1 font-medium outline-none">
          {PERSONA_ORDER.map((p) => <option key={p} value={p}>{personaText(language, p).label}</option>)}
        </select>
      </label>
      <label className="flex min-h-11 items-center gap-1 rounded-md border bg-card px-2 text-sm">
        <Languages aria-hidden className="size-4 text-muted-foreground" />
        <span className="sr-only">Language</span>
        <select value={language} onChange={(e) => setLanguage(e.target.value as Language)} className="h-9 bg-transparent font-medium outline-none">
          {(Object.keys(LANGUAGE_NAMES) as Language[]).map((l) => <option key={l} value={l}>{LANGUAGE_NAMES[l]}</option>)}
        </select>
      </label>
    </div>
  );
}

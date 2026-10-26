"use client";

import { Check, Star } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { useMatchDetail, useMatches } from "./api";
import type { Persona, PublicPlayer, Team } from "./types";
import { CLUB_COLORS, PERSONA_HUES } from "./clubs";
import { LANGUAGE_NAMES, personaText, translate, type Language } from "./i18n";
import { PlayerFace } from "./player-face";
import { PERSONA_ORDER } from "./personas";
import { usePrefs } from "./stores";

const MAX_PLAYERS = 5;
const STEP_COUNT = 3;


export function Onboarding() {
  const router = useRouter();
  const params = useSearchParams();
  const prefs = usePrefs();
  const [step, setStep] = useState(0);
  const [persona, setPersona] = useState<Persona>(prefs.persona);
  const [language, setLanguage] = useState<Language>(prefs.language);
  const t = translate(language);
  const [club, setClub] = useState<string | null>(prefs.favoriteTeam);
  const [players, setPlayers] = useState<string[]>(prefs.followedPlayers);
  // Clubs from every fixture; squads come from the live demo match (the only one with a roster).
  const matches = useMatches().data ?? [];
  const CLUBS: Team[] = [...new Map(matches.flatMap((m) => [m.home, m.away]).map((tm) => [tm.short, tm])).values()];
  const demo = matches.find((m) => m.status === "live") ?? matches[0];
  const PLAYERS = useMatchDetail(demo?.id ?? "").data?.players ?? [];
  const side = demo && club ? (demo.home.short === club ? "home" : demo.away.short === club ? "away" : null) : null;
  const squad = side ? PLAYERS.filter((p) => p.team === side) : [];
  const others = PLAYERS.filter((p) => !squad.includes(p));
  const togglePlayer = (id: string) =>
    setPlayers((xs) => (xs.includes(id) ? xs.filter((x) => x !== id) : xs.length >= MAX_PLAYERS ? xs : [...xs, id]));

  function finish() {
    prefs.completeOnboarding({ persona, language, favoriteTeam: club, followedPlayers: players });
    const next = params.get("next");
    router.replace(next?.startsWith("/") && next !== "/onboarding" ? next : "/matches");
  }

  return (
    <main lang={language} className="mx-auto flex min-h-dvh max-w-3xl flex-col px-4 py-10 sm:px-6"
      style={club && CLUB_COLORS[club] ? { ["--tally" as string]: CLUB_COLORS[club].deep } : undefined}>
      <p className="font-display text-sm font-semibold tracking-[0.2em] text-tally uppercase">Matchlens</p>
      <ol aria-label="Setup steps" className="mt-4 flex gap-2">
        {t.steps.map((label, i) => (
          <li key={label} aria-current={i === step ? "step" : undefined}
            className={cn("flex-1 border-t-4 pt-2 text-xs font-medium", i <= step ? "border-tally text-foreground" : "border-muted text-muted-foreground")}>
            {i + 1}. {label}
          </li>
        ))}
      </ol>

      {step === 0 && (
        <section aria-labelledby="ob-persona" className="mt-8">
          <fieldset className="mb-6">
            <legend className="text-sm font-semibold">{t.language}</legend>
            <div role="radiogroup" className="mt-2 flex flex-wrap gap-2">
              {(Object.keys(LANGUAGE_NAMES) as Language[]).map((l) => (
                <button key={l} type="button" role="radio" aria-checked={language === l} lang={l} onClick={() => setLanguage(l)}
                  className={cn("min-h-11 rounded-full border px-5 text-sm font-medium", language === l ? "border-foreground bg-foreground text-background" : "hover:bg-muted")}>
                  {LANGUAGE_NAMES[l]}
                </button>
              ))}
            </div>
          </fieldset>
          <h1 id="ob-persona" className="font-display text-display font-semibold">{t.howWatch}</h1>
          <p className="mt-2 text-sm text-muted-foreground">{t.howWatchHelp}</p>
          <div role="radiogroup" className="mt-6 grid gap-3 sm:grid-cols-2">
            {PERSONA_ORDER.map((p) => {
              const pt = personaText(language, p);
              return (
                <button key={p} type="button" role="radio" aria-checked={persona === p} onClick={() => setPersona(p)}
                  style={{ ["--p" as string]: PERSONA_HUES[p] }}
                  className={cn("rounded-xl border border-l-4 border-l-[var(--p)] bg-card p-4 text-left hover:bg-muted/50",
                    persona === p && "ring-2 ring-[var(--p)] bg-[color-mix(in_oklch,var(--p)_12%,var(--card))]")}>
                  <span className="flex items-center justify-between font-semibold">{pt.label}{persona === p && <Check aria-hidden className="size-4 text-[var(--p)]" />}</span>
                  <span className="mt-1 block text-sm text-muted-foreground">{pt.blurb}</span>
                  <span className="mt-3 block text-xs text-muted-foreground">{t.firstOnStats}: {pt.priorities.slice(0, 2).map((x) => x.title).join(" · ")}</span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {step === 1 && (
        <section aria-labelledby="ob-club" className="mt-8">
          <h1 id="ob-club" className="font-display text-display font-semibold">{t.pickClub}</h1>
          <p className="mt-2 text-sm text-muted-foreground">{t.pickClubHelp}</p>
          <div role="radiogroup" className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
            {CLUBS.map((c) => (
              <button key={c.short} type="button" role="radio" aria-checked={club === c.short} onClick={() => setClub(c.short)}
                style={{ ["--c" as string]: CLUB_COLORS[c.short]?.deep, ["--c2" as string]: CLUB_COLORS[c.short]?.bright }}
                className={cn("flex min-h-24 items-center gap-3 rounded-xl border bg-card p-4 text-left hover:bg-muted/50",
                  club === c.short && "ring-2 ring-[var(--c)] bg-[color-mix(in_oklch,var(--c)_18%,var(--card))]")}>
                <span aria-hidden className="grid size-11 shrink-0 place-items-center rounded-full bg-[linear-gradient(135deg,var(--c)_50%,var(--c2)_50%)] font-display text-sm font-bold text-white">{c.short}</span>
                <span><span className="block font-display text-xl font-semibold">{c.short}</span><span className="text-sm text-muted-foreground">{c.name}</span></span>
              </button>
            ))}
            <button type="button" role="radio" aria-checked={club === null} onClick={() => setClub(null)}
              className={cn("min-h-20 rounded-xl border border-dashed p-4 text-left text-sm text-muted-foreground hover:bg-muted/50",
                club === null && "border-foreground text-foreground")}>
              {t.neutral}
            </button>
          </div>
        </section>
      )}

      {step === 2 && (
        <section aria-labelledby="ob-players" className="mt-8">
          <h1 id="ob-players" className="font-display text-display font-semibold">{t.followPlayers}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {t.upTo(MAX_PLAYERS)} {persona === "fantasy" ? t.fantasyHelp : t.followHelp}
            <span className="ml-1 tabular-nums">{t.picked(players.length, MAX_PLAYERS)}</span>
          </p>
          {club && squad.length === 0 && (
            <p className="mt-4 rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground">{t.noSquad(club)}</p>
          )}
          {squad.length > 0 && <PlayerGrid title={t.squad(club!)} list={squad} picked={players} onToggle={togglePlayer} full={players.length >= MAX_PLAYERS} />}
          <PlayerGrid title={squad.length ? t.otherPlayers : t.tonightPlayers} list={others} picked={players} onToggle={togglePlayer} full={players.length >= MAX_PLAYERS} />
        </section>
      )}

      <div className="sticky bottom-0 mt-auto flex items-center justify-between gap-3 border-t bg-background/95 py-4 backdrop-blur">
        <Button type="button" variant="ghost" className="h-11" disabled={step === 0} onClick={() => setStep(step - 1)}>{t.back}</Button>
        {step < STEP_COUNT - 1
          ? <Button type="button" className="h-11 px-6 text-base" onClick={() => setStep(step + 1)}>{t.continue}</Button>
          : <Button type="button" className="h-11 px-6 text-base" onClick={finish}>{players.length ? t.start : t.skip}</Button>}
      </div>
    </main>
  );
}

function PlayerGrid({ title, list, picked, onToggle, full }: {
  title: string; list: PublicPlayer[]; picked: string[]; onToggle: (id: string) => void; full: boolean;
}) {
  return (
    <div className="mt-6">
      <h2 className="text-sm font-semibold">{title}</h2>
      <ul className="mt-2 grid gap-2 sm:grid-cols-2">
        {list.map((p) => {
          const on = picked.includes(p.id);
          return (
            <li key={p.id}>
              <button type="button" aria-pressed={on} disabled={!on && full} onClick={() => onToggle(p.id)}
                className={cn("flex min-h-14 w-full items-center gap-3 rounded-lg border bg-card px-3 text-left text-sm hover:bg-muted/50 disabled:opacity-40",
                  on && "border-foreground")}>
                <PlayerFace id={p.id} shirt={p.shirt} kit={p.team} className="size-10" />
                <span className="flex-1 font-medium">{p.name}</span>
                <span className="text-xs text-muted-foreground">{p.position}</span>
                <Star aria-hidden className={cn("size-4", on ? "fill-current text-graphic-warn" : "text-muted-foreground")} />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

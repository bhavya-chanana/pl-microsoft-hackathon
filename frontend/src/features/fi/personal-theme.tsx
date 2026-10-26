"use client";

import { useEffect } from "react";

import { CLUB_COLORS, PERSONA_HUES } from "./clubs";
import { usePrefs } from "./stores";

/** Paints the app in the viewer's club colours and persona hue, and sets the document language. */
export function PersonalTheme() {
  const favoriteTeam = usePrefs((s) => s.favoriteTeam);
  const persona = usePrefs((s) => s.persona);
  const language = usePrefs((s) => s.language);

  useEffect(() => {
    const root = document.documentElement;
    const club = favoriteTeam ? CLUB_COLORS[favoriteTeam] : undefined;
    if (club) {
      root.style.setProperty("--tally", club.deep);
      root.style.setProperty("--tally-foreground", club.ink);
      root.style.setProperty("--ring", club.deep);
    } else {
      for (const v of ["--tally", "--tally-foreground", "--ring"]) root.style.removeProperty(v);
    }
    root.style.setProperty("--persona", PERSONA_HUES[persona]);
    root.dataset.persona = persona;
    root.lang = language;
  }, [favoriteTeam, persona, language]);

  return null;
}

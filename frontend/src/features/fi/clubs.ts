import type { Persona } from "./types";

/**
 * Club colours. `deep` carries light text (home side, accents); `bright` carries dark text (away side).
 * The favourite club's `deep` becomes the app accent once a viewer picks it in onboarding.
 */
export const CLUB_COLORS: Record<string, { deep: string; bright: string; ink: string }> = {
  NOR: { deep: "oklch(0.52 0.19 258)", bright: "oklch(0.72 0.14 250)", ink: "oklch(0.99 0 0)" },
  HAR: { deep: "oklch(0.58 0.16 55)", bright: "oklch(0.78 0.14 70)", ink: "oklch(0.99 0 0)" },
  WES: { deep: "oklch(0.45 0.15 12)", bright: "oklch(0.72 0.13 15)", ink: "oklch(0.99 0 0)" },
  KIN: { deep: "oklch(0.50 0.14 152)", bright: "oklch(0.78 0.15 150)", ink: "oklch(0.99 0 0)" },
  EAS: { deep: "oklch(0.48 0.18 300)", bright: "oklch(0.74 0.13 305)", ink: "oklch(0.99 0 0)" },
};

/** Each persona has its own hue for its chip and a faint page wash. */
export const PERSONA_HUES: Record<Persona, string> = {
  diehard: "oklch(0.60 0.20 25)",
  casual: "oklch(0.70 0.14 190)",
  tactical: "oklch(0.62 0.16 280)",
  fantasy: "oklch(0.74 0.16 130)",
};

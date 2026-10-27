import type { Language, LocalisedText, MatchMeta, Overlay } from "@/lib/contract";

export type Side = "home" | "away";

export function sideOf(meta: MatchMeta | null, teamId: string | null | undefined): Side | null {
  if (!meta || !teamId) return null;
  if (teamId === meta.home.team_id) return "home";
  if (teamId === meta.away.team_id) return "away";
  return null;
}

export function teamName(meta: MatchMeta | null, teamId: string | null | undefined, short = false): string {
  const side = sideOf(meta, teamId);
  if (!meta || !side) return "";
  return short ? meta[side].short_name : meta[side].name;
}

export function playerName(meta: MatchMeta | null, playerId: string | null | undefined, short = false): string {
  if (!meta || !playerId) return "";
  const p = meta.players[playerId];
  if (!p) return "";
  return short ? p.short_name : p.name;
}

/** Tailwind classes per side, all backed by tokens. */
export const sideTone = {
  home: { bg: "bg-home", text: "text-home-text", border: "border-home", fill: "fill-home", stroke: "stroke-home" },
  away: { bg: "bg-away", text: "text-away-text", border: "border-away", fill: "fill-away", stroke: "stroke-away" },
} as const;

export function overlayText(overlay: Overlay, language: Language): LocalisedText {
  return (
    overlay.localized[language] ??
    overlay.localized.en ?? { headline: overlay.title, body: overlay.subtitle ?? "" }
  );
}

export function formatNumber(value: number, digits = 0): string {
  return value.toLocaleString("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

import type { Source } from "./types";

/** Player-table tabs (FotMob style) and the columns each shows. */
export const PLAYER_CATEGORIES: Record<string, { label: string; cols: Array<{ key: string; label: string; source: Source }> }> = {
  top: { label: "Top stats", cols: [
    { key: "minutes", label: "Min", source: "vest" }, { key: "goals", label: "Goals", source: "fusion" },
    { key: "assists", label: "Assists", source: "fusion" }, { key: "xg", label: "xG", source: "model" },
    { key: "shots", label: "Shots", source: "fusion" }, { key: "distance", label: "Dist (km)", source: "vest" }] },
  attack: { label: "Attack", cols: [
    { key: "shots", label: "Shots", source: "fusion" }, { key: "onTarget", label: "On target", source: "fusion" },
    { key: "xg", label: "xG", source: "model" }, { key: "touches", label: "Touches", source: "fusion" },
    { key: "fastestShot", label: "Fastest shot", source: "ball" }] },
  passes: { label: "Passes", cols: [
    { key: "passes", label: "Passes", source: "fusion" }, { key: "passAccuracy", label: "Acc %", source: "fusion" },
    { key: "progressivePasses", label: "Progressive", source: "fusion" }, { key: "assists", label: "Assists", source: "fusion" }] },
  defence: { label: "Defence", cols: [
    { key: "recoveries", label: "Recoveries", source: "fusion" }, { key: "duelsWon", label: "Duels won", source: "fusion" },
    { key: "yellow", label: "Yellow", source: "llm_vision" }, { key: "red", label: "Red", source: "llm_vision" }] },
  physical: { label: "Physical", cols: [
    { key: "distance", label: "Dist (km)", source: "vest" }, { key: "highSpeedRun", label: "HSR (m)", source: "vest" },
    { key: "sprints", label: "Sprints", source: "vest" }, { key: "topSpeed", label: "Top km/h", source: "vest" },
    { key: "avgHeartRate", label: "Avg HR", source: "vest" }] },
  goalkeeping: { label: "Goalkeeping", cols: [
    { key: "saves", label: "Saves", source: "fusion" }, { key: "minutes", label: "Min", source: "vest" },
    { key: "passes", label: "Passes", source: "fusion" }, { key: "passAccuracy", label: "Acc %", source: "fusion" }] },
};

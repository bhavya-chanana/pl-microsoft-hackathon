import type { EventType, Persona, Source } from "./types";

type PersonaConfig = {
  label: string;
  blurb: string;
  /** Events shown as full-screen popups (critical events always show). */
  popupTypes: EventType[];
  /** Feed ordering weight per event type; higher floats up in "For you". */
  weights: Partial<Record<EventType, number>>;
  /** Stat chips beside the score in Watch. */
  chips: string[];
  /** Stats overview order (team stat keys). */
  statsOrder: string[];
  /** Player page metrics above the fold. */
  playerHeadline: string[];
  /** Ranked: what this viewer wants from the Stats page, most important first. */
  priorities: Array<{ title: string; why: string }>;
  stats: StatsLayout;
};

/** A headline tile: a row of a match-stats section, with persona wording. */
export type KeyStat = { section: string; row: string; caption: string };

export type StatsLayout = {
  /** Four headline numbers at the top of Stats. */
  keyStats: KeyStat[];
  /** Section ids open by default, in order; the rest sit under "More stats". */
  sections: string[];
  more: string[];
  /** Panels in the main column, top to bottom. */
  panels: Array<"myClub" | "mySquad" | "myPlayers" | "shotMap" | "differentials">;
  /** Side column, top to bottom. */
  aside: Array<"bestPlayer" | "zones" | "sources" | "cleanSheet" | "glossary">;
  playerTable: { category: string; sort: string };
};

export const PERSONAS: Record<Persona, PersonaConfig> = {
  diehard: {
    label: "Diehard fan",
    blurb: "Your club first: chances, concessions and why",
    popupTypes: ["big_chance", "save", "yellow", "sub", "var"],
    weights: { goal: 10, var: 9, big_chance: 7, save: 6, yellow: 5, sub: 4, press_win: 3 },
    chips: ["shots", "bigChances", "possession"],
    statsOrder: ["shots", "bigChances", "xg", "possession", "highRegains", "corners", "fouls", "yellow", "distance", "passes", "ppda", "lineHeight", "sprints", "red"],
    playerHeadline: ["goals", "assists", "shots", "duelsWon", "distance"],
    priorities: [
      { title: "Is my club winning the game?", why: "Score context, chances and xG for us vs them" },
      { title: "The danger moments", why: "Shots on target, big chances and saves at both ends" },
      { title: "Fight and effort", why: "Duels, distance and sprints — who wants it more" },
      { title: "My players", why: "How the players I follow are doing right now" },
      { title: "Discipline", why: "Cards and fouls that change the next 20 minutes" },
    ],
    stats: {
      keyStats: [
        { section: "top", row: "Big chances", caption: "Big chances" },
        { section: "top", row: "Shots on target", caption: "On target" },
        { section: "duels", row: "Duels won", caption: "Duels won" },
        { section: "physical", row: "Distance covered", caption: "Ran" },
      ],
      sections: ["top", "shots", "duels", "physical", "discipline"],
      more: ["xg", "passes", "defence"],
      panels: ["myClub", "myPlayers", "shotMap"],
      aside: ["bestPlayer", "zones", "sources"],
      playerTable: { category: "top", sort: "rating" },
    },
  },
  casual: {
    label: "Casual viewer",
    blurb: "Just the big moments, in plain words",
    popupTypes: ["save", "big_chance"],
    weights: { goal: 10, red: 9, var: 8, save: 6, big_chance: 6, sprint: 4, yellow: 3 },
    chips: ["possession", "shots"],
    statsOrder: ["possession", "shots", "bigChances", "corners", "yellow", "red", "distance", "xg", "passes", "fouls", "highRegains", "sprints", "ppda", "lineHeight"],
    playerHeadline: ["goals", "assists", "shots", "topSpeed"],
    priorities: [
      { title: "Who's on top?", why: "Possession and shots, nothing more" },
      { title: "Star of the match", why: "The best player so far, in one card" },
      { title: "Wow numbers", why: "Fastest sprint, hardest shot, furthest run" },
      { title: "Players I know", why: "The names I picked, at a glance" },
      { title: "No jargon", why: "Advanced stats tucked away and explained" },
    ],
    stats: {
      keyStats: [
        { section: "top", row: "Total shots", caption: "Shots" },
        { section: "top", row: "Shots on target", caption: "On target" },
        { section: "physical", row: "Top speed", caption: "Fastest run" },
        { section: "shots", row: "Fastest shot", caption: "Hardest shot" },
      ],
      sections: ["top", "physical"],
      more: ["shots", "discipline", "xg", "passes", "duels", "defence"],
      panels: ["myPlayers", "shotMap"],
      aside: ["bestPlayer", "glossary"],
      playerTable: { category: "top", sort: "rating" },
    },
  },
  tactical: {
    label: "Tactical",
    blurb: "Mechanisms, pressing and shape — with evidence",
    popupTypes: ["press_win", "big_chance", "sub", "offside", "var"],
    weights: { press_win: 9, big_chance: 8, goal: 8, sub: 7, offside: 6, shot: 5, var: 6 },
    chips: ["xg", "ppda", "lineHeight"],
    statsOrder: ["xg", "ppda", "highRegains", "lineHeight", "possession", "passes", "bigChances", "shots", "distance", "sprints", "corners", "fouls", "yellow", "red"],
    playerHeadline: ["progressivePasses", "passAccuracy", "recoveries", "xg", "highSpeedRun"],
    priorities: [
      { title: "Chance quality", why: "xG, xGOT, open play vs set pieces" },
      { title: "Build-up and progression", why: "Pass volume by half, progressive passes, long balls" },
      { title: "Pressing and structure", why: "High regains, tackles, interceptions, where attacks go" },
      { title: "Duels and transitions", why: "Ground and aerial duels, dribbles" },
      { title: "Physical load", why: "Running and sprint load to explain late-game drop-offs" },
    ],
    stats: {
      keyStats: [
        { section: "xg", row: "Expected goals (xG)", caption: "xG" },
        { section: "passes", row: "Progressive passes", caption: "Progressive passes" },
        { section: "defence", row: "High regains", caption: "High regains" },
        { section: "duels", row: "Duels won", caption: "Duels won" },
      ],
      sections: ["xg", "passes", "defence", "duels", "physical"],
      more: ["top", "shots", "discipline"],
      panels: ["shotMap"],
      aside: ["zones", "bestPlayer", "sources"],
      playerTable: { category: "passes", sort: "progressivePasses" },
    },
  },
  fantasy: {
    label: "Fantasy manager",
    blurb: "Your players, their points, as they happen",
    popupTypes: ["yellow", "save", "sub"],
    weights: { goal: 10, red: 9, yellow: 7, save: 6, sub: 6, big_chance: 5 },
    chips: ["xg", "shots"],
    statsOrder: ["xg", "shots", "bigChances", "yellow", "red", "possession", "corners", "passes", "distance", "sprints", "highRegains", "fouls", "ppda", "lineHeight"],
    playerHeadline: ["goals", "assists", "xg", "minutes", "saves"],
    priorities: [
      { title: "My squad's points", why: "Live provisional points for every player I own" },
      { title: "Attacking returns", why: "Goals, assists, xG and shots — who's close to a haul" },
      { title: "Clean sheet watch", why: "Goals against, saves and shots faced for my defence" },
      { title: "Risks", why: "Cards, minutes and subs that cost points" },
      { title: "Differentials", why: "Top performers I don't own yet" },
    ],
    stats: {
      keyStats: [
        { section: "xg", row: "Expected goals (xG)", caption: "xG" },
        { section: "top", row: "Shots on target", caption: "On target" },
        { section: "defence", row: "Keeper saves", caption: "Saves" },
        { section: "discipline", row: "Yellow cards", caption: "Yellows" },
      ],
      sections: ["top", "xg", "defence", "discipline"],
      more: ["shots", "physical", "passes", "duels"],
      panels: ["mySquad", "differentials", "shotMap"],
      aside: ["cleanSheet", "bestPlayer", "sources"],
      playerTable: { category: "attack", sort: "xg" },
    },
  },
};

export const PERSONA_ORDER: Persona[] = ["diehard", "casual", "tactical", "fantasy"];

export const STAT_LABELS: Record<string, string> = {
  possession: "Possession", xg: "Expected goals (xG)", shots: "Shots", bigChances: "Big chances", passes: "Passes",
  highRegains: "High regains", ppda: "PPDA (lower = more pressing)", corners: "Corners", fouls: "Fouls",
  yellow: "Yellow cards", red: "Red cards", distance: "Distance covered", sprints: "Sprints", lineHeight: "Defensive line height",
  minutes: "Minutes", topSpeed: "Top speed", highSpeedRun: "High-speed running", avgHeartRate: "Avg heart rate",
  touches: "Touches", passAccuracy: "Pass accuracy", progressivePasses: "Progressive passes", goals: "Goals",
  assists: "Assists", duelsWon: "Duels won", recoveries: "Recoveries", saves: "Saves",
};

export const SOURCE_LABELS: Record<Source, string> = {
  vest: "Player vest", ball: "Ball sensor", fusion: "Vest + ball", llm_vision: "Video AI", operator: "Match operator", model: "Model",
};

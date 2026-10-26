/** Board ids with this prefix are served from bundled sample data instead of the backend. */
export const FIXTURE_BOARD_PREFIX = "fixture-";

/** Teams and key moments offered in the offline sample (`?fixture=1`). */
export const FIXTURE_TEAMS = [
  { team_id: "riv", name: "Riverside United", short_name: "RIV", side: "home" as const },
  { team_id: "har", name: "Harbour Athletic", short_name: "HAR", side: "away" as const },
];

export const FIXTURE_MOMENTS = [
  { insight_id: "fx-1", minute_label: "34'", headline: "Riverside stuck in front of a compact block", elapsed: 2040, team_id: "riv" },
  { insight_id: "fx-2", minute_label: "58'", headline: "Harbour's press is forcing long balls", elapsed: 3480, team_id: "har" },
  { insight_id: "fx-3", minute_label: "71'", headline: "Right side overloaded, nobody finding the box", elapsed: 4260, team_id: "riv" },
];

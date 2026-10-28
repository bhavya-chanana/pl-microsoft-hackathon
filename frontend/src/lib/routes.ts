/** Every in-app path and server route in one place. */
export const routes = {
  live: "/",
  moments: "/moments",
  stats: "/stats",
  coach: "/coach",
  coachTactics: "/coach?tab=tactics",
  explore: "/explore",
  multiverse: "/explore/multiverse",
  broadcast: "/explore/broadcast",
  lab: "/explore/lab",
  agents: "/agents",
  next10: "/next10",
  next10Verify: "/next10/verify",
  next10Match: (matchId: string) => `/next10/${encodeURIComponent(matchId)}`,
  next10Moments: (matchId: string) => `/next10/${encodeURIComponent(matchId)}/moments`,
} as const;

export const serverRoutes = {
  stream: "/api/stream",
  next10Live: (matchId: string) => `/api/next10/live/${encodeURIComponent(matchId)}`,
  backend: (path: string) => `/api/backend/${path.replace(/^\/+/, "")}`,
} as const;

/** Every in-app path and server route in one place. */
export const routes = {
  matchCentre: "/legacy",
  broadcast: "/broadcast",
  agents: "/agents",
  coach: "/coach",
  login: "/login",
  onboarding: "/onboarding",
  matches: "/matches",
  previous: "/matches?tab=previous",
  watch: (id: string) => `/matches/${id}/watch`,
  stats: (id: string) => `/matches/${id}/stats`,
  player: (id: string, playerId: string) => `/matches/${id}/players/${playerId}`,
} as const;

export const serverRoutes = {
  stream: "/api/stream",
  backend: (path: string) => `/api/backend/${path.replace(/^\/+/, "")}`,
} as const;

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
} as const;

export const serverRoutes = {
  stream: "/api/stream",
  backend: (path: string) => `/api/backend/${path.replace(/^\/+/, "")}`,
} as const;

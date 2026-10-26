/** Every in-app path and server route in one place. */
export const routes = {
  matchCentre: "/",
  broadcast: "/broadcast",
  agents: "/agents",
  coach: "/coach",
  multiverse: "/multiverse",
  lab: "/lab",
  tactics: "/tactics",
} as const;

export const serverRoutes = {
  stream: "/api/stream",
  backend: (path: string) => `/api/backend/${path.replace(/^\/+/, "")}`,
} as const;

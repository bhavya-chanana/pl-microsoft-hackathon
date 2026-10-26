import "server-only";

/** Backend base URL. Server-only so the endpoint never reaches client bundles. */
export function backendUrl(): string {
  return (process.env.MATCHLENS_BACKEND_URL ?? "http://localhost:8765").replace(/\/+$/, "");
}

/** Read-only backend paths the browser may reach through the proxy. */
export const PROXY_GET = [
  /^health$/,
  /^auth\/me$/,
  /^engagement$/,
  /^match$/,
  /^state$/,
  /^timeline$/,
  /^signals$/,
  /^insights(\/[\w:.\-]+)?$/,
  /^overlays$/,
  /^catchup$/,
  /^recap$/,
  /^coach$/,
  /^agents\/decisions$/,
  /^players\/[\w-]+$/,
  /^teams\/[\w-]+$/,
  // Football Intelligence reads (all filtered by playhead on the server).
  /^fi\/(health|auth\/me|matches)$/,
  /^fi\/matches\/[\w-]+(\/(view|stats|players\/[\w-]+))?$/,
];

/** Replay controls (demo only). */
export const PROXY_POST = [/^replay$/, /^replay\/control$/, /^auth\/(google|logout)$/, /^profile$/,
  /^engagement\/(vote|rating)$/, /^fi\/auth\/(login|logout)$/, /^fi\/me\/preferences$/];

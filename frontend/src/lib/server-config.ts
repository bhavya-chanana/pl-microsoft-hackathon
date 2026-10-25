import "server-only";

/** Backend base URL. Server-only so the endpoint never reaches client bundles. */
export function backendUrl(): string {
  return (process.env.MATCHLENS_BACKEND_URL ?? "http://localhost:8765").replace(/\/+$/, "");
}

/** Read-only backend paths the browser may reach through the proxy. */
export const PROXY_GET = [
  /^health$/,
  /^match$/,
  /^state$/,
  /^timeline$/,
  /^signals$/,
  /^insights(\/[\w:.\-]+)?$/,
  /^overlays$/,
  /^catchup$/,
  /^recap$/,
  /^coach$/,
  /^speech$/,
  /^polls$/,
  /^ratings$/,
  /^runs$/,
  /^feeds$/,
  /^agents\/decisions$/,
  /^players\/[\w-]+$/,
  /^teams\/[\w-]+$/,
];

/** Replay controls and the match archive (demo only), fan votes and fan ratings. */
export const PROXY_POST = [
  /^replay$/,
  /^replay\/control$/,
  /^replay\/feed$/,
  /^runs\/[\w-]+\/open$/,
  /^polls\/[\w-]+\/vote$/,
  /^ratings$/,
];

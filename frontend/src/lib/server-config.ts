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
  /^moments\/[\w:.\-]+$/,
  /^pulse$/,
  /^scenarios$/,
  /^scenarios\/check$/,
  /^dataset$/,
  /^tactics\/boards\/[\w-]+$/,
  /^tactics\/boards\/[\w-]+\/rollouts\/[\w-]+$/,
  // Next-10s companion (backend flag MATCHLENS_NEXT10=1). Match ids look like "fotmob:123".
  /^next10\/matches$/,
  /^next10\/matches\/[\w:.\-]+$/,
  /^next10\/matches\/[\w:.\-]+\/(predictions|frames|moments|jobs)$/,
  /^next10\/verify$/,
];

/** Replay controls and the match archive (demo only), fan votes and fan ratings, the pundit, Scenario Lab, the tactics board. */
export const PROXY_POST = [
  /^replay$/,
  /^replay\/control$/,
  /^replay\/restart$/,
  /^replay\/feed$/,
  /^runs\/[\w-]+\/open$/,
  /^polls\/[\w-]+\/vote$/,
  /^ratings$/,
  /^ask$/,
  /^scenarios\/draft$/,
  /^scenarios\/kickoff$/,
  /^tactics\/boards$/,
  /^tactics\/simulate$/,
];

/** Paths whose answer may take a strong-model call or two (ms); everything else gets the default. */
export const PROXY_SLOW: Record<string, number> = { "scenarios/draft": 100_000 };

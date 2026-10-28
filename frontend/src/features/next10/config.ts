/**
 * Dev-only mock switch: `NEXT_PUBLIC_NEXT10_MOCK=1 npm run dev` serves fictional matches, frames and predictions in
 * the backend's shapes, so the Next-10s pages render with no backend. Compiled out of production builds.
 */
export const MOCK = process.env.NODE_ENV !== "production" && process.env.NEXT_PUBLIC_NEXT10_MOCK === "1";

export const LIVE_POLL_MS = 5_000;
export const PITCH_X = 52.5;
export const PITCH_Y = 34;

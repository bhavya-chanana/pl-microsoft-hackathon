"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { serverRoutes } from "@/lib/routes";

import { cadence } from "./polling";
import { usePlayback, usePrefs, useSession } from "./stores";
import { snapshotThrough } from "./time";
import type { MatchDetail, MatchListItem, MatchStats, MatchView, Period, PlayerDetail, Preferences, PublicPlayer } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Calls the backend's /api/fi routes through the same-origin proxy (the session cookie rides along). */
export async function fi<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(serverRoutes.backend(`fi/${path}`), {
    cache: "no-store",
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const detail = await res.json().then((b: { detail?: unknown }) => b.detail).catch(() => undefined);
    if (res.status === 401 && !path.startsWith("auth/login")) useSession.getState().signOut();
    throw new ApiError(res.status, typeof detail === "string" ? detail : `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

// --- Accounts -------------------------------------------------------------------------------------------------------

type AuthResponse = { user: { id: number; username: string }; preferences: Preferences; created?: boolean };

export const signInRequest = (username: string, password: string) =>
  fi<AuthResponse>("auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
export const meRequest = () => fi<AuthResponse>("auth/me");
export const signOutRequest = () => fi<{ ok: boolean }>("auth/logout", { method: "POST" });
export const savePreferences = (prefs: Preferences) =>
  fi<Preferences>("me/preferences", { method: "POST", body: JSON.stringify(prefs) });

// --- Match data -----------------------------------------------------------------------------------------------------

export function useMatches() {
  const spoilers = usePrefs((s) => s.spoilersVisible);
  return useQuery({
    queryKey: ["fi", "matches", spoilers],
    queryFn: () => fi<MatchListItem[]>(`matches?spoilers=${spoilers}`),
    refetchInterval: cadence.boardRefreshMs,
  });
}

export function useMatchDetail(matchId: string) {
  return useQuery({
    queryKey: ["fi", "match", matchId], queryFn: () => fi<MatchDetail>(`matches/${matchId}`), enabled: !!matchId,
    // Server-clock live matches report their live edge here; everything else is static.
    staleTime: Infinity,
    refetchInterval: (q) => (q.state.data?.serverClock ? 2_000 : false),
  });
}

export function usePlayersById(matchId: string): Map<string, PublicPlayer> {
  const { data } = useMatchDetail(matchId);
  return new Map((data?.players ?? []).map((p) => [p.id, p]));
}

/** The playhead a request may use, coarsened so fast-forwarding does not flood the backend. */
function usePlayheadRequest() {
  const { matchId, playheadMs, mode, speed } = usePlayback();
  const step = 1000 * Math.max(1, speed);
  return { matchId, mode, playheadMs, requested: Math.floor(playheadMs / step) * step };
}

/** Watch data: events, score, pulse and chips as of the playhead. Never shows a response from later in the match. */
export function useMatchView() {
  const { matchId, mode, playheadMs, requested } = usePlayheadRequest();
  const query = useQuery({
    queryKey: ["fi", "view", matchId, mode, requested],
    queryFn: () => fi<MatchView>(`matches/${matchId}/view?playhead_ms=${requested}&mode=${mode}`),
    enabled: !!matchId,
    placeholderData: keepPreviousData,
    refetchInterval: mode === "live" ? 2_000 : false,
  });
  // After a seek backwards, the previous (later) response must not stay on screen.
  const safe = query.data && (mode === "full_review" || query.data.playheadMs <= playheadMs) ? query.data : undefined;
  return { ...query, data: safe };
}

/** Stats lag the video on purpose: they move one 30 s snapshot at a time and never pass the playhead. */
export function useMatchStats(period: Period) {
  const { matchId, mode, playheadMs } = usePlayback();
  const timing = useMatchDetail(matchId ?? "").data?.snapshot;
  const through = snapshotThrough(playheadMs, timing);
  const lag = timing?.lagMs ?? cadence.snapshotLagMs;
  const query = useQuery({
    queryKey: ["fi", "stats", matchId, mode, period, through],
    queryFn: () => fi<MatchStats>(`matches/${matchId}/stats?playhead_ms=${through + lag}&mode=${mode}&period=${period}`),
    enabled: !!matchId,
    placeholderData: keepPreviousData,
    refetchInterval: cadence.statsRefreshMs,
  });
  const safe = query.data && (mode === "full_review" || query.data.throughMs <= through) ? query.data : undefined;
  return { ...query, data: safe };
}

export function usePlayerDetail(playerId: string) {
  const { matchId, mode, playheadMs } = usePlayback();
  const timing = useMatchDetail(matchId ?? "").data?.snapshot;
  const through = snapshotThrough(playheadMs, timing);
  const lag = timing?.lagMs ?? cadence.snapshotLagMs;
  const query = useQuery({
    queryKey: ["fi", "player", matchId, playerId, mode, through],
    queryFn: () => fi<PlayerDetail>(`matches/${matchId}/players/${playerId}?playhead_ms=${through + lag}&mode=${mode}`),
    enabled: !!matchId && !!playerId,
    placeholderData: keepPreviousData,
    refetchInterval: cadence.statsRefreshMs,
  });
  const fresh = query.data && (mode === "full_review" || query.data.throughMs <= through);
  return { ...query, data: fresh && query.data?.player.id === playerId ? query.data : undefined };
}

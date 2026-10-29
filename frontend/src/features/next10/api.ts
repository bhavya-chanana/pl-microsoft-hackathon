"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { getJson } from "@/lib/api";

import { MOCK } from "./config";
import { FotmobDoc } from "./fotmob-contract";
import { type LiveStats, LiveStats as LiveStatsSchema, type Moment, Moment as MomentSchema, MatchDetail, Next10Match, Prediction, ScoreDoc, TrackFrame } from "./contract";
import { mockFrames, mockJobs, mockMatch, mockMatches, mockMoments, mockPredictions, mockVerify } from "./mock";

/** Backend paths, ids URL-encoded ("fotmob:123" carries a colon). */
const path = {
  matches: "next10/matches",
  match: (id: string) => `next10/matches/${encodeURIComponent(id)}`,
  predictions: (id: string) => `next10/matches/${encodeURIComponent(id)}/predictions`,
  frames: (id: string) => `next10/matches/${encodeURIComponent(id)}/frames`,
  moments: (id: string) => `next10/matches/${encodeURIComponent(id)}/moments`,
  fotmob: (id: string) => `next10/matches/${encodeURIComponent(id)}/fotmob`,
  jobs: (id: string) => `next10/matches/${encodeURIComponent(id)}/jobs`,
  verify: "next10/verify",
};

/** Parse array endpoints item by item, so one malformed document does not hide the rest. */
function lenient<S extends z.ZodType>(schema: S) {
  return z.array(z.unknown()).transform((items) => items.flatMap((item) => {
    const parsed = schema.safeParse(item);
    return parsed.success ? [parsed.data as z.infer<S>] : [];
  }));
}

const wait = <T,>(value: T) => new Promise<T>((resolve) => setTimeout(() => resolve(value), 120));

export async function fetchMatches(): Promise<Next10Match[]> {
  if (MOCK) return wait(mockMatches());
  return getJson(path.matches, lenient(Next10Match));
}

export async function fetchMatch(id: string): Promise<{ match: Next10Match; score: ScoreDoc | null }> {
  if (MOCK) {
    const found = mockMatch(id);
    if (!found) throw new Error("Match not found");
    return wait(found);
  }
  const detail = await getJson(path.match(id), MatchDetail);
  const score = ScoreDoc.safeParse(detail.score);
  return { match: detail.match, score: score.success ? score.data : null };
}

/** `last` asks for only the newest N documents (the live follow); backends that ignore it answer the window. */
export type WindowQuery = { from_s?: number; to_s?: number; last?: number };

export async function fetchPredictions(id: string, query: WindowQuery & { kind?: string; team?: string }): Promise<Prediction[]> {
  if (MOCK) return wait(mockPredictions(id, query.kind ?? null, query.team ?? null, query.from_s ?? null, query.to_s ?? null, query.last ?? null));
  return getJson(path.predictions(id), lenient(Prediction), query);
}

export async function fetchFrames(id: string, query: WindowQuery): Promise<TrackFrame[]> {
  if (MOCK) return wait(mockFrames(query.from_s ?? 0, query.to_s ?? 0, query.last ?? null));
  const frames = await getJson(path.frames(id), lenient(TrackFrame), query);
  return frames.sort((a, b) => a.t_s - b.t_s);
}

export async function fetchMoments(id: string): Promise<Moment[]> {
  if (MOCK) return wait(mockMoments(id));
  return getJson(path.moments(id), lenient(MomentSchema));
}

/** The newest pipeline statistics for a match: from the video job's `stats`, or a stats document itself. */
export async function fetchStats(id: string): Promise<LiveStats | null> {
  const docs = MOCK ? await wait(mockJobs(id)) : await getJson(path.jobs(id), z.array(z.unknown()));
  let found: LiveStats | null = null;
  for (const doc of docs) {
    const rec = doc as { stats?: unknown } | null;
    const parsed = LiveStatsSchema.safeParse(rec?.stats ?? doc);
    if (parsed.success && (parsed.data.live_ready != null || parsed.data.calibrated_share != null)) found = parsed.data;
  }
  return found;
}

export async function fetchVerify(): Promise<ScoreDoc[]> {
  if (MOCK) return wait(mockVerify());
  return getJson(path.verify, lenient(ScoreDoc));
}

export const useMatches = () => useQuery({ queryKey: ["next10", "matches"], queryFn: fetchMatches, refetchInterval: 30_000 });

export const useMatch = (id: string) => useQuery({ queryKey: ["next10", "match", id], queryFn: () => fetchMatch(id), refetchInterval: 20_000 });

export const useStats = (id: string, live: boolean) =>
  useQuery({ queryKey: ["next10", "stats", id], queryFn: () => fetchStats(id), refetchInterval: live ? 10_000 : false, retry: false });

export const useVerify = () => useQuery({ queryKey: ["next10", "verify"], queryFn: fetchVerify, refetchInterval: 60_000 });

export const useMoments = (id: string) => useQuery({ queryKey: ["next10", "moments", id], queryFn: () => fetchMoments(id) });

export function useShadowPredictions(id: string, live: boolean) {
  return useQuery({
    queryKey: ["next10", "shadow", id],
    queryFn: () => fetchPredictions(id, { kind: "shadow_event" }),
    refetchInterval: live ? 15_000 : false,
  });
}

/** Next10 predictions in a window of the match clock (seconds). */
export function useNext10Window(id: string, from: number, to: number, poll: number | false = false, enabled = true) {
  return useQuery({
    queryKey: ["next10", "n10", id, from, to],
    queryFn: () => fetchPredictions(id, { kind: "next10", from_s: from, to_s: to }),
    refetchInterval: poll,
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useFramesWindow(id: string, from: number, to: number, poll: number | false = false, enabled = true) {
  return useQuery({
    queryKey: ["next10", "frames", id, from, to],
    queryFn: () => fetchFrames(id, { from_s: from, to_s: to }),
    refetchInterval: poll,
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** The newest frame and the newest Next10 forecast: the live follow when the socket is not delivering. */
export function useLatest(id: string, poll: number | false, enabled: boolean, withPrediction = true) {
  const frame = useQuery({
    queryKey: ["next10", "latest-frame", id],
    queryFn: async () => (await fetchFrames(id, { last: 1 })).at(-1) ?? null,
    refetchInterval: poll,
    enabled,
  });
  const prediction = useQuery({
    queryKey: ["next10", "latest-n10", id],
    queryFn: async () => (await fetchPredictions(id, { kind: "next10", last: 1 })).at(-1) ?? null,
    refetchInterval: poll,
    enabled: enabled && withPrediction,
  });
  return { frame: frame.data ?? null, prediction: prediction.data ?? null, loading: frame.isPending || (withPrediction && prediction.isPending) };
}

/** The FotMob match page (events, stats, shot map, lineups ...). Null when the backend has none: the blocks are then hidden. */
export async function fetchFotmob(id: string): Promise<FotmobDoc | null> {
  if (MOCK) return null;
  try {
    return await getJson(path.fotmob(id), FotmobDoc);
  } catch {
    return null;
  }
}

export const useFotmob = (id: string, live: boolean, enabled: boolean) =>
  useQuery({ queryKey: ["next10", "fotmob", id], queryFn: () => fetchFotmob(id), refetchInterval: live ? 20_000 : false, enabled, retry: false });

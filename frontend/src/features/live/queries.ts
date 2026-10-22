"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { getJson } from "@/lib/api";
import {
  Catchup,
  type CatchupWindow,
  Decision,
  type FanProfile,
  FeedItem,
  Health,
  PlayerStats,
  Recap,
  TeamStats,
} from "@/lib/contract";
import { profileQuery } from "@/features/profile/profile-store";

import { useLive } from "./live-store";

export const queryKeys = {
  feed: (p: FanProfile) => ["feed", p.mode, p.language, p.clubId, p.playerId] as const,
  catchup: (m: CatchupWindow, p: FanProfile) => ["catchup", m, p.mode, p.language, p.clubId, p.playerId] as const,
  recap: (p: FanProfile) => ["recap", p.mode, p.language, p.clubId, p.playerId] as const,
  player: (id: string) => ["player", id] as const,
  team: (id: string) => ["team", id] as const,
  decisions: ["decisions"] as const,
  health: ["health"] as const,
};

/** Refresh live queries when new insights land, at most once a second. */
function useInsightRefresh(prefix: string) {
  const client = useQueryClient();
  const version = useLive((s) => s.insightsVersion);
  const last = useRef(0);
  useEffect(() => {
    const wait = Math.max(0, 1000 - (Date.now() - last.current));
    const timer = setTimeout(() => {
      last.current = Date.now();
      void client.invalidateQueries({ queryKey: [prefix] });
    }, wait);
    return () => clearTimeout(timer);
  }, [client, version, prefix]);
}

export function useFeed(profile: FanProfile) {
  useInsightRefresh("feed");
  return useQuery({
    queryKey: queryKeys.feed(profile),
    queryFn: ({ signal }) => getJson("insights", FeedItem.array(), { ...profileQuery(profile), limit: 60 }, signal),
    placeholderData: keepPreviousData,
  });
}

export function useCatchup(minutes: CatchupWindow, profile: FanProfile, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.catchup(minutes, profile),
    queryFn: ({ signal }) => getJson("catchup", Catchup, { minutes, ...profileQuery(profile) }, signal),
    enabled,
    staleTime: 20_000,
    placeholderData: keepPreviousData,
  });
}

export function useRecap(profile: FanProfile, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.recap(profile),
    queryFn: ({ signal }) => getJson("recap", Recap, profileQuery(profile), signal),
    enabled,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}

export function usePlayerStats(playerId: string | null) {
  return useQuery({
    queryKey: queryKeys.player(playerId ?? ""),
    queryFn: ({ signal }) => getJson(`players/${playerId}`, PlayerStats, undefined, signal),
    enabled: Boolean(playerId),
    refetchInterval: 5_000,
  });
}

export function useTeamStats(teamId: string | null) {
  return useQuery({
    queryKey: queryKeys.team(teamId ?? ""),
    queryFn: ({ signal }) => getJson(`teams/${teamId}`, TeamStats, undefined, signal),
    enabled: Boolean(teamId),
    refetchInterval: 5_000,
  });
}

export function useDecisions() {
  useInsightRefresh("decisions");
  return useQuery({
    queryKey: queryKeys.decisions,
    queryFn: ({ signal }) => getJson("agents/decisions", Decision.array(), { limit: 200 }, signal),
    placeholderData: keepPreviousData,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => getJson("health", Health, undefined, signal),
    refetchInterval: 5_000,
  });
}

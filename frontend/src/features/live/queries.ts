"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { z } from "zod";

import { ApiError, getJson, postJson } from "@/lib/api";
import {
  BoardCreated,
  type BoardRequest,
  Catchup,
  type CatchupWindow,
  CoachBrief,
  Decision,
  type FanProfile,
  FeedItem,
  Health,
  MatchEvent,
  Moment,
  PlayerRating,
  Pulse,
  Scenarios,
  ScriptCheck,
  PlayerStats,
  Recap,
  RolloutView,
  type RolloutVariant,
  RunSummary,
  TacticsBoard,
  TeamStats,
} from "@/lib/contract";
import { profileQuery } from "@/features/profile/profile-store";

import { FIXTURE_BOARD_PREFIX } from "@/features/tactics/constants";

import { useLive } from "./live-store";

export const queryKeys = {
  feed: (p: FanProfile) => ["feed", p.mode, p.language, p.clubId, p.playerId, p.metric] as const,
  catchup: (m: CatchupWindow, p: FanProfile) =>
    ["catchup", m, p.mode, p.language, p.clubId, p.playerId, p.metric] as const,
  recap: (p: FanProfile) => ["recap", p.mode, p.language, p.clubId, p.playerId, p.metric] as const,
  coach: (teamId: string, language: string) => ["coach", teamId, language] as const,
  player: (id: string) => ["player", id] as const,
  playerEvents: (id: string) => ["player-events", id] as const,
  team: (id: string) => ["team", id] as const,
  decisions: ["decisions"] as const,
  health: ["health"] as const,
  ratings: ["ratings"] as const,
  runs: ["runs"] as const,
  feeds: ["feeds"] as const,
  moment: (id: string) => ["moment", id] as const,
  pulse: ["pulse"] as const,
  scenarios: ["scenarios"] as const,
  scriptCheck: ["script-check"] as const,
  tacticsBoard: (id: string) => ["tactics-board", id] as const,
  rollout: (id: string, tacticId: string, variant: RolloutVariant) => ["rollout", id, tacticId, variant] as const,
};

/** Boards with the fixture id prefix are answered from bundled sample data (the `?fixture=1` demo), not the backend. */
const isFixture = (id: string) => id.startsWith(FIXTURE_BOARD_PREFIX);

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

/** Coach brief for one side; refreshed as new moments land (the backend answers once per match minute). */
export function useCoachBrief(teamId: string | null, language: string) {
  useInsightRefresh("coach");
  return useQuery({
    queryKey: queryKeys.coach(teamId ?? "", language),
    queryFn: ({ signal }) => getJson("coach", CoachBrief, { team_id: teamId, language }, signal),
    enabled: Boolean(teamId),
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

/** The player's key moments (goals, shots, cards...), oldest first. */
export function usePlayerEvents(playerId: string | null) {
  const count = useLive((s) => s.timeline.length);
  return useQuery({
    queryKey: [...queryKeys.playerEvents(playerId ?? ""), count],
    queryFn: ({ signal }) => getJson("timeline", MatchEvent.array(), { player_id: playerId }, signal),
    enabled: Boolean(playerId),
    // Keep the list steady while it refreshes, but never show another player's moments.
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[1] === playerId ? previous : undefined),
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

/** Data and fan ratings; refreshed when fans vote and as the match moves on. */
export function useRatings() {
  const version = useLive((s) => s.fanVersion);
  const client = useQueryClient();
  useEffect(() => {
    void client.invalidateQueries({ queryKey: queryKeys.ratings });
  }, [client, version]);
  return useQuery({
    queryKey: queryKeys.ratings,
    queryFn: ({ signal }) => getJson("ratings", PlayerRating.array(), undefined, signal),
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  });
}

/** Stored replays, newest first (the match archive). */
export function useRuns() {
  const runId = useLive((s) => s.runId);
  return useQuery({
    queryKey: [...queryKeys.runs, runId],
    queryFn: ({ signal }) => getJson("runs", RunSummary.array(), { limit: 8 }, signal),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });
}

/** Recorded matches (from video, on this machine) that can be replayed. */
export function useFeeds() {
  return useQuery({
    queryKey: queryKeys.feeds,
    queryFn: ({ signal }) => getJson("feeds", z.string().array(), undefined, signal),
    staleTime: 60_000,
  });
}

/** Frames and telestrator drawings for one moment; a moment never changes once it has happened. */
export function useMoment(insightId: string | null) {
  return useQuery({
    queryKey: queryKeys.moment(insightId ?? ""),
    queryFn: ({ signal }) => getJson(`moments/${encodeURIComponent(insightId ?? "")}`, Moment, undefined, signal),
    enabled: Boolean(insightId),
    staleTime: Infinity,
    retry: false,
  });
}

/** The minute-by-minute Match Pulse; refreshed as new moments land. */
export function usePulse() {
  useInsightRefresh("pulse");
  return useQuery({
    queryKey: queryKeys.pulse,
    queryFn: ({ signal }) => getJson("pulse", Pulse, undefined, signal),
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  });
}

export function useScenarios() {
  return useQuery({
    queryKey: queryKeys.scenarios,
    queryFn: ({ signal }) => getJson("scenarios", Scenarios, undefined, signal),
    staleTime: Infinity,
  });
}

/** How the running scripted match is keeping to its script; null when the default demo is running. */
export function useScriptCheck() {
  const runId = useLive((s) => s.runId);
  useInsightRefresh("script-check");
  return useQuery({
    queryKey: [...queryKeys.scriptCheck, runId],
    queryFn: async ({ signal }) => {
      try {
        return await getJson("scenarios/check", ScriptCheck, undefined, signal);
      } catch (error) {
        if (error instanceof Error && "status" in error && error.status === 404) return null;
        throw error;
      }
    },
    refetchInterval: 5_000,
    placeholderData: keepPreviousData,
  });
}

/** Starts a Tactics Board; the backend answers 202 with the frozen situation and builds the tactics in the background. */
export function useCreateBoard(fixture: boolean) {
  return useMutation({
    mutationFn: async (request: BoardRequest) =>
      fixture
        ? (await import("@/features/tactics/fixtures")).createFixtureBoard(request)
        : postJson("tactics/boards", request, BoardCreated),
  });
}

/** A board, polled every 750 ms until every tactic is scored (or the build failed). */
export function useTacticsBoard(boardId: string | null) {
  return useQuery({
    queryKey: queryKeys.tacticsBoard(boardId ?? ""),
    queryFn: async ({ signal }) =>
      boardId && isFixture(boardId)
        ? (await import("@/features/tactics/fixtures")).fixtureBoard(boardId)
        : getJson(`tactics/boards/${boardId}`, TacticsBoard, undefined, signal),
    enabled: Boolean(boardId),
    refetchInterval: (query) => {
      const { data, error } = query.state;
      if (data?.status === "done" || data?.status === "failed") return false;
      if (error instanceof ApiError && error.status === 404) return false;
      return 750;
    },
    staleTime: 0,
  });
}

/** One simulated branch (5 Hz frames, events, intent drawings). A rollout never changes once made. */
export function useRollout(boardId: string | null, tacticId: string | null, variant: RolloutVariant, enabled = true) {
  return useQuery({
    queryKey: queryKeys.rollout(boardId ?? "", tacticId ?? "", variant),
    queryFn: async ({ signal }) =>
      boardId && isFixture(boardId)
        ? (await import("@/features/tactics/fixtures")).fixtureRollout(tacticId ?? "", variant)
        : getJson(`tactics/boards/${boardId}/rollouts/${tacticId}`, RolloutView, { variant }, signal),
    enabled: Boolean(boardId && tacticId) && enabled,
    staleTime: Infinity,
    retry: false,
  });
}

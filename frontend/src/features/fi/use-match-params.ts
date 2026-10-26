"use client";

import { useParams } from "next/navigation";

export function useMatchParams() {
  const params = useParams<{ matchId: string; playerId?: string }>();
  return { matchId: params.matchId, playerId: params.playerId };
}

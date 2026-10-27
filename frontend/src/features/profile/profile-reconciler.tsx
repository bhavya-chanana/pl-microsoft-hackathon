"use client";

import { useEffect } from "react";

import { useLive } from "@/features/live/live-store";
import { useProfile } from "./profile-store";

/**
 * The saved club and player belong to one match. When the loaded match changes (a restart, another
 * feed, a stored run) forget the ones that are not in it, so no query asks for a team the backend
 * does not have. Renders nothing.
 */
export function ProfileReconciler() {
  const meta = useLive((s) => s.meta);
  const clubId = useProfile((s) => s.clubId);
  const playerId = useProfile((s) => s.playerId);
  const setClub = useProfile((s) => s.setClub);
  const setPlayer = useProfile((s) => s.setPlayer);
  // Also runs when the saved profile loads after the match did.
  useEffect(() => {
    if (!meta) return;
    if (clubId && clubId !== meta.home.team_id && clubId !== meta.away.team_id) setClub(null);
    if (playerId && !(playerId in meta.players)) setPlayer(null);
  }, [meta, clubId, playerId, setClub, setPlayer]);
  return null;
}

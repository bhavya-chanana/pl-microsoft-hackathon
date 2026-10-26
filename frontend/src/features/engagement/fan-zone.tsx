"use client";

import { Star } from "lucide-react";
import { useEffect, useState } from "react";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useLive } from "@/features/live/live-store";
import { useProfile } from "@/features/profile/profile-store";
import { useAuth } from "@/features/auth/auth-store";
import { copy } from "@/lib/copy";

import { useEngagementStorage } from "./use-engagement-storage";

const CHOICE_CLOSE = "close-call";
const StoredEngagement = z.object({
  pollVote: z.string().nullable(),
  ratings: z.record(z.string(), z.number().int().min(1).max(5)),
});
type StoredEngagement = z.infer<typeof StoredEngagement>;
const emptyEngagement: StoredEngagement = { pollVote: null, ratings: {} };

function choicesFor(home: string, away: string, closeCall: string) {
  return [
    { id: "home", label: home },
    { id: "away", label: away },
    { id: CHOICE_CLOSE, label: closeCall },
  ];
}

/** Local matchday interactions; labels are explicit that votes/ratings are not shared. */
export function FanZone() {
  const meta = useLive((s) => s.meta);
  const clubId = useProfile((s) => s.clubId);
  const playerId = useProfile((s) => s.playerId);
  const language = useProfile((s) => s.language);
  const user = useAuth((s) => s.user);
  const text = copy.fanZone[language];
  const [draft, setDraft] = useState<{ matchId: string | null; vote: string | null }>({ matchId: null, vote: null });
  const [editingVote, setEditingVote] = useState(false);
  const [selectedPlayerId, setSelectedPlayerId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState(false);
  const [serverEngagement, setServerEngagement] = useState<{ match_id: string; vote: string | null; ratings: Record<string, number>;
    poll_counts: Record<string, number>; rating_averages: Record<string, { average: number; count: number }> } | null>(null);
  const matchId = meta?.match_id;

  useEffect(() => {
    if (!user || !matchId) return;
    let cancelled = false;
    void fetch(`/api/backend/engagement?match_id=${encodeURIComponent(matchId)}`, { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load your fan activity.");
        const result = await response.json() as { vote: string | null; ratings: Record<string, number>; poll_counts: Record<string, number>;
          rating_averages: Record<string, { average: number; count: number }> };
        if (!cancelled) setServerEngagement({ ...result, match_id: matchId });
      }).catch(() => { if (!cancelled) setSaveError(true); });
    return () => { cancelled = true; };
  }, [user, matchId]);

  const storageKey = meta ? `matchlens.engagement.v1.${meta.match_id}` : null;
  const { raw, write } = useEngagementStorage(storageKey);
  let localEngagement = emptyEngagement;
  if (raw) {
    try {
      const parsed = StoredEngagement.safeParse(JSON.parse(raw));
      if (parsed.success) localEngagement = parsed.data;
    } catch {
      // Ignore malformed browser-local data; the fan can still participate.
    }
  }
  const matchEngagement = serverEngagement?.match_id === matchId ? serverEngagement : null;
  const engagement = user && matchEngagement
    ? { pollVote: matchEngagement.vote, ratings: matchEngagement.ratings }
    : localEngagement;
  const pendingVote = draft.matchId === (meta?.match_id ?? null) ? draft.vote : null;

  const players = meta
    ? [meta.home, meta.away].flatMap((team) =>
        [...team.starting_xi, ...team.bench].map((id) => ({
          id,
          name: meta.players[id]?.name ?? id,
          shirt: meta.players[id]?.shirt,
          team: team.name,
          teamId: team.team_id,
        })),
      )
    : [];
  const selectedPlayer = players.find((player) => player.id === selectedPlayerId)
    ?? players.find((player) => player.id === playerId)
    ?? players[0];
  const playerItems = Object.fromEntries(
    players.map((player) => [player.id, `#${player.shirt ?? "–"} ${player.name}`]),
  );
  const favoriteClub = meta
    ? [meta.home, meta.away].find((team) => team.team_id === clubId)?.name ?? null
    : null;
  const favoritePlayer = meta?.players[playerId ?? ""]?.name ?? null;
  const choices = meta ? choicesFor(meta.home.name, meta.away.name, text.closeCall) : [];

  function save(next: StoredEngagement) {
    setSaveError(!write(JSON.stringify(next)));
  }

  async function castVote() {
    if (!pendingVote) return;
    if (user && meta) {
      try {
        const response = await fetch("/api/backend/engagement/vote", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ match_id: meta.match_id, choice: pendingVote }) });
        if (!response.ok) throw new Error();
        const counts = { ...(serverEngagement?.poll_counts ?? { home: 0, away: 0, "close-call": 0 }) };
        if (engagement.pollVote && engagement.pollVote !== pendingVote) counts[engagement.pollVote] = Math.max(0, (counts[engagement.pollVote] ?? 0) - 1);
        if (engagement.pollVote !== pendingVote) counts[pendingVote] = (counts[pendingVote] ?? 0) + 1;
        setServerEngagement({ match_id: meta.match_id, vote: pendingVote, ratings: { ...(serverEngagement?.ratings ?? {}) }, poll_counts: counts,
          rating_averages: serverEngagement?.rating_averages ?? {} });
        setSaveError(false);
      } catch { setSaveError(true); return; }
      setEditingVote(false);
      return;
    }
    save({ ...engagement, pollVote: pendingVote });
    setEditingVote(false);
  }

  async function rate(score: number) {
    if (!selectedPlayer) return;
    if (user && meta) {
      try {
        const response = await fetch("/api/backend/engagement/rating", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ match_id: meta.match_id, player_id: selectedPlayer.id, score }) });
        if (!response.ok) throw new Error();
        const previous = serverEngagement?.rating_averages[selectedPlayer.id];
        const oldScore = serverEngagement?.ratings[selectedPlayer.id];
        const count = (previous?.count ?? 0) + (oldScore === undefined ? 1 : 0);
        const average = count ? ((previous?.average ?? 0) * (previous?.count ?? 0) - (oldScore ?? 0) + score) / count : score;
        setServerEngagement({ match_id: meta.match_id, vote: serverEngagement?.vote ?? null,
          ratings: { ...(serverEngagement?.ratings ?? {}), [selectedPlayer.id]: score },
          poll_counts: serverEngagement?.poll_counts ?? { home: 0, away: 0, "close-call": 0 },
          rating_averages: { ...(serverEngagement?.rating_averages ?? {}),
            [selectedPlayer.id]: { average: Math.round(average * 10) / 10, count } } });
        setSaveError(false);
      } catch { setSaveError(true); }
      return;
    }
    save({ ...engagement, ratings: { ...engagement.ratings, [selectedPlayer.id]: score } });
  }

  const currentRating = selectedPlayer ? engagement.ratings[selectedPlayer.id] : undefined;

  return (
    <section aria-labelledby="fan-zone-heading" className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h2 id="fan-zone-heading" className="font-display text-lg font-semibold">{text.heading}</h2>
        <p className="text-sm text-muted-foreground">{text.intro(favoriteClub, favoritePlayer)}</p>
      </header>

      <section aria-labelledby="fan-poll-heading" className="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-raised">
        <header className="flex flex-col gap-1">
          <h3 id="fan-poll-heading" className="font-display text-base font-semibold">{text.pollTitle}</h3>
          <p className="text-sm text-muted-foreground">{text.pollQuestion}</p>
        </header>
        {meta ? (
          <>
            {engagement.pollVote && !editingVote ? (
              <div className="flex flex-col gap-3" aria-live="polite">
                <p className="rounded-md bg-raised px-3 py-2 text-sm">
                  <span className="font-medium">{text.voted}: </span>
                  {choices.find((choice) => choice.id === engagement.pollVote)?.label ?? engagement.pollVote}
                </p>
                <Button
                  variant="outline"
                  className="min-h-11"
                  onClick={() => {
                    setDraft({ matchId: meta.match_id, vote: engagement.pollVote });
                    setEditingVote(true);
                  }}
                >
                  {text.changeVote}
                </Button>
              </div>
            ) : (
              <>
                <ToggleGroup
                  aria-label={text.pollQuestion}
                  value={pendingVote ? [pendingVote] : []}
                  onValueChange={(value: string[]) => setDraft({ matchId: meta.match_id, vote: value[0] ?? null })}
                  orientation="vertical"
                  spacing={1}
                  variant="outline"
                  className="w-full"
                >
                  {choices.map((choice) => (
                    <ToggleGroupItem
                      key={choice.id}
                      value={choice.id}
                      className="min-h-11 justify-start px-3 aria-pressed:bg-primary aria-pressed:text-primary-foreground"
                    >
                      {choice.label}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <Button className="min-h-11" onClick={castVote} disabled={!pendingVote}>
                  {text.vote}
                </Button>
              </>
            )}
            {user && matchEngagement && (
              <p className="text-xs text-muted-foreground" aria-live="polite">
                Fan votes · {choices.map((choice) => `${choice.label}: ${matchEngagement.poll_counts[choice.id] ?? 0}`).join(" · ")}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {user ? "Your vote is counted in the match total and saved to your account." : text.pollPrivacy}
            </p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">{text.noMatch}</p>
        )}
      </section>

      <section aria-labelledby="player-rating-heading" className="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-raised">
        <header className="flex flex-col gap-1">
          <h3 id="player-rating-heading" className="font-display text-base font-semibold">{text.ratingTitle}</h3>
          <p className="text-sm text-muted-foreground">{selectedPlayer ? text.ratingPrompt(selectedPlayer.name) : text.noPlayers}</p>
        </header>
        {selectedPlayer && (
          <>
            <Select
              items={playerItems}
              value={selectedPlayer.id}
              onValueChange={(value) => value && setSelectedPlayerId(value)}
            >
              <SelectTrigger aria-label={text.ratingTitle} className="h-11 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="max-h-80">
                {[meta?.home, meta?.away].filter((team) => team !== undefined).map((team) => (
                  <SelectGroup key={team.team_id}>
                    <SelectLabel>{team.name}</SelectLabel>
                    {[...team.starting_xi, ...team.bench].map((id) => {
                      const player = meta?.players[id];
                      return player ? (
                        <SelectItem key={id} value={id}>#{player.shirt} {player.name}</SelectItem>
                      ) : null;
                    })}
                  </SelectGroup>
                ))}
              </SelectContent>
            </Select>
            <div role="group" aria-label={text.ratingPrompt(selectedPlayer.name)} className="flex items-center justify-between gap-1">
              {Array.from({ length: 5 }, (_, index) => {
                const score = index + 1;
                return (
                  <Button
                    key={score}
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-11"
                    aria-label={text.ratingLabel(score)}
                    aria-pressed={currentRating === score}
                    onClick={() => rate(score)}
                  >
                    <Star aria-hidden className={currentRating !== undefined && score <= currentRating ? "fill-current text-graphic-warn" : "text-muted-foreground"} />
                  </Button>
                );
              })}
            </div>
            {currentRating !== undefined && <p className="text-sm font-medium" aria-live="polite">{text.ratingSaved(currentRating)}</p>}
            {user && matchEngagement?.rating_averages[selectedPlayer.id] && (
              <p className="text-xs text-muted-foreground">
                Fan average: {matchEngagement.rating_averages[selectedPlayer.id].average}/5 · {matchEngagement.rating_averages[selectedPlayer.id].count} ratings
              </p>
            )}
          </>
        )}
        <p className="text-xs text-muted-foreground">
          {user ? "Your rating is saved to your account and included in fan averages." : text.localOnly}
        </p>
      </section>
      {saveError && <p role="status" className="text-xs text-destructive">{text.saveError}</p>}
    </section>
  );
}

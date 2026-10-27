"use client";

import { SlidersHorizontal, Volume2, VolumeX } from "lucide-react";
import { AnimatePresence, m } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type { StoreApi } from "zustand";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Toggle } from "@/components/ui/toggle";
import { BroadcastMonitor } from "@/features/broadcast/broadcast-monitor";
import { useOverlayDirector } from "@/features/broadcast/use-overlay-director";
import { Caption } from "@/features/live/live-pitch";
import { FactCheckBadge } from "@/features/companion/fact-check-badge";
import { Narrator } from "@/features/companion/narration";
import { useLive } from "@/features/live/live-store";
import { useCoachBrief, useFeed } from "@/features/live/queries";
import {
  createProfileStore,
  ProfileScopeContext,
  type ProfileStore,
  useFanProfile,
  useProfile,
} from "@/features/profile/profile-store";
import type { FanProfile, Language, MatchMeta } from "@/lib/contract";
import { copy, signalLabels } from "@/lib/copy";
import { playerName, sideOf, sideTone, teamName } from "@/lib/match";
import { cn } from "@/lib/utils";

type Kind = "casual" | "analyst" | "player" | "coach";
const KINDS: Kind[] = ["casual", "analyst", "player", "coach"];
const LANGUAGES: Language[] = ["en", "hi", "es"];
const FLASH_MS = 1_800;

/** The four fans the wall starts with: different people, different languages, one match. */
const START: { kind: Kind; language: Language }[] = [
  { kind: "casual", language: "hi" },
  { kind: "analyst", language: "en" },
  { kind: "player", language: "es" },
  { kind: "coach", language: "en" },
];

/** The away side's number 10 (or their first forward): the player the player-focus fan follows. */
function focusPlayer(meta: MatchMeta): string | null {
  const away = Object.values(meta.players).filter((p) => p.player_id.startsWith(`${meta.away.team_id}-`));
  return (away.find((p) => p.shirt === 10) ?? away.find((p) => p.position === "FWD") ?? away[0])?.player_id ?? null;
}

function profileFor(kind: Kind, language: Language, meta: MatchMeta | null): Partial<FanProfile> {
  switch (kind) {
    case "casual":
      return { mode: "casual", language, clubId: meta?.away.team_id ?? null, playerId: null };
    case "analyst":
      return { mode: "analyst", language, clubId: null, playerId: null };
    case "player":
      return { mode: "player_focus", language, clubId: meta?.away.team_id ?? null, playerId: meta ? focusPlayer(meta) : null };
    case "coach":
      return { mode: "analyst", language, clubId: meta?.home.team_id ?? null, playerId: null };
  }
}

function who(kind: Kind, meta: MatchMeta | null, profile: FanProfile): string {
  if (!meta) return copy.multiverse.personas[kind];
  if (kind === "player") return `${copy.multiverse.personas.player}: ${playerName(meta, profile.playerId, true)}`;
  if (kind === "coach") return `${copy.multiverse.personas.coach}, ${teamName(meta, profile.clubId, true)}`;
  if (kind === "casual") return `${copy.multiverse.personas.casual}, ${teamName(meta, profile.clubId, true)}`;
  return copy.multiverse.personas.analyst;
}

function FeedTop() {
  const profile = useFanProfile();
  const feed = useFeed(profile);
  const meta = useLive((s) => s.meta);
  if (feed.isPending) return <Skeleton className="h-24 w-full" />;
  const items = (feed.data ?? []).filter((i) => i.stage !== "retracted").slice(0, 3);
  if (!items.length) return <p className="text-sm text-muted-foreground">{copy.multiverse.empty}</p>;
  return (
    <ol className="flex flex-col divide-y" lang={profile.language}>
        {items.map((item) => {
          const side = sideOf(meta, item.team_id);
          return (
            <m.li
              key={item.insight_id}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex gap-3 py-2 first:pt-0"
            >
              <span aria-hidden className={cn("mt-1 w-1 shrink-0 self-stretch rounded-full", side ? sideTone[side].bg : "bg-border")} />
              <div className="flex min-w-0 flex-col gap-0.5">
                <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                  <span className="font-display text-sm font-semibold text-foreground tabular-nums">{item.minute_label}</span>
                  <span>{signalLabels[item.signal_type] ?? item.signal_type}</span>
                  <FactCheckBadge status={item.fact_check} />
                </div>
                <p className="line-clamp-2 font-display text-base leading-snug font-semibold">{item.headline}</p>
              </div>
            </m.li>
          );
        })}
    </ol>
  );
}

function CoachTop() {
  const clubId = useProfile((s) => s.clubId);
  const language = useProfile((s) => s.language);
  const brief = useCoachBrief(clubId, language);
  if (brief.isPending) return <Skeleton className="h-24 w-full" />;
  const items = brief.data?.items.slice(0, 3) ?? [];
  if (!items.length) return <p className="text-sm text-muted-foreground">{copy.multiverse.coachEmpty}</p>;
  return (
    <ol className="flex flex-col divide-y" lang={language}>
      {items.map((item) => (
        <li key={item.issue_id} className="flex gap-3 py-2 first:pt-0">
          <span aria-hidden className="font-display text-2xl leading-none font-bold text-muted-foreground tabular-nums">
            {item.rank}
          </span>
          <div className="flex min-w-0 flex-col gap-0.5">
            <p className="font-display text-base leading-snug font-semibold">{item.title}</p>
            <p className="line-clamp-2 text-sm text-muted-foreground">{item.recommendation}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function PaneControls({ index, kind, onKind }: { index: number; kind: Kind; onKind: (k: Kind) => void }) {
  const language = useProfile((s) => s.language);
  const setLanguage = useProfile((s) => s.setLanguage);
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="secondary" className="h-11 gap-2 self-start px-4" />}>
        <SlidersHorizontal aria-hidden />
        {copy.multiverse.customise}
      </PopoverTrigger>
      <PopoverContent className="flex w-72 flex-col gap-3">
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          {copy.multiverse.persona}
          <Select items={copy.multiverse.personas} value={kind} onValueChange={(v) => v && onKind(v as Kind)}>
            <SelectTrigger aria-label={`${copy.multiverse.persona} ${index + 1}`} className="h-11 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {copy.multiverse.personas[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          {copy.profile.language}
          <Select items={copy.languages} value={language} onValueChange={(v) => v && setLanguage(v as Language)}>
            <SelectTrigger aria-label={`${copy.profile.language} ${index + 1}`} className="h-11 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LANGUAGES.map((l) => (
                <SelectItem key={l} value={l} lang={l}>
                  {copy.languages[l]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </PopoverContent>
    </Popover>
  );
}

function PaneTitle({ kind, audio, onAudio }: { kind: Kind; audio: boolean; onAudio: (on: boolean) => void }) {
  const meta = useLive((s) => s.meta);
  const profile = useFanProfile();
  return (
    <div className="flex items-center justify-between gap-2">
      <h2 className="min-w-0 truncate font-display text-lg font-semibold">
        {who(kind, meta, profile)}
        <span className="ml-2 text-sm font-medium text-muted-foreground" lang={profile.language}>
          {copy.languages[profile.language]}
        </span>
      </h2>
      <Toggle
        variant="outline"
        className="size-11 shrink-0"
        pressed={audio}
        onPressedChange={onAudio}
        aria-label={audio ? copy.multiverse.audioOn : copy.multiverse.audio}
        title={audio ? copy.multiverse.audioOn : copy.multiverse.audio}
      >
        {audio ? <Volume2 aria-hidden /> : <VolumeX aria-hidden />}
      </Toggle>
    </div>
  );
}

/** What is on air, as a caption under the pitch (same as the Live page), not over the play. */
function PaneCaption() {
  const onAir = useOverlayDirector();
  return <Caption onAir={onAir} />;
}

function Pane({
  index,
  store,
  kind,
  onKind,
  audio,
  onAudio,
  flash,
}: {
  index: number;
  store: StoreApi<ProfileStore>;
  kind: Kind;
  onKind: (k: Kind) => void;
  audio: boolean;
  onAudio: (on: boolean) => void;
  flash: boolean;
}) {
  return (
    <ProfileScopeContext value={store}>
      <section
        aria-label={`${copy.multiverse.panes} ${index + 1}`}
        className={cn(
          "flex w-[88vw] max-w-xl shrink-0 snap-center flex-col gap-3 rounded-2xl border bg-card p-3 shadow-raised transition-shadow duration-300 sm:w-[70vw] lg:w-auto lg:max-w-none",
          flash && "ring-2 ring-tally",
        )}
      >
        <PaneTitle kind={kind} audio={audio} onAudio={onAudio} />
        <BroadcastMonitor compact autoReplay={index === 0} />
        <PaneCaption />
        <div className="min-h-32">{kind === "coach" ? <CoachTop /> : <FeedTop />}</div>
        <PaneControls index={index} kind={kind} onKind={onKind} />
        <Narrator enabled={audio} />
      </section>
    </ProfileScopeContext>
  );
}

/** Flashes the wall when a top-priority moment lands that the viewer saw happen live. */
function useSyncFlash(): boolean {
  const signals = useLive((s) => s.signals);
  const runId = useLive((s) => s.runId);
  const seen = useRef<{ runId: string | null; count: number } | null>(null);
  const [flashAt, setFlashAt] = useState(0);
  useEffect(() => {
    if (!seen.current || seen.current.runId !== runId) {
      seen.current = { runId, count: signals.length };
      return;
    }
    const fresh = signals.slice(seen.current.count);
    seen.current.count = signals.length;
    if (fresh.some((s) => s.priority === 1)) setFlashAt(Date.now());
  }, [signals, runId]);
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!flashAt) return;
    const id = setTimeout(() => setNow(Date.now()), FLASH_MS);
    return () => clearTimeout(id);
  }, [flashAt]);
  return flashAt > now;
}

export function Multiverse() {
  const meta = useLive((s) => s.meta);
  const [panes, setPanes] = useState(() => START.map((p) => ({ ...p, store: createProfileStore(profileFor(p.kind, p.language, null)) })));
  const [audio, setAudio] = useState<number | null>(null);
  const flash = useSyncFlash();
  const matchId = meta?.match_id;

  // Clubs and players come from the match, so fill them in once it is known (and again for a new match).
  useEffect(() => {
    if (!meta) return;
    for (const p of panes) p.store.setState(profileFor(p.kind, p.store.getState().language, meta));
    // Only when the match changes; persona changes set their own profile.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId]);

  const changeKind = (index: number, kind: Kind) => {
    setPanes((ps) =>
      ps.map((p, i) => {
        if (i !== index) return p;
        p.store.setState(profileFor(kind, p.store.getState().language, meta));
        return { ...p, kind };
      }),
    );
  };

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4 py-4 lg:px-6 lg:py-6">
      <div className="flex flex-wrap items-end justify-between gap-3 px-4 lg:px-0">
        <div className="flex flex-col gap-1">
          <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{copy.multiverse.heading}</h1>
          <p className="max-w-prose text-sm text-muted-foreground">{copy.multiverse.intro}</p>
        </div>
        <AnimatePresence>
          {flash && (
            <m.p
              role="status"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="rounded-full bg-tally px-3 py-1 font-display text-sm font-semibold text-tally-foreground"
            >
              {copy.multiverse.sync}
            </m.p>
          )}
        </AnimatePresence>
      </div>
      <div className="flex snap-x snap-mandatory gap-4 overflow-x-auto px-4 pb-2 lg:grid lg:grid-cols-2 lg:overflow-visible lg:px-0">
        {panes.map((p, i) => (
          <Pane
            key={i}
            index={i}
            store={p.store}
            kind={p.kind}
            onKind={(k) => changeKind(i, k)}
            audio={audio === i}
            onAudio={(on) => setAudio(on ? i : null)}
            flash={flash}
          />
        ))}
      </div>
    </div>
  );
}

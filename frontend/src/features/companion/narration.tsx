"use client";

import { Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { Toggle } from "@/components/ui/toggle";
import { useShell } from "@/components/shell/shell-store";
import { useFeed, useHealth } from "@/features/live/queries";
import { profileQuery, useFanProfile } from "@/features/profile/profile-store";
import type { FanProfile, FeedItem, Language } from "@/lib/contract";
import { copy } from "@/lib/copy";

const LOCALES = { en: "en-GB", hi: "hi-IN", es: "es-ES" } satisfies Record<Language, string>;
/** A data card is read once the agents have had this long to write it up. */
const SETTLE_MS = 6_000;
/** Older clips are dropped rather than read out minutes late. */
const MAX_QUEUE = 3;

function spokenText(item: FeedItem): string {
  const headline = item.headline.replace(/[.!?…\s]+$/u, "");
  return [`${headline}.`, item.body, item.club_angle].filter(Boolean).join(" ");
}

function speechUrl(item: FeedItem, profile: FanProfile): string {
  const params = new URLSearchParams({ insight_id: item.insight_id });
  for (const [key, value] of Object.entries(profileQuery(profile))) {
    if (value && key !== "metric") params.set(key, value);
  }
  return `/api/backend/speech?${params}`;
}

/** Plays one clip at a time from a short queue. */
class NarrationPlayer {
  private profile: FanProfile | null = null;
  private azure = false;
  private queue: FeedItem[] = [];
  private busy = false;
  private audio: HTMLAudioElement | null = null;

  configure(profile: FanProfile, azure: boolean) {
    this.profile = profile;
    this.azure = azure;
  }

  enqueue(item: FeedItem) {
    this.queue.push(item);
    if (this.queue.length > MAX_QUEUE) this.queue.shift();
    this.next();
  }

  stop() {
    this.queue = [];
    this.busy = false;
    this.audio?.pause();
    this.audio = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }

  private next() {
    const profile = this.profile;
    if (this.busy || !profile) return;
    const item = this.queue.shift();
    if (!item) return;
    this.busy = true;
    const finish = () => {
      this.busy = false;
      this.next();
    };
    if (!this.azure) return speakInBrowser(item, profile.language, finish);
    const clip = new Audio(speechUrl(item, profile));
    this.audio = clip;
    const fallback = () => {
      if (this.audio !== clip) return; // stopped, or already falling back
      this.audio = null;
      speakInBrowser(item, profile.language, finish);
    };
    clip.onended = () => {
      if (this.audio !== clip) return;
      this.audio = null;
      finish();
    };
    clip.onerror = fallback;
    clip.play().catch(fallback);
  }
}

function speakInBrowser(item: FeedItem, language: Language, finish: () => void) {
  if (!("speechSynthesis" in window)) return finish();
  const utterance = new SpeechSynthesisUtterance(spokenText(item));
  utterance.lang = LOCALES[language];
  utterance.onend = finish;
  utterance.onerror = finish;
  window.speechSynthesis.speak(utterance);
}

/**
 * Reads new insights aloud while enabled. Uses the backend's Azure AI Speech voice
 * when it has one and falls back to the browser's own voices on any failure.
 */
function useNarration(enabled: boolean) {
  const profile = useFanProfile();
  const feed = useFeed(profile);
  const azure = useHealth().data?.speech === "azure";
  const [player] = useState(() => new NarrationPlayer());
  const firstSeen = useRef(new Map<string, number>());
  const done = useRef(new Set<string>());
  const [tick, setTick] = useState(0);

  useEffect(() => {
    player.configure(profile, azure);
  }, [player, profile, azure]);

  // Re-check settling cards every second.
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setTick((t) => t + 1), 1_000);
    return () => clearInterval(id);
  }, [enabled]);

  // On enable, what is already on screen counts as read; on disable, stop at once.
  useEffect(() => {
    if (!enabled) return;
    for (const item of feed.data ?? []) done.current.add(item.insight_id);
    return () => player.stop();
    // Only the switch itself; later feed changes are handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, player]);

  useEffect(() => {
    if (!enabled || !feed.data) return;
    const now = Date.now();
    // The feed is newest first; read oldest first.
    for (const item of [...feed.data].reverse()) {
      if (done.current.has(item.insight_id) || item.stage === "retracted") continue;
      const seen = firstSeen.current.get(item.insight_id) ?? now;
      firstSeen.current.set(item.insight_id, seen);
      if (item.stage !== "agent" && now - seen < SETTLE_MS) continue;
      done.current.add(item.insight_id);
      player.enqueue(item);
    }
  }, [enabled, feed.data, tick, player]);

  return azure;
}

/** Narrates the feed of whichever profile scope it sits in; renders nothing. */
export function Narrator({ enabled }: { enabled: boolean }) {
  useNarration(enabled);
  return null;
}

const noSubscribe = () => () => {};

/** The Listen switch. The reading itself is done by one Narrator mounted in the app shell. */
export function ListenToggle() {
  const enabled = useShell((s) => s.listen);
  const setEnabled = useShell((s) => s.setListen);
  const supported = useSyncExternalStore(noSubscribe, () => "speechSynthesis" in window || "Audio" in window, () => true);
  const azure = useHealth().data?.speech === "azure";

  const voice = azure ? copy.narration.azureVoice : copy.narration.browserVoice;
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex min-w-0 flex-col">
        <span className="text-sm font-medium">{enabled ? copy.narration.listening : copy.narration.listen}</span>
        <span className="text-xs text-muted-foreground">
          {supported ? (enabled ? voice : copy.narration.help) : copy.narration.unsupported}
        </span>
      </div>
      <Toggle
        variant="outline"
        className="size-11 shrink-0"
        pressed={enabled}
        disabled={!supported}
        onPressedChange={setEnabled}
        aria-label={copy.narration.listen}
      >
        {enabled ? <Volume2 aria-hidden /> : <VolumeX aria-hidden />}
      </Toggle>
    </div>
  );
}

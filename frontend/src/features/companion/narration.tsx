"use client";

import { TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { useShell } from "@/components/shell/shell-store";
import { useFeed, useHealth } from "@/features/live/queries";
import { profileQuery, useFanProfile } from "@/features/profile/profile-store";
import type { FanProfile, FeedItem, Language } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { SoundButton } from "./sound-button";
import { canPlayClips, canSpeak, markClipPlaying, markIdle, problemOf, sharedClip, speakInBrowser, stopBrowserVoice, unlockVoice, useVoice } from "./voice";

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

type Spoken = { key: string; text: string; clipUrl: string | null };

/** Plays one clip at a time from a short queue. */
class NarrationPlayer {
  private language: Language = "en";
  private queue: Spoken[] = [];
  private busy = false;
  private run = 0;
  private clip: HTMLAudioElement | null = null;

  configure(language: Language) {
    this.language = language;
  }

  enqueue(item: Spoken) {
    this.queue.push(item);
    if (this.queue.length > MAX_QUEUE) this.queue.shift();
    this.next();
  }

  stop() {
    this.run++;
    this.queue = [];
    this.busy = false;
    if (this.clip) {
      this.clip.onended = this.clip.onerror = null;
      this.clip.pause();
      this.clip = null;
    }
    stopBrowserVoice();
  }

  private next() {
    if (this.busy) return;
    const item = this.queue.shift();
    if (!item) return;
    this.busy = true;
    const run = this.run;
    const finish = () => {
      if (run !== this.run) return; // stopped meanwhile
      this.busy = false;
      this.next();
    };
    const browser = () => void speakInBrowser(item.text, this.language, finish);
    const clip = item.clipUrl ? sharedClip() : null;
    if (!clip || !item.clipUrl) return browser();
    let fell = false;
    const fallback = () => {
      if (fell || run !== this.run) return;
      fell = true;
      clip.onended = clip.onerror = null;
      this.clip = null;
      browser();
    };
    this.clip = clip;
    clip.onended = () => {
      if (run !== this.run) return;
      this.clip = null;
      markIdle();
      finish();
    };
    clip.onerror = fallback;
    clip.src = item.clipUrl;
    clip.play().then(markClipPlaying, fallback);
  }
}

/**
 * Reads new insights aloud while enabled. On enable it reads the newest one at once (so the
 * switch audibly does something even when the match is quiet), then each new one as it lands.
 * Uses the backend's Azure AI Speech voice when it has one and the browser's own voices on any
 * failure.
 */
function useNarration(enabled: boolean) {
  const profile = useFanProfile();
  const feed = useFeed(profile);
  const azure = useHealth().data?.speech === "azure";
  const [player] = useState(() => new NarrationPlayer());
  const firstSeen = useRef(new Map<string, number>());
  const done = useRef(new Set<string>());
  const primed = useRef(false);
  const attempt = useVoice((s) => s.attempt);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    player.configure(profile.language);
  }, [player, profile.language]);

  // Re-check settling cards every second.
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setTick((t) => t + 1), 1_000);
    return () => clearInterval(id);
  }, [enabled]);

  // On enable, make sure audio is unlocked (the click already did it; this covers a switch set
  // some other way) and start fresh; on disable, stop at once.
  useEffect(() => {
    if (!enabled) return;
    primed.current = false;
    return () => {
      player.stop();
      primed.current = false;
    };
    // A new click on the sound switch starts the narration over.
  }, [enabled, player, attempt]);

  useEffect(() => {
    if (!enabled || !feed.data) return;
    const now = Date.now();
    const toSpoken = (item: FeedItem): Spoken => ({
      key: item.insight_id,
      text: spokenText(item),
      clipUrl: azure ? speechUrl(item, profile) : null,
    });
    if (!primed.current) {
      primed.current = true;
      // What is on screen counts as read, except the newest, which is read now.
      const items = feed.data.filter((i) => i.stage !== "retracted");
      for (const item of feed.data) done.current.add(item.insight_id);
      const newest = items[0];
      player.enqueue(newest ? toSpoken(newest) : { key: "ready", text: copy.narration.ready, clipUrl: null });
      return;
    }
    // The feed is newest first; read oldest first.
    for (const item of [...feed.data].reverse()) {
      if (done.current.has(item.insight_id) || item.stage === "retracted") continue;
      const seen = firstSeen.current.get(item.insight_id) ?? now;
      firstSeen.current.set(item.insight_id, seen);
      if (item.stage !== "agent" && now - seen < SETTLE_MS) continue;
      done.current.add(item.insight_id);
      player.enqueue(toSpoken(item));
    }
  }, [enabled, feed.data, tick, player, azure, profile]);

  return azure;
}

/** Narrates the feed of whichever profile scope it sits in; renders nothing. */
export function Narrator({ enabled }: { enabled: boolean }) {
  useNarration(enabled);
  return null;
}

/** Says why nothing is audible, for as long as that lasts. Renders nothing while sound works. */
export function VoiceNote({ enabled, className, retry }: { enabled: boolean; className?: string; retry?: boolean }) {
  const status = useVoice((s) => s.status);
  const fallback = useVoice((s) => s.languageFallback);
  const problem = problemOf(status);
  const message = problem ? copy.narration.problem[problem] : fallback ? copy.narration.languageFallback : null;
  if (!enabled || !message) return null;
  return (
    <p role="status" className={cn("flex items-start gap-1.5 text-[13px] leading-5 text-balance", problem ? "text-warning" : "text-muted-foreground", className)}>
      {problem && <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />}
      <span>
        {message}
        {problem && retry && (
          <button type="button" onClick={unlockVoice} className="ml-2 inline-flex h-11 items-center text-sm underline underline-offset-2">
            {copy.narration.tryAgain}
          </button>
        )}
      </span>
    </p>
  );
}

/** Whether a problem is being shown, so a hidden control can stay in view. */
export function useVoiceProblem(enabled: boolean): boolean {
  return useVoice((s) => enabled && problemOf(s.status) !== null);
}

const noSubscribe = () => () => {};

/** The Listen switch. The reading itself is done by one Narrator mounted in the app shell. */
export function ListenToggle() {
  const enabled = useShell((s) => s.listen);
  const setEnabled = useShell((s) => s.setListen);
  const supported = useSyncExternalStore(noSubscribe, () => canSpeak() || canPlayClips(), () => true);
  const azure = useHealth().data?.speech === "azure";

  const voice = azure ? copy.narration.azureVoice : copy.narration.browserVoice;
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 flex-col">
        <span className="text-sm font-medium">{enabled ? copy.narration.listening : copy.narration.listen}</span>
        <span className="text-[13px] leading-5 text-muted-foreground">
          {supported ? (enabled ? voice : copy.narration.help) : copy.narration.unsupported}
        </span>
        <VoiceNote enabled={enabled} retry />
      </div>
      <SoundButton enabled={enabled} onChange={setEnabled} name={copy.narration.listen} />
    </div>
  );
}

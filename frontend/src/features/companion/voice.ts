"use client";

import { create } from "zustand";

import type { Language } from "@/lib/contract";

/**
 * Everything that makes sound goes through here: the browser's speech voices and the backend's
 * Azure clips. It unlocks audio from a click, keeps the utterances alive until they finish
 * (Chrome drops ones that nothing references), never leaves a queue stuck behind a clip that
 * will not play, and records why nothing is audible so the UI can say so.
 */

export const LOCALES = { en: "en-GB", hi: "hi-IN", es: "es-ES" } satisfies Record<Language, string>;

export type VoiceProblem = "blocked" | "no-voice" | "failed" | "unsupported";
export type VoiceStatus = "idle" | "starting" | "speaking" | VoiceProblem;

type VoiceStore = {
  status: VoiceStatus;
  /** True when the browser read in another language because it has no voice for the chosen one. */
  languageFallback: boolean;
  /** Counts clicks that (re)start sound, so a narrator knows to begin again. */
  attempt: number;
  set: (status: VoiceStatus, languageFallback?: boolean) => void;
};

export const useVoice = create<VoiceStore>((set) => ({
  status: "idle",
  languageFallback: false,
  attempt: 0,
  set: (status, languageFallback = false) => set({ status, languageFallback }),
}));

const setStatus = (status: VoiceStatus, languageFallback = false) => useVoice.getState().set(status, languageFallback);

export const canSpeak = () => typeof window !== "undefined" && "speechSynthesis" in window;
export const canPlayClips = () => typeof window !== "undefined" && "Audio" in window;

/** One-sample silent WAV, to unlock a media element inside a click. */
const START_WAIT_MS = 6_000;
const SILENCE =
  "data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQIAAACAgA==";

/** The one audio element, unlocked by a click and reused for every clip so later plays are allowed. */
let clipElement: HTMLAudioElement | null = null;
export function sharedClip(): HTMLAudioElement | null {
  if (!canPlayClips()) return null;
  clipElement ??= new Audio();
  return clipElement;
}

/**
 * Call synchronously from the click that turns sound on. Browsers only allow audio that
 * a click started, so this primes both speech and clip playback while the click is still live.
 */
export function unlockVoice() {
  useVoice.setState((s) => ({ attempt: s.attempt + 1 }));
  if (canSpeak()) {
    const synth = window.speechSynthesis;
    synth.resume();
    synth.getVoices(); // starts loading the voice list
    const silent = new SpeechSynthesisUtterance(" ");
    silent.volume = 0;
    synth.speak(silent);
  }
  const clip = sharedClip();
  if (clip) {
    clip.src = SILENCE;
    clip.muted = true;
    void clip
      .play()
      .catch(() => {})
      .finally(() => {
        if (clip.src === SILENCE) clip.pause();
        clip.muted = false;
      });
  }
  if (!canSpeak() && !canPlayClips()) return setStatus("unsupported");
  setStatus("starting");
  // Nothing audible within a few seconds means the browser is refusing it.
  setTimeout(() => {
    if (useVoice.getState().status === "starting") setStatus("blocked");
  }, START_WAIT_MS);
}

/** Utterances must be referenced until they end, or Chrome never fires their events. */
const alive = new Set<SpeechSynthesisUtterance>();

function voicesReady(): Promise<SpeechSynthesisVoice[]> {
  const synth = window.speechSynthesis;
  const now = synth.getVoices();
  if (now.length) return Promise.resolve(now);
  return new Promise((resolve) => {
    const done = () => {
      synth.removeEventListener("voiceschanged", done);
      clearTimeout(timer);
      resolve(synth.getVoices());
    };
    const timer = setTimeout(done, 1_500);
    synth.addEventListener("voiceschanged", done);
  });
}

function pickVoice(voices: SpeechSynthesisVoice[], locale: string): SpeechSynthesisVoice | null {
  const lower = locale.toLowerCase();
  const base = lower.split("-")[0];
  return (
    voices.find((v) => v.lang.toLowerCase().replace("_", "-") === lower) ??
    voices.find((v) => v.lang.toLowerCase().replace("_", "-").split("-")[0] === base) ??
    null
  );
}

/** Rough upper bound for how long a text takes to read, so a stalled voice cannot hold the queue. */
const readingBudgetMs = (text: string) => 6_000 + text.length * 120;
/** An utterance that has not started by then was refused or has no working voice. */
const START_BUDGET_MS = 4_000;

let generation = 0;

/**
 * Read text aloud with the browser's voice. Always calls `finish` exactly once, whether it was
 * read, refused, failed or cancelled, so queues keep moving.
 */
export async function speakInBrowser(text: string, language: Language, finish: () => void, onStart?: () => void) {
  if (!canSpeak()) {
    setStatus("unsupported");
    return finish();
  }
  const synth = window.speechSynthesis;
  const mine = ++generation;
  const voices = await voicesReady();
  if (mine !== generation) return finish();
  if (voices.length === 0) {
    setStatus("no-voice");
    return finish();
  }

  const locale = LOCALES[language];
  const voice = pickVoice(voices, locale);
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = locale;
  alive.add(utterance);

  let settled = false;
  let started = false;
  const end = (problem?: VoiceProblem) => {
    if (settled) return;
    settled = true;
    clearTimeout(startTimer);
    clearTimeout(readTimer);
    alive.delete(utterance);
    if (problem) setStatus(problem);
    else if (mine === generation) setStatus("idle");
    finish();
  };
  utterance.onstart = () => {
    started = true;
    clearTimeout(startTimer);
    setStatus("speaking", !voice);
    onStart?.();
  };
  utterance.onend = () => end();
  utterance.onerror = (event) => {
    if (event.error === "interrupted" || event.error === "canceled") return end();
    end(event.error === "not-allowed" ? "blocked" : event.error === "language-unavailable" ? "no-voice" : "failed");
  };
  const startTimer = setTimeout(() => {
    if (started) return;
    synth.cancel();
    end("blocked");
  }, START_BUDGET_MS);
  const readTimer = setTimeout(() => {
    synth.cancel();
    end();
  }, readingBudgetMs(text));

  // A paused engine (Safari, or a tab that was backgrounded) accepts text and says nothing.
  try {
    if (voice) utterance.voice = voice;
    if (synth.paused) synth.resume();
    synth.speak(utterance);
  } catch {
    end("failed");
  }
}

/** Stops everything the browser is saying. */
export function stopBrowserVoice() {
  generation++;
  if (canSpeak()) window.speechSynthesis.cancel();
  setStatus("idle");
}

/** Is there a problem to tell the person about? */
export function problemOf(status: VoiceStatus): VoiceProblem | null {
  return status === "speaking" || status === "idle" || status === "starting" ? null : status;
}

export function markClipFailed(problem: VoiceProblem) {
  setStatus(problem);
}
export function markClipPlaying() {
  setStatus("speaking");
}
export function markIdle() {
  setStatus("idle");
}

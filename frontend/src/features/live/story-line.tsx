"use client";

import { useLive } from "@/features/live/live-store";
import { useRecap } from "@/features/live/queries";
import { useFanProfile } from "@/features/profile/profile-store";

/** Recap text is only used when it is one clean sentence: never raw event text, never cut off. */
function cleanSentence(text: string | undefined): string | null {
  const first = text?.trim().match(/^.+?[.!](\s|$)/)?.[0].trim();
  if (!first || first.length > 140) return null;
  if (/\d+['’+]|GOAL!|\s-\s|…|\.\.\./.test(first)) return null;
  return first;
}

/** "Story so far", built from the score and clock when the recap is not a clean sentence. */
function fromState(): string | null {
  const { meta, state } = useLive.getState();
  if (!meta || !state) return null;
  const h = state.score[meta.home.team_id] ?? 0;
  const a = state.score[meta.away.team_id] ?? 0;
  const [lead, trail, hi, lo] = h >= a ? [meta.home.name, meta.away.name, h, a] : [meta.away.name, meta.home.name, a, h];
  if (state.status === "full_time") return hi === lo ? `${meta.home.name} and ${meta.away.name} drew ${h}-${a}.` : `${lead} beat ${trail} ${hi}-${lo}.`;
  if (hi === lo) return hi === 0 && state.elapsed < 60 ? null : `${meta.home.name} and ${meta.away.name} are level at ${h}-${a}.`;
  return `${lead} lead ${trail} ${hi}-${lo} after ${state.minute_label}.`;
}

/** The match in one clean line, in the one editorial serif. */
export function StoryLine() {
  const profile = useFanProfile();
  const hasMatch = useLive((s) => s.meta !== null);
  const score = useLive((s) => s.state?.minute_label);
  const recap = useRecap(profile, hasMatch);
  void score;
  const text = cleanSentence(recap.data?.summary) ?? fromState();
  if (!text) return null;
  return (
    <p lang={profile.language} className="truncate font-story text-lg leading-7 text-foreground/90 italic md:text-xl">
      {text}
    </p>
  );
}

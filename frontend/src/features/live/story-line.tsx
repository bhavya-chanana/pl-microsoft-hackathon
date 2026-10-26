"use client";

import { useLive } from "@/features/live/live-store";
import { useRecap } from "@/features/live/queries";
import { useFanProfile } from "@/features/profile/profile-store";

/** "Story so far": the match recap, clamped to two lines, in the one editorial serif. Hidden without recap text. */
export function StoryLine() {
  const profile = useFanProfile();
  const hasMatch = useLive((s) => s.meta !== null);
  const recap = useRecap(profile, hasMatch);
  const text = recap.data?.summary?.trim();
  if (!text) return null;
  return (
    <p lang={profile.language} className="line-clamp-2 max-w-3xl font-story text-xl leading-8 text-foreground/90 italic md:text-[22px] md:leading-8">
      {text}
    </p>
  );
}

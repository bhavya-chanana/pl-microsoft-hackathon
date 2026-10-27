"use client";

import { useProfile } from "@/features/profile/profile-store";

import { useCoach } from "./coach-store";

/** Coach view and Analyst style both use analyst wording (xG, momentum value with its scale). */
export function useAnalystWording(): boolean {
  const coachMode = useCoach((s) => s.coachMode);
  const analyst = useProfile((s) => s.mode === "analyst");
  return coachMode || analyst;
}

import type { Metadata } from "next";
import { Suspense } from "react";

import { RequireSession } from "@/features/fi/auth";
import { Onboarding } from "@/features/fi/onboarding";

export const metadata: Metadata = { title: "Set up Matchlens" };

export default function OnboardingPage() {
  return (
    <RequireSession allowUnonboarded>
      <Suspense>
        <Onboarding />
      </Suspense>
    </RequireSession>
  );
}

import type { Metadata } from "next";
import { Suspense } from "react";

import { RequireSession } from "@/features/fi/auth";
import { MatchBoard } from "@/features/fi/board";

export const metadata: Metadata = { title: "Matches" };

export default function MatchesPage() {
  return (
    <RequireSession>
      <Suspense>
        <MatchBoard />
      </Suspense>
    </RequireSession>
  );
}

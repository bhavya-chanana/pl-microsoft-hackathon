import { type ReactNode, Suspense } from "react";

import { RequireSession } from "@/features/fi/auth";
import { MatchShell } from "@/features/fi/match-shell";

// The mock match runs entirely on the client clock, so there is no static shell to validate.
export const instant = false;

export default function MatchLayout({ children }: { children: ReactNode }) {
  return (
    <RequireSession>
      <Suspense>
        <MatchShell>{children}</MatchShell>
      </Suspense>
    </RequireSession>
  );
}

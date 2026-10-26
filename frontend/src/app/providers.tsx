"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { ThemeProvider } from "next-themes";
import { usePathname } from "next/navigation";
import { type ReactNode, Suspense, useEffect, useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useLiveStream } from "@/features/live/use-live-stream";
import { useProfile } from "@/features/profile/profile-store";
import { LiveStreamContext } from "@/features/live/live-stream-context";
import { PersonalTheme } from "@/features/fi/personal-theme";

const LEGACY_PREFIXES = ["/legacy", "/broadcast", "/coach", "/agents"];

/** Only the legacy Matchlens screens use the backend SSE stream; the mock app does not. */
function LiveStreamGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return LEGACY_PREFIXES.some((p) => pathname.startsWith(p)) ? <LiveStream>{children}</LiveStream> : children;
}

function LiveStream({ children }: { children: ReactNode }) {
  const stream = useLiveStream();
  useEffect(() => {
    void useProfile.persist.rehydrate();
  }, []);
  return <LiveStreamContext value={stream}>{children}</LiveStreamContext>;
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 2_000 } },
      }),
  );
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
      <QueryClientProvider client={client}>
        <MotionConfig reducedMotion="user">
          <TooltipProvider delay={300}>
            <Suspense>
              <LiveStreamGate>{children}</LiveStreamGate>
            </Suspense>
            <PersonalTheme />
            <Toaster position="bottom-center" />
          </TooltipProvider>
        </MotionConfig>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

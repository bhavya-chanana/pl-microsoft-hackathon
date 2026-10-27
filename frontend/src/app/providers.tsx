"use client";

import { useCoach } from "@/features/coach/coach-store";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { ThemeProvider } from "next-themes";
import { type ReactNode, useEffect, useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useLiveStream } from "@/features/live/use-live-stream";
import { useFans } from "@/features/fans/fan-store";
import { profileStore } from "@/features/profile/profile-store";
import { LiveStreamContext } from "@/features/live/live-stream-context";

function LiveStream({ children }: { children: ReactNode }) {
  const stream = useLiveStream();
  useEffect(() => {
    void profileStore.persist.rehydrate();
    void useFans.persist.rehydrate();
    void useCoach.persist.rehydrate();
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
            <LiveStream>{children}</LiveStream>
            <Toaster position="bottom-center" />
          </TooltipProvider>
        </MotionConfig>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

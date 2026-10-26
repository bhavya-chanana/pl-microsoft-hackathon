import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "@/features/fi/auth";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <main className="grid min-h-dvh place-items-center px-4 py-12">
      <div className="w-full max-w-sm">
        <p className="font-display text-sm font-semibold tracking-[0.2em] text-tally uppercase">Matchlens</p>
        <h1 className="mt-2 font-display text-display font-semibold">Every match, read for you</h1>
        <p className="mt-2 text-sm text-muted-foreground">Live video with sensor-backed stats and commentary tuned to how you watch.</p>
        <div className="mt-8 rounded-xl border bg-card p-6 shadow-floating">
          <Suspense>
            <LoginForm />
          </Suspense>
        </div>
      </div>
    </main>
  );
}

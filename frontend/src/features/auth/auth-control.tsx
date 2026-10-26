"use client";

import Script from "next/script";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth, type FanAccount } from "@/features/auth/auth-store";
import { useFanProfile, useProfile } from "@/features/profile/profile-store";
import type { Metric } from "@/lib/contract";

type GoogleCredential = { credential: string };
declare global {
  interface Window {
    google?: { accounts: { id: {
      initialize: (options: { client_id: string; callback: (response: GoogleCredential) => void }) => void;
      renderButton: (element: HTMLElement, options: { theme: string; size: string; shape: string; text: string }) => void;
      cancel: () => void;
    } } };
  }
}

type FanPreferences = { mode: "casual" | "analyst" | "player_focus"; language: "en" | "hi" | "es";
  club_id: string | null; player_id: string | null; metric: Metric | null };
const CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;

/** Google Identity Services sign-in and synchronization of the returning fan profile. */
export function AuthControl() {
  const button = useRef<HTMLDivElement>(null);
  const initialized = useRef(false);
  const [googleReady, setGoogleReady] = useState(false);
  const [message, setMessage] = useState("");
  const user = useAuth((state) => state.user);
  const setUser = useAuth((state) => state.setUser);
  const fanProfile = useFanProfile();
  const profile = useMemo<FanPreferences>(() => ({ mode: fanProfile.mode, language: fanProfile.language,
    club_id: fanProfile.clubId, player_id: fanProfile.playerId, metric: fanProfile.metric }), [fanProfile]);
  const setProfile = useProfile.setState;

  const acceptLogin = useCallback(async (payload: { user: FanAccount; profile: FanPreferences }) => {
    setProfile({ mode: payload.profile.mode, language: payload.profile.language,
      clubId: payload.profile.club_id, playerId: payload.profile.player_id, metric: payload.profile.metric });
    setUser(payload.user);
  }, [setProfile, setUser]);

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/backend/auth/me", { cache: "no-store" }).then(async (response) => {
      if (!response.ok) return;
      const data = await response.json() as { user: FanAccount; profile: FanPreferences };
      if (!cancelled) await acceptLogin(data);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [acceptLogin]);

  useEffect(() => {
    if (!googleReady || !CLIENT_ID || !button.current || !window.google || initialized.current) return;
    initialized.current = true;
    window.google.accounts.id.initialize({
      client_id: CLIENT_ID,
      callback: (result) => {
        setMessage("Signing in…");
        void fetch("/api/backend/auth/google", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ credential: result.credential }) })
          .then(async (response) => {
            if (!response.ok) throw new Error("Google sign-in could not be completed.");
            await acceptLogin(await response.json() as { user: FanAccount; profile: FanPreferences });
            setMessage("");
          }).catch((error: unknown) => setMessage(error instanceof Error ? error.message : "Sign-in failed."));
      },
    });
    window.google.accounts.id.renderButton(button.current, { theme: "outline", size: "medium", shape: "pill", text: "signin_with" });
  }, [acceptLogin, googleReady]);

  useEffect(() => {
    if (!user) return;
    const timer = window.setTimeout(() => {
      void fetch("/api/backend/profile", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(profile) });
    }, 500);
    return () => window.clearTimeout(timer);
  }, [profile, user]);

  async function signOut() {
    await fetch("/api/backend/auth/logout", { method: "POST" }).catch(() => undefined);
    setUser(null);
    setMessage("Signed out");
  }

  return (
    <div className="flex min-w-0 items-center gap-2">
      {CLIENT_ID && <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" onLoad={() => setGoogleReady(true)} />}
      {user ? (
        <>
          <span className="hidden max-w-28 truncate text-xs text-muted-foreground lg:inline" title={user.email}>{user.name}</span>
          <button type="button" onClick={() => void signOut()} className="min-h-11 rounded-md px-2 text-xs font-medium text-muted-foreground hover:bg-muted" aria-label="Sign out">
            Sign out
          </button>
        </>
      ) : CLIENT_ID ? (
        <div ref={button} className="min-h-10" aria-label="Sign in with Google" />
      ) : (
        <span className="hidden text-xs text-muted-foreground xl:inline">Sign-in setup needed</span>
      )}
      {message && <span role="status" className="sr-only">{message}</span>}
    </div>
  );
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, type ReactNode, useEffect, useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";

import { ApiError, meRequest, savePreferences, signInRequest } from "./api";
import type { Language } from "./i18n";
import { usePrefs, useSession } from "./stores";
import type { Preferences } from "./types";

const subscribeHydration = (cb: () => void) => useSession.persist.onFinishHydration(cb);
export const useSessionHydrated = () =>
  useSyncExternalStore(subscribeHydration, () => useSession.persist.hasHydrated(), () => false);
const subscribePrefsHydration = (cb: () => void) => usePrefs.persist.onFinishHydration(cb);
export const usePrefsHydrated = () =>
  useSyncExternalStore(subscribePrefsHydration, () => usePrefs.persist.hasHydrated(), () => false);

/** Server preferences win on sign-in; after that local changes are saved back. */
function applyServerPreferences(prefs: Preferences) {
  usePrefs.setState({ ...prefs, language: prefs.language as Language });
}

function currentPreferences(): Preferences {
  const { persona, favoriteTeam, followedPlayers, spoilersVisible, language, onboarded } = usePrefs.getState();
  return { persona, favoriteTeam, followedPlayers, spoilersVisible, language, onboarded };
}

/** Saves preference changes to the account, debounced. */
function usePreferenceSync(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let timer: number | undefined;
    const save = () => { timer = undefined; void savePreferences(currentPreferences()).catch(() => undefined); };
    const unsubscribe = usePrefs.subscribe(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(save, 600);
    });
    // Navigating away (e.g. finishing onboarding) must not drop a pending save.
    return () => { unsubscribe(); if (timer !== undefined) { window.clearTimeout(timer); save(); } };
  }, [enabled]);
}

/** Sends signed-out visitors to the login page, and first-time viewers through onboarding. */
export function RequireSession({ children, allowUnonboarded = false }: { children: ReactNode; allowUnonboarded?: boolean }) {
  const sessionReady = useSessionHydrated();
  const prefsReady = usePrefsHydrated();
  const hydrated = sessionReady && prefsReady;
  const username = useSession((s) => s.username);
  const onboarded = usePrefs((s) => s.onboarded);
  const router = useRouter();
  // The cookie is the real session; the stored username is only a hint. Check it once per page load.
  const me = useQuery({ queryKey: ["fi", "me", username], queryFn: meRequest, enabled: hydrated && !!username, staleTime: Infinity, retry: false });
  const verified = me.isSuccess;
  usePreferenceSync(verified);
  const needsOnboarding = !allowUnonboarded && !onboarded;
  useEffect(() => {
    if (!hydrated) return;
    const next = encodeURIComponent(window.location.pathname);
    if (!username) router.replace(`/login?next=${next}`);
    else if (verified && needsOnboarding) router.replace(`/onboarding?next=${next}`);
  }, [hydrated, username, verified, needsOnboarding, router]);
  if (me.isError && !(me.error instanceof ApiError && me.error.status === 401)) {
    return <p role="alert" className="mx-auto max-w-md px-4 py-16 text-center text-sm text-muted-foreground">The match server is not reachable. It retries when you reload.</p>;
  }
  if (!hydrated || !username || !verified || needsOnboarding) return <div className="min-h-[60dvh]" aria-busy="true" />;
  return children;
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const signIn = useSession((s) => s.signIn);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!/^[A-Za-z0-9_.-]{3,40}$/.test(username.trim())) return setError("Username: 3–40 letters, numbers, dots, dashes or underscores.");
    if (password.length < 4) return setError("Password needs at least 4 characters.");
    setBusy(true);
    setError("");
    try {
      const res = await signInRequest(username.trim(), password);
      applyServerPreferences(res.preferences);
      signIn(res.user.username);
      const next = params.get("next");
      const target = next?.startsWith("/") ? next : "/matches";
      router.replace(res.preferences.onboarded ? target : `/onboarding?next=${encodeURIComponent(target)}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed. Is the backend running?");
      setBusy(false);
    }
  }

  const field = "h-11 w-full rounded-md border border-input bg-background px-3 text-base outline-none focus-visible:ring-3 focus-visible:ring-ring/50";
  return (
    <form onSubmit={(e) => void submit(e)} noValidate className="flex flex-col gap-4">
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        Username
        <input className={field} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
      </label>
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        Password
        <input className={field} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button type="submit" className="h-11 text-base" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
      <p className="text-xs text-muted-foreground">New username? Signing in creates the account. Your settings are saved to it.</p>
    </form>
  );
}

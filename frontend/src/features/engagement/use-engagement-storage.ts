"use client";

import { useCallback, useSyncExternalStore } from "react";

const CHANGE_EVENT = "matchlens:engagement-storage";

function subscribe(key: string | null, onChange: () => void) {
  if (!key) return () => undefined;
  const onStorage = (event: StorageEvent) => {
    if (event.key === key) onChange();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

/** `null` is a stable server snapshot; raw strings are stable client snapshots. */
export function useEngagementStorage(key: string | null) {
  const subscribeToKey = useCallback((onChange: () => void) => subscribe(key, onChange), [key]);
  const readSnapshot = useCallback(() => {
    if (!key) return null;
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  }, [key]);
  const raw = useSyncExternalStore(subscribeToKey, readSnapshot, () => null);

  const write = useCallback((value: string) => {
    if (!key) return false;
    try {
      window.localStorage.setItem(key, value);
      window.dispatchEvent(new Event(CHANGE_EVENT));
      return true;
    } catch {
      return false;
    }
  }, [key]);

  return { raw, write };
}

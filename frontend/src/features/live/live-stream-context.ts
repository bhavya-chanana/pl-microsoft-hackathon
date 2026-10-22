"use client";

import { createContext, use } from "react";

export const LiveStreamContext = createContext<{ reconnect: () => void }>({ reconnect: () => undefined });

export function useReconnect(): () => void {
  return use(LiveStreamContext).reconnect;
}

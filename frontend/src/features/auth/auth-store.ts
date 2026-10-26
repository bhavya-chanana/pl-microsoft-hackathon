"use client";

import { create } from "zustand";

export type FanAccount = { sub: string; email: string; name: string; picture: string };
type AuthState = { user: FanAccount | null; setUser: (user: FanAccount | null) => void };
export const useAuth = create<AuthState>((set) => ({ user: null, setUser: (user) => set({ user }) }));

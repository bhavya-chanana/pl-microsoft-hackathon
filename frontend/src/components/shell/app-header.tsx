"use client";

import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { PersonaSwitcher } from "@/features/fi/persona-switcher";
import { signOutRequest } from "@/features/fi/api";
import { useSession } from "@/features/fi/stores";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";

import { ThemeToggle } from "./theme-toggle";

export function AppHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const username = useSession((s) => s.username);
  const signOut = useSession((s) => s.signOut);
  if (pathname === routes.broadcast || pathname === routes.login) return null;
  return (
    <header className="sticky top-0 z-40 border-b border-b-transparent bg-background/90 shadow-[inset_0_-3px_0_var(--tally)] backdrop-blur supports-[backdrop-filter]:bg-background/75">
      <div className="mx-auto flex h-14 max-w-screen-2xl items-center gap-2 px-4 sm:gap-4 sm:px-6">
        <Link href={routes.matches} className="flex min-h-11 items-center font-display text-xl font-semibold tracking-tight uppercase">
          {copy.product}
        </Link>
        <div className="flex-1" />
        <PersonaSwitcher />
        {username && (
          <>
            <span className="hidden max-w-28 truncate text-sm text-muted-foreground md:inline">{username}</span>
            <button type="button" aria-label="Sign out" onClick={() => { void signOutRequest().catch(() => undefined); signOut(); router.replace(routes.login); }}
              className="grid size-11 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground">
              <LogOut aria-hidden className="size-4" />
            </button>
          </>
        )}
        <ThemeToggle />
      </div>
    </header>
  );
}

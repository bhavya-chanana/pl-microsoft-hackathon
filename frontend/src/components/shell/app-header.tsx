"use client";

import { Bot, ClipboardList, MonitorPlay, Radio } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";

import { ConnectionPill } from "./connection-pill";
import { ThemeToggle } from "./theme-toggle";

const NAV = [
  { href: routes.matchCentre, label: copy.nav.matchCentre, short: "Match", icon: Radio },
  { href: routes.broadcast, label: copy.nav.broadcast, short: "Broadcast", icon: MonitorPlay },
  { href: routes.coach, label: copy.nav.coach, short: "Coach", icon: ClipboardList },
  { href: routes.agents, label: copy.nav.agents, short: "Agents", icon: Bot },
] as const;

export function AppHeader() {
  const pathname = usePathname();
  if (pathname === routes.broadcast) return null;
  return (
    <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/75">
      <div className="mx-auto flex h-14 max-w-screen-2xl items-center gap-2 px-4 sm:gap-4 sm:px-6">
        <Link
          href={routes.matchCentre}
          className="flex min-h-11 items-center font-display text-xl font-semibold tracking-tight uppercase"
        >
          {copy.product}
        </Link>
        <nav aria-label="Main" className="flex flex-1 items-center gap-1">
          {NAV.map(({ href, label, short, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-11 items-center gap-2 rounded-md px-2 text-sm font-medium text-muted-foreground transition-colors sm:px-3",
                  "hover:bg-muted hover:text-foreground",
                  active && "bg-muted text-foreground",
                )}
              >
                <Icon aria-hidden className="size-4" />
                <span className="hidden md:inline">{label}</span>
                <span className="sr-only md:hidden">{label}</span>
                <span aria-hidden className="hidden sm:inline md:hidden">
                  {short}
                </span>
              </Link>
            );
          })}
        </nav>
        <ConnectionPill />
        <ThemeToggle />
      </div>
    </header>
  );
}

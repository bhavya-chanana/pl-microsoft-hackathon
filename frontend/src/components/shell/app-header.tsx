"use client";

import { Bot, ClipboardList, FlaskConical, LayoutGrid, MonitorPlay, Radio } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";

import { ConnectionPill } from "./connection-pill";
import { ThemeToggle } from "./theme-toggle";

const NAV = [
  { href: routes.matchCentre, label: copy.nav.matchCentre, short: "Match", icon: Radio },
  { href: routes.multiverse, label: copy.nav.multiverse, short: "Multiverse", icon: LayoutGrid },
  { href: routes.broadcast, label: copy.nav.broadcast, short: "Broadcast", icon: MonitorPlay },
  { href: routes.coach, label: copy.nav.coach, short: "Coach", icon: ClipboardList },
  { href: routes.lab, label: copy.nav.lab, short: "Lab", icon: FlaskConical },
  { href: routes.agents, label: copy.nav.agents, short: "Agents", icon: Bot },
] as const;

export function AppHeader() {
  const pathname = usePathname();
  if (pathname === routes.broadcast) return null;
  return (
    <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/75">
      <div className="mx-auto flex min-h-14 max-w-screen-2xl flex-wrap items-center gap-x-2 px-4 sm:h-14 sm:flex-nowrap sm:gap-4 sm:px-6">
        <Link
          href={routes.matchCentre}
          className="flex min-h-11 items-center font-display text-xl font-semibold tracking-tight uppercase"
        >
          {copy.product}
        </Link>
        <nav aria-label="Main" className="order-last flex basis-full items-center justify-between gap-1 sm:order-none sm:flex-1 sm:basis-auto sm:justify-start">
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
                <span className="hidden xl:inline">{label}</span>
                <span className="sr-only xl:hidden">{label}</span>
                <span aria-hidden className="hidden lg:inline xl:hidden">
                  {short}
                </span>
              </Link>
            );
          })}
        </nav>
        <span className="ml-auto flex items-center gap-2 sm:ml-0 sm:gap-4">
          <ConnectionPill />
          <ThemeToggle />
        </span>
      </div>
    </header>
  );
}

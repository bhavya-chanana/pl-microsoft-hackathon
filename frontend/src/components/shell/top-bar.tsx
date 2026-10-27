"use client";

import { LayoutGroup, m } from "motion/react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { spring } from "@/lib/motion";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { CommandChip } from "./command-palette";
import { isActive, NAV } from "./nav";
import { ProfileMenu } from "./profile-menu";

/** Fixed 56px glass bar from 768px up. Five destinations, search, profile. */
export function TopBar() {
  const pathname = usePathname();
  return (
    <header className="glass fixed inset-x-0 top-0 z-50 hidden h-14 items-center gap-4 border-x-0 border-t-0 px-4 md:flex lg:px-6">
      <Link href="/" className="font-display text-xl font-bold tracking-tight uppercase">
        {copy.product}
      </Link>
      <nav aria-label="Main" className="flex flex-1 items-center justify-center">
        <LayoutGroup id="topnav">
          <ul className="flex items-center gap-1">
            {NAV.map(({ href, label }) => {
              const active = isActive(pathname, href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "relative flex h-10 items-center rounded-full px-4 text-sm font-medium transition-colors",
                      active ? "text-foreground" : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground",
                    )}
                  >
                    {active && (
                      <m.span
                        layoutId="nav-pill"
                        transition={spring.snappy}
                        className="absolute inset-0 rounded-full bg-foreground/10 ring-1 ring-foreground/10"
                      />
                    )}
                    <span className="relative">{label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </LayoutGroup>
      </nav>
      <div className="flex items-center gap-2">
        <CommandChip />
        <ProfileMenu />
      </div>
    </header>
  );
}

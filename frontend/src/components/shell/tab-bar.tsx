"use client";

import { LayoutGroup, motion } from "motion/react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { spring } from "@/lib/motion";
import { cn } from "@/lib/utils";

import { isActive, NAV } from "./nav";

/** Floating glass tab bar under 768px: icon and label always visible. */
export function TabBar() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Main"
      className="glass-strong fixed inset-x-3 bottom-[calc(12px+env(safe-area-inset-bottom))] z-50 h-16 rounded-full p-1.5 md:hidden"
    >
      <LayoutGroup id="tabbar">
        <ul className="grid h-full grid-cols-5">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = isActive(pathname, href);
            return (
              <li key={href} className="min-w-0">
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "relative flex h-full flex-col items-center justify-center gap-0.5 rounded-full text-[11px] font-medium",
                    active ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {active && (
                    <motion.span
                      layoutId="tab-pill"
                      transition={spring.snappy}
                      className="absolute inset-0 rounded-full bg-raised ring-1 ring-foreground/10"
                    />
                  )}
                  <Icon aria-hidden className="relative size-[22px]" />
                  <span className="relative">{label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </LayoutGroup>
    </nav>
  );
}

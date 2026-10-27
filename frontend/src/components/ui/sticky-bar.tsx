"use client";

import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * A bar that sticks under the fixed chrome. Transparent while in flow; once it is stuck it picks up the
 * glass surface and a hairline, so it never paints a hard block over the page wash.
 */
export function StickyBar({ children, className }: { children: React.ReactNode; className?: string }) {
  const sentinel = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    // The sentinel sits just above the bar; once it scrolls under the chrome the bar is stuck.
    const offset = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--stack-h")) || 52;
    const io2 = new IntersectionObserver(([entry]) => setStuck(!entry.isIntersecting), {
      rootMargin: `-${offset}px 0px 0px 0px`,
      threshold: 0,
    });
    io2.observe(el);
    return () => io2.disconnect();
  }, []);
  return (
    <>
      <div ref={sentinel} aria-hidden className="h-px" />
      <div
        className={cn(
          "sticky top-[var(--stack-h)] z-30 -mx-4 px-4 py-2 transition-[background-color,box-shadow] md:-mx-6 md:px-6 xl:-mx-8 xl:px-8 print:hidden",
          stuck ? "glass border-b" : "bg-transparent",
          className,
        )}
      >
        {children}
      </div>
    </>
  );
}

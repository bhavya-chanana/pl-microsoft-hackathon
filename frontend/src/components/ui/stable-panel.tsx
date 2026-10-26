"use client";

import { useLayoutEffect, useRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Wraps tab content. When `panelKey` changes it holds the previous panel's height for 300ms and
 * disables scroll anchoring, so switching tabs never moves the page under the reader.
 */
export function StablePanel({ panelKey, children, className }: { panelKey: string; children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const last = useRef({ key: panelKey, height: 0 });
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (last.current.key !== panelKey) {
      el.style.minHeight = `${last.current.height}px`;
      clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        el.style.minHeight = "";
      }, 300);
      last.current.key = panelKey;
    }
    const ro = new ResizeObserver(() => {
      if (!el.style.minHeight) last.current.height = el.offsetHeight;
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [panelKey]);

  return (
    <div ref={ref} className={cn("panel-hold", className)}>
      {children}
    </div>
  );
}

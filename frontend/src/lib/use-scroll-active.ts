"use client";

import { useEffect, useRef } from "react";

const ACTIVE = '[aria-selected="true"], [aria-checked="true"], [aria-pressed="true"]';

/**
 * For a strip that scrolls sideways: brings the active tab or chip into view whenever `key` changes
 * (and on mount), scrolling only the strip itself, never the page.
 */
export function useScrollActiveIntoView<T extends HTMLElement>(key: unknown) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const strip = ref.current;
    const active = strip?.querySelector<HTMLElement>(ACTIVE);
    if (!strip || !active) return;
    const s = strip.getBoundingClientRect();
    const a = active.getBoundingClientRect();
    const pad = 16;
    if (a.left < s.left + pad) strip.scrollBy({ left: a.left - s.left - pad, behavior: "auto" });
    else if (a.right > s.right - pad) strip.scrollBy({ left: a.right - s.right + pad, behavior: "auto" });
  }, [key]);
  return ref;
}

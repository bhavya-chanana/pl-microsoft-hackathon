"use client";

import { Star } from "lucide-react";

import { cn } from "@/lib/utils";

export type RatingTone = "potm" | "good" | "ok" | "poor";

/** Player of the match or a 10 is blue; 7+ green; 5+ orange; under 5 red. */
export function ratingTone(value: number, potm = false): RatingTone {
  return potm || value >= 10 ? "potm" : value >= 7 ? "good" : value >= 5 ? "ok" : "poor";
}

const SIZE = { sm: "h-[18px] min-w-[28px] px-[5px] text-xs", md: "h-[22px] min-w-[32px] px-1.5 text-xs", lg: "h-7 min-w-[40px] px-2 text-sm" } as const;

/** A number in a colour-coded pill. Rating is from this match only; 6.0 is average. `text` swaps the number (lens chips). */
export function RatingBadge({ value, text, potm, size = "md", className }: { value: number; text?: string; potm?: boolean; size?: keyof typeof SIZE; className?: string }) {
  const tone = ratingTone(value, potm);
  return (
    <span
      title={text ? undefined : "Rating from this match only. 6.0 is average."}
      className={cn("inline-flex items-center justify-center gap-0.5 rounded-xl font-medium tabular-nums", SIZE[size], className)}
      style={{ background: `var(--rating-${tone})`, color: "var(--rating-ink)" }}
    >
      {tone === "potm" && <Star aria-label="Player of the match" className="size-3 fill-current" />}
      {text ?? value.toFixed(1)}
    </span>
  );
}

/** A neutral pill for lens values (distance, speed, passes, shots) that carry no rating colour. */
export function LensBadge({ text, className }: { text: string; className?: string }) {
  return <span className={cn("inline-flex h-[18px] min-w-[28px] items-center justify-center rounded-xl bg-foreground px-[5px] text-xs font-medium tabular-nums text-background", className)}>{text}</span>;
}

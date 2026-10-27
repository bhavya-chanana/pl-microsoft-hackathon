"use client";

import { LayoutGroup, m } from "motion/react";
import { useId } from "react";

import { spring } from "@/lib/motion";
import { cn } from "@/lib/utils";

export type SegmentedOption<T extends string> = { value: T; label: React.ReactNode };

/** Pill-track segmented control with a sliding indicator. Always visible labels, 44px targets. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  className,
  size = "md",
  disabled,
  title,
}: {
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  className?: string;
  size?: "md" | "sm";
  disabled?: boolean;
  title?: string;
}) {
  const id = useId();
  return (
    <LayoutGroup id={id}>
      <div
        role="radiogroup"
        aria-label={label}
        aria-disabled={disabled || undefined}
        title={title}
        className={cn(disabled && "opacity-50", "inline-flex w-fit max-w-[420px] min-w-[min(100%,18rem)] items-center gap-1 rounded-full bg-raised p-1 ring-1 ring-border", className)}
      >
        {options.map((o) => {
          const active = o.value === value;
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={disabled}
              onClick={() => onChange(o.value)}
              className={cn(
                "relative flex flex-1 items-center justify-center gap-1.5 rounded-full px-3 text-sm font-medium whitespace-nowrap transition-colors outline-offset-0",
                size === "md" ? "h-11" : "h-9",
                active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {active && (
                <m.span
                  layoutId="seg"
                  transition={spring.snappy}
                  className="absolute inset-0 rounded-full bg-card shadow-raised ring-1 ring-border"
                />
              )}
              <span className="relative inline-flex items-center gap-1.5">{o.label}</span>
            </button>
          );
        })}
      </div>
    </LayoutGroup>
  );
}

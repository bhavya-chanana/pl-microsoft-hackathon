"use client";

import type { ReactNode } from "react";
import { LoaderCircle, TriangleAlert, Volume2, VolumeX } from "lucide-react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { problemOf, unlockVoice, useVoice } from "./voice";

/** Three bars that move while the voice is speaking (still when motion is reduced). */
function Equaliser() {
  return (
    <span aria-hidden className="flex h-4 items-end gap-0.5">
      {[0.6, 1, 0.45].map((h, i) => (
        <span
          key={i}
          className="w-0.5 origin-bottom rounded-full bg-current motion-safe:animate-pulse"
          style={{ height: `${h * 100}%`, animationDelay: `${i * 160}ms` }}
        />
      ))}
    </span>
  );
}

/**
 * The sound switch: always says in words whether sound is off, starting, on, speaking or blocked,
 * so a speaker icon alone never has to carry it. Turning it on unlocks audio inside the click.
 */
export function SoundButton({
  enabled,
  onChange,
  name = copy.narration.name,
  labels = {},
  compact = false,
  className,
}: {
  enabled: boolean;
  onChange: (on: boolean) => void;
  /** The control's accessible name; its on/off state is `aria-pressed`. */
  name?: string;
  /** Visible text per state, where the default does not fit (a short off label, or "Playing"). */
  labels?: Partial<Record<"off" | "on" | "speaking", ReactNode>>;
  /** Below the sm breakpoint use the shortest words, so a crowded bar still fits. */
  compact?: boolean;
  className?: string;
}) {
  const status = useVoice((s) => s.status);
  const problem = enabled ? problemOf(status) : null;
  const t = copy.narration.button;

  const words = (full: ReactNode, short: ReactNode) =>
    compact ? (
      <>
        <span className="sm:hidden">{short}</span>
        <span className="max-sm:hidden">{full}</span>
      </>
    ) : (
      full
    );
  let icon = <VolumeX aria-hidden className="size-5" />;
  let label: ReactNode = labels.off ?? t.off;
  let tone = "text-muted-foreground";
  if (enabled && problem === "unsupported") {
    label = t.unsupported;
    tone = "text-muted-foreground opacity-60";
  } else if (enabled && problem === "no-voice") {
    label = t.noVoice;
    tone = "text-muted-foreground opacity-60";
  } else if (problem) {
    icon = (
      <span className="relative">
        <VolumeX aria-hidden className="size-5" />
        <TriangleAlert aria-hidden className="absolute -top-1.5 -right-2 size-2.5 fill-warning text-background" />
      </span>
    );
    label = words(t.blocked, t.short.blocked);
    tone = "text-warning";
  } else if (enabled && status === "starting") {
    icon = <LoaderCircle aria-hidden className="size-5 motion-safe:animate-spin" />;
    label = t.starting;
    tone = "text-muted-foreground";
  } else if (enabled && status === "speaking") {
    icon = <Equaliser />;
    label = labels.speaking ?? t.speaking;
    tone = "text-foreground";
  } else if (enabled) {
    icon = <Volume2 aria-hidden className="size-5" />;
    label = labels.on ?? words(t.on, t.short.on);
    tone = "text-foreground";
  }

  return (
    <button
      type="button"
      aria-label={name}
      aria-pressed={enabled}
      aria-busy={enabled && status === "starting"}
      onClick={() => {
        if (!enabled || problem) unlockVoice();
        // A problem is retried by clicking again; otherwise the click flips the switch.
        if (!(enabled && problem)) onChange(!enabled);
      }}
      className={cn(
        "inline-flex h-11 shrink-0 items-center gap-2 rounded-full px-3.5 text-sm font-medium ring-1 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        enabled ? "bg-foreground/10 ring-border" : "ring-border hover:bg-raised",
        problem && "ring-warning/60",
        tone,
        className,
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

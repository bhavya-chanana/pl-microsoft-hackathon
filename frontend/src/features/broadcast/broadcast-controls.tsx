"use client";

import { ArrowLeft, Volume2, VolumeX } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { buttonVariants } from "@/components/ui/button";
import { Toggle } from "@/components/ui/toggle";
import { Narrator } from "@/features/companion/narration";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

/** The controls step aside once the pointer has been still this long. */
const IDLE_MS = 2_500;

/** Shown while the viewer is moving the pointer or tabbing; hidden on an idle clean feed. */
function useActive(): boolean {
  const [active, setActive] = useState(true);
  useEffect(() => {
    let timer = setTimeout(() => setActive(false), IDLE_MS);
    const wake = () => {
      setActive(true);
      clearTimeout(timer);
      timer = setTimeout(() => setActive(false), IDLE_MS);
    };
    const events = ["pointermove", "pointerdown", "keydown"] as const;
    for (const e of events) window.addEventListener(e, wake);
    return () => {
      clearTimeout(timer);
      for (const e of events) window.removeEventListener(e, wake);
    };
  }, []);
  return active;
}

/**
 * The way out of the full-screen feed and its commentary switch. Browsers only
 * let a page play sound after a click, so commentary starts off.
 */
export function BroadcastControls() {
  const active = useActive();
  const [sound, setSound] = useState(false);
  return (
    <div
      className={cn(
        "absolute right-[2%] bottom-[2%] z-30 flex items-center gap-2 transition-opacity duration-300",
        "focus-within:opacity-100",
        active ? "opacity-100" : "pointer-events-none opacity-0",
      )}
    >
      <Link
        href={routes.matchCentre}
        className={cn(
          buttonVariants({ variant: "secondary" }),
          "min-h-11 gap-2 bg-graphic/80 px-3 text-graphic-foreground hover:bg-graphic",
        )}
      >
        <ArrowLeft aria-hidden />
        {copy.broadcastControls.back}
      </Link>
      <Toggle
        variant="outline"
        className="size-11 shrink-0 border-graphic-foreground/20 bg-graphic/80 text-graphic-foreground hover:bg-graphic hover:text-graphic-foreground aria-pressed:bg-graphic aria-pressed:text-telestrator"
        pressed={sound}
        onPressedChange={setSound}
        aria-label={sound ? copy.broadcastControls.soundOn : copy.broadcastControls.sound}
        title={sound ? copy.broadcastControls.soundOn : copy.broadcastControls.sound}
      >
        {sound ? <Volume2 aria-hidden /> : <VolumeX aria-hidden />}
      </Toggle>
      <Narrator enabled={sound} />
    </div>
  );
}

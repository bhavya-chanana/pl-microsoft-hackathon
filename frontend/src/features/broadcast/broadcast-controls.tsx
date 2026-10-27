"use client";

import { Link2, Volume2, VolumeX, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { toast } from "sonner";

import { Button, buttonVariants } from "@/components/ui/button";
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

const HINT_KEY = "matchlens.broadcast.hint.v1";

/**
 * The way out of the full-screen feed, a link to put it in OBS, and the commentary switch, in one glass
 * pill that steps aside when idle. Browsers only play sound after a click, so commentary starts off.
 */
export function BroadcastControls() {
  const active = useActive();
  const [sound, setSound] = useState(false);
  const [hint, setHint] = useState(false);
  useEffect(() => {
    let first = false;
    try {
      first = !localStorage.getItem(HINT_KEY);
      if (first) localStorage.setItem(HINT_KEY, "1");
    } catch {
      /* storage can be blocked */
    }
    if (!first) return;
    const on = setTimeout(() => setHint(true), 0);
    const off = setTimeout(() => setHint(false), 6_000);
    return () => {
      clearTimeout(on);
      clearTimeout(off);
    };
  }, []);
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast.success(copy.broadcastControls.copied);
    } catch {
      toast.error(copy.broadcastControls.copyFailed);
    }
  };
  const show = active || hint;
  return (
    <div
      className={cn(
        "absolute top-4 left-1/2 z-30 flex -translate-x-1/2 flex-col items-center gap-2 transition-opacity duration-300 focus-within:opacity-100",
        show ? "opacity-100" : "pointer-events-none opacity-0",
      )}
    >
      <div className="glass-strong flex items-center gap-1 rounded-full p-1.5">
        <Link href={routes.explore} className={cn(buttonVariants({ variant: "ghost" }), "h-11 gap-2 rounded-full px-4")}>
          <X aria-hidden />
          {copy.broadcastControls.exit}
        </Link>
        <Button variant="ghost" className="h-11 gap-2 rounded-full px-4" onClick={() => void copyLink()}>
          <Link2 aria-hidden />
          {copy.broadcastControls.copyLink}
        </Button>
        <Toggle
          className="size-11 shrink-0 rounded-full"
          pressed={sound}
          onPressedChange={setSound}
          aria-label={sound ? copy.broadcastControls.soundOn : copy.broadcastControls.sound}
          title={sound ? copy.broadcastControls.soundOn : copy.broadcastControls.sound}
        >
          {sound ? <Volume2 aria-hidden /> : <VolumeX aria-hidden />}
        </Toggle>
        <Narrator enabled={sound} />
      </div>
      {hint && (
        <p role="status" className="glass rounded-full px-4 py-1.5 text-[13px]">
          {copy.broadcastControls.hint}
        </p>
      )}
    </div>
  );
}

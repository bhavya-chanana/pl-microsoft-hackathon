"use client";

import { SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";

import { useShell } from "./shell-store";

/** Stream settings from the mobile corner, where the header pill is hidden once collapsed. */
export function StreamButton() {
  const open = useShell((s) => s.setStream);
  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-11 rounded-full bg-foreground/5 ring-1 ring-foreground/10 hover:bg-foreground/10"
      onClick={() => open(true)}
      aria-label={copy.stream.open}
    >
      <SlidersHorizontal aria-hidden className="size-4" />
    </Button>
  );
}

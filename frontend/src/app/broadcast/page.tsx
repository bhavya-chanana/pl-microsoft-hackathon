import type { Metadata } from "next";

import { BroadcastMonitor } from "@/features/broadcast/broadcast-monitor";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.broadcast };

/** Clean programme output for a capture card or a second screen: no chrome. */
export default function BroadcastPage() {
  return (
    <main>
      <h1 className="sr-only">{copy.nav.broadcast}</h1>
      <BroadcastMonitor fill />
    </main>
  );
}

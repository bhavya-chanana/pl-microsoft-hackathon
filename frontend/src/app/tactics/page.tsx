import type { Metadata } from "next";

import { TacticsRoute } from "@/features/tactics/tactics-board";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.tactics };

export default function TacticsPage() {
  return <TacticsRoute />;
}

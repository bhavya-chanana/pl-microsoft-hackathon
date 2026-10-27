import type { Metadata } from "next";

import { StatsPage } from "@/features/stats/stats-page";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.stats };

export default function Page() {
  return <StatsPage />;
}

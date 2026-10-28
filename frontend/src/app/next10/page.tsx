import type { Metadata } from "next";

import { MatchesList } from "@/features/next10/matches-list";
import { n10 } from "@/features/next10/copy";

export const metadata: Metadata = { title: n10.product };

export default function Page() {
  return <MatchesList />;
}

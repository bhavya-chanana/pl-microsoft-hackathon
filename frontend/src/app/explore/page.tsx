import type { Metadata } from "next";

import { ExplorePage } from "@/features/explore/explore-page";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.explore };

export default function Page() {
  return <ExplorePage />;
}

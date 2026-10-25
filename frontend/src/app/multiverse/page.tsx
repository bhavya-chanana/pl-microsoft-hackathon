import type { Metadata } from "next";

import { Multiverse } from "@/features/multiverse/multiverse";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.multiverse };

export default function MultiversePage() {
  return <Multiverse />;
}

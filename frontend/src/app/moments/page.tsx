import type { Metadata } from "next";

import { MomentsPage } from "@/features/moments/moments-page";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.moments };

export default function Page() {
  return <MomentsPage />;
}

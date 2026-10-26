import type { Metadata } from "next";

import { CoachPage } from "@/features/coach/coach-page";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.coach };

export default function Page() {
  return <CoachPage />;
}

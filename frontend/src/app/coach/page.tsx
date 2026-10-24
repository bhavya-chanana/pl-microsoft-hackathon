import type { Metadata } from "next";

import { CoachRoom } from "@/features/coach/coach-room";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.coach };

export default function CoachPage() {
  return <CoachRoom />;
}

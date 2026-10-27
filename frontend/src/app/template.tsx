"use client";

import { m } from "motion/react";

import { fadeUp } from "@/lib/motion";

/** Re-mounts on every navigation, so each page eases in once. Opacity and a short rise only. */
export default function Template({ children }: { children: React.ReactNode }) {
  return <m.div {...fadeUp}>{children}</m.div>;
}

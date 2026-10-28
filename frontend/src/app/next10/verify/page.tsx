import type { Metadata } from "next";

import { n10 } from "@/features/next10/copy";
import { VerifyBoard } from "@/features/next10/verify-board";

export const metadata: Metadata = { title: n10.verify.heading };

export default function Page() {
  return <VerifyBoard />;
}

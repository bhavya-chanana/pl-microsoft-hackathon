import type { Metadata } from "next";

import { ScenarioLab } from "@/features/lab/scenario-lab";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.lab };

export default function LabPage() {
  return <ScenarioLab />;
}

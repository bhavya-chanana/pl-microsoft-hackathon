import type { Metadata } from "next";

import { AgentRoom } from "@/features/agents/agent-room";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.nav.agents };

export default function AgentsPage() {
  return <AgentRoom />;
}

import { BadgeCheck, Database, PencilLine } from "lucide-react";

import type { FactCheckStatus } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const STYLE: Record<FactCheckStatus, { icon: typeof BadgeCheck; tone: string }> = {
  verified: { icon: BadgeCheck, tone: "text-muted-foreground" },
  rewritten: { icon: PencilLine, tone: "text-muted-foreground" },
  blocked: { icon: Database, tone: "text-muted-foreground" },
  data_only: { icon: Database, tone: "text-muted-foreground" },
};

/** Provenance label: icon + words, never colour alone. */
export function FactCheckBadge({ status, className }: { status: FactCheckStatus; className?: string }) {
  const { icon: Icon, tone } = STYLE[status];
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-medium", tone, className)} title={copy.factCheckHelp[status]}>
      <Icon aria-hidden className="size-3.5" />
      {copy.factCheck[status]}
    </span>
  );
}

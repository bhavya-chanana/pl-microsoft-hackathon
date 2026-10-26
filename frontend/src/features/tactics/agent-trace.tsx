import { CheckCircle2, CircleDashed, LoaderCircle, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { TacticStatus, TacticsBoard } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { labelOf } from "./lib";

const t = copy.tactics.trace;

const ICON = {
  thinking: LoaderCircle,
  evaluating: LoaderCircle,
  ready: CheckCircle2,
  rejected: XCircle,
  pending: CircleDashed,
} as const;
const TONE = {
  thinking: "text-muted-foreground",
  evaluating: "text-data",
  ready: "text-verified",
  rejected: "text-blocked",
  pending: "text-muted-foreground",
} as const;

function Row({ name, state, text }: { name: string; state: TacticStatus | "pending"; text: string }) {
  const Icon = ICON[state];
  const spin = state === "thinking" || state === "evaluating";
  return (
    <li className="flex items-center gap-2 text-sm">
      <Icon aria-hidden className={cn("size-4 shrink-0", TONE[state], spin && "motion-safe:animate-spin")} />
      <span className="font-medium">{name}</span>
      <span className={cn("ml-auto text-xs", TONE[state])}>{text}</span>
    </li>
  );
}

/** What the agents did to make this board: one call per persona, then the verdict, with the model used. */
export function AgentTrace({ board }: { board: TacticsBoard }) {
  const ready = board.tactics.filter((x) => x.status === "ready").length;
  const settled = board.tactics.filter((x) => x.status === "ready" || x.status === "rejected").length;
  const verdictState = board.verdict ? "ready" : board.status === "failed" ? "rejected" : "pending";
  return (
    <section aria-labelledby="trace-heading" className="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-raised">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 id="trace-heading" className="font-display text-lg font-semibold">
          {t.heading}
        </h2>
        <Badge variant="outline">
          {t.model}: {board.model_label}
        </Badge>
        {board.recorded && (
          <Badge className="bg-rewritten/15 text-rewritten" title={t.recordedHelp}>
            {t.recorded}
          </Badge>
        )}
        <span className="text-xs text-muted-foreground">
          {t.evaluator}: {board.evaluator}
        </span>
      </div>
      <p role="status" aria-live="polite" className="text-sm font-medium tabular-nums">
        {board.status === "failed" ? t.failed : t.statusLine(ready, board.tactics.length)}
        <span className="sr-only">{settled === board.tactics.length && board.tactics.length > 0 ? `. ${board.verdict?.headline ?? ""}` : ""}</span>
      </p>
      <ul className="grid gap-x-8 gap-y-1.5 sm:grid-cols-2">
        {board.tactics.map((x) => (
          <Row key={x.tactic_id} name={labelOf(copy.tactics.personas, x.persona)} state={x.status} text={t.calls[x.status]} />
        ))}
        <Row name={t.verdict} state={verdictState} text={board.verdict ? t.verdictState.done : board.status === "failed" ? t.verdictState.failed : t.verdictState.pending} />
      </ul>
      {board.errors.length > 0 && (
        <ul role="alert" className="list-disc pl-5 text-sm text-blocked">
          {board.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

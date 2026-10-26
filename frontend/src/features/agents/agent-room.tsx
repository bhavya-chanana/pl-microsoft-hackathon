"use client";

import dynamic from "next/dynamic";
import { RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { FactCheckBadge } from "@/features/companion/fact-check-badge";
import { useLive } from "@/features/live/live-store";
import { useDecisions, useHealth } from "@/features/live/queries";
import { ReplayControls } from "@/features/replay/replay-controls";
import type { Decision, Signal } from "@/lib/contract";
import { copy, signalLabels } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { useMediaQuery } from "@/hooks/use-media-query";

const PipelineGraph = dynamic(() => import("./pipeline-graph"), {
  ssr: false,
  loading: () => <Skeleton className="h-80 w-full rounded-lg" />,
});

const PLAN_TONE: Record<string, string> = {
  full: "bg-verified/15 text-verified",
  light: "bg-data/15 text-data",
  data_only: "bg-muted text-muted-foreground",
  skip: "bg-muted text-muted-foreground",
  retract: "bg-blocked/15 text-blocked",
};

function Metric({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-0.5 rounded-lg border bg-card p-4 shadow-raised", className)}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate font-display text-2xl font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  if (value < 10) return `${value.toFixed(1)} ms`;
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
}

function LatestInsight() {
  const latest = useLive((s) => {
    let best = null as (typeof s.insights)[string] | null;
    for (const i of Object.values(s.insights)) {
      if (i.stage === "agent" && (!best || i.elapsed > best.elapsed)) best = i;
    }
    return best;
  });
  if (!latest) return null;
  return (
    <section aria-labelledby="latest-heading" className="flex flex-col gap-2 rounded-lg border bg-card p-4 shadow-raised">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 id="latest-heading" className="text-xs font-medium text-muted-foreground">
          {copy.agents.latest}
        </h2>
        <span className="font-display text-lg font-semibold tabular-nums">{latest.minute_label}</span>
        <span className="text-xs text-muted-foreground">{signalLabels[latest.signal_type] ?? latest.signal_type}</span>
        <FactCheckBadge status={latest.fact_check.status} className="ml-auto" />
      </div>
      <p className="font-display text-xl font-semibold">{latest.headline}</p>
      {latest.explanation && <p className="max-w-prose text-sm text-muted-foreground">{latest.explanation}</p>}
      {latest.latency_ms !== null && <p className="text-xs text-muted-foreground">{copy.feed.latency(latest.latency_ms)}</p>}
    </section>
  );
}

function DecisionRow({ decision, signal }: { decision: Decision; signal: Signal | undefined }) {
  return (
    <tr className="border-b last:border-0">
      <td className="py-2 pr-3 font-display text-base font-semibold tabular-nums">{signal?.minute_label ?? "–"}</td>
      <td className="py-2 pr-3">
        {signalLabels[signal?.type ?? decision.signal_id.split(":")[0]] ?? decision.signal_id.split(":")[0]}
        {decision.revision > 0 && <span className="ml-1 text-xs text-muted-foreground">(rev {decision.revision})</span>}
      </td>
      <td className="py-2 pr-3">
        <span className={cn("rounded-sm px-1.5 py-0.5 text-xs font-medium", PLAN_TONE[decision.plan] ?? "bg-muted")}>
          {copy.agents.plans[decision.plan] ?? decision.plan}
        </span>
      </td>
      <td className="py-2 text-muted-foreground">{decision.reason}</td>
    </tr>
  );
}

function Decisions() {
  const query = useDecisions();
  const signals = useLive((s) => s.signals);
  const bySignal = new Map(signals.map((s) => [s.signal_id, s]));
  if (query.isPending) return <Skeleton className="h-64 w-full rounded-lg" />;
  if (query.isError && !query.data) {
    return (
      <div role="alert" className="flex flex-col items-start gap-3 rounded-lg border bg-card p-4">
        <p className="text-sm">{copy.stats.error}</p>
        <Button variant="outline" className="min-h-11 px-4" onClick={() => void query.refetch()}>
          <RotateCw aria-hidden />
          {copy.connection.retry}
        </Button>
      </div>
    );
  }
  const rows = query.data.slice().reverse();
  if (!rows.length) return <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{copy.agents.decisionsEmpty}</p>;
  return (
    <div tabIndex={0} aria-label={copy.agents.decisions} className="max-h-[28rem] overflow-auto rounded-lg border bg-card px-4">
      <table className="w-full min-w-[36rem] text-left text-sm">
        <thead className="sticky top-0 bg-card text-xs text-muted-foreground">
          <tr className="border-b">
            <th scope="col" className="py-2 pr-3 font-medium">Minute</th>
            <th scope="col" className="py-2 pr-3 font-medium">Signal</th>
            <th scope="col" className="py-2 pr-3 font-medium">Plan</th>
            <th scope="col" className="py-2 font-medium">Reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d, i) => (
            <DecisionRow key={`${rows.length - i}:${d.signal_id}:${d.plan}`} decision={d} signal={bySignal.get(d.signal_id)} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AgentRoom() {
  const health = useHealth();
  const wide = useMediaQuery("(min-width: 768px)");
  const activeAgents = useLive((s) => {
    let best = null as (typeof s.insights)[string] | null;
    for (const i of Object.values(s.insights)) {
      if (i.stage === "agent" && (!best || i.elapsed > best.elapsed)) best = i;
    }
    return best?.agents.join(",") ?? "";
  });
  const model = health.data?.llm ?? null;
  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-4 sm:px-6 lg:py-6">
      <div className="flex max-w-3xl flex-col gap-2">
        <h1 className="font-display text-display font-semibold">{copy.agents.heading}</h1>
        <p className="text-sm text-muted-foreground">{copy.agents.intro}</p>
      </div>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Metric label={copy.agents.model} value={model ?? "–"} className="col-span-2 sm:col-span-1" />
        <Metric label={copy.agents.p95} value={ms(health.data?.agent_insight_ms_p95)} />
        <Metric label={copy.agents.processing} value={ms(health.data?.processing_ms_p95)} />
      </dl>
      <section aria-labelledby="pipeline-heading" className="flex flex-col gap-3">
        <h2 id="pipeline-heading" className="font-display text-lg font-semibold">
          {copy.agents.pipeline}
        </h2>
        <PipelineGraph activeAgents={activeAgents ? activeAgents.split(",") : []} vertical={!wide} />
        <LatestInsight />
      </section>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <section aria-labelledby="decisions-heading" className="flex min-w-0 flex-col gap-3">
          <h2 id="decisions-heading" className="font-display text-lg font-semibold">
            {copy.agents.decisions}
          </h2>
          <Decisions />
        </section>
        <ReplayControls className="self-start" />
      </div>
    </div>
  );
}

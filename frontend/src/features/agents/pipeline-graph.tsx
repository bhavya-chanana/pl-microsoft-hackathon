"use client";

import "@xyflow/react/dist/style.css";

import { Background, type Edge, Handle, type Node, type NodeProps, Position, ReactFlow } from "@xyflow/react";
import { useTheme } from "next-themes";
import { useMemo } from "react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

type StepId = keyof typeof copy.agents.nodes;
type StepData = { id: StepId; active: boolean; vertical: boolean };

function StepNode({ data }: NodeProps<Node<StepData>>) {
  const text = copy.agents.nodes[data.id];
  return (
    <div
      className={cn(
        "w-44 rounded-lg border bg-card px-3 py-2 text-card-foreground shadow-raised transition-colors",
        data.active && "border-verified ring-1 ring-verified",
      )}
    >
      <Handle type="target" position={data.vertical ? Position.Top : Position.Left} className="!size-1.5 !border-0 !bg-muted-foreground" />
      <p className="font-display text-base leading-tight font-semibold">{text.title}</p>
      <p className="text-xs text-muted-foreground">{text.body}</p>
      <Handle type="source" position={data.vertical ? Position.Bottom : Position.Right} className="!size-1.5 !border-0 !bg-muted-foreground" />
    </div>
  );
}

const nodeTypes = { step: StepNode };

const LAYOUT: { id: StepId; x: number; y: number }[] = [
  { id: "signal", x: 0, y: 90 },
  { id: "coordinator", x: 210, y: 90 },
  { id: "tactical", x: 420, y: 20 },
  { id: "narrator", x: 420, y: 160 },
  { id: "fact_checker", x: 630, y: 90 },
  { id: "producer", x: 840, y: 20 },
  { id: "personaliser", x: 840, y: 160 },
  { id: "publish", x: 1050, y: 90 },
];

const LINKS: [StepId, StepId][] = [
  ["signal", "coordinator"],
  ["coordinator", "tactical"],
  ["coordinator", "narrator"],
  ["tactical", "fact_checker"],
  ["narrator", "fact_checker"],
  ["fact_checker", "producer"],
  ["fact_checker", "personaliser"],
  ["producer", "publish"],
  ["personaliser", "publish"],
];

/** The multi-agent workflow, lighting up the agents that worked on the latest insight. */
export default function PipelineGraph({ activeAgents, vertical = false }: { activeAgents: string[]; vertical?: boolean }) {
  const { resolvedTheme } = useTheme();
  const active = useMemo(() => new Set<string>(["signal", "coordinator", "publish", ...activeAgents]), [activeAgents]);
  const nodes: Node<StepData>[] = useMemo(
    () =>
      LAYOUT.map(({ id, x, y }) => ({
        id,
        type: "step",
        position: vertical ? { x: y * 1.3, y: x * 0.48 } : { x, y },
        draggable: false,
        selectable: false,
        data: { id, active: active.has(id), vertical },
      })),
    [active, vertical],
  );
  const edges: Edge[] = useMemo(
    () =>
      LINKS.map(([source, target]) => {
        const on = active.has(source) && active.has(target);
        return {
          id: `${source}-${target}`,
          source,
          target,
          animated: on,
          className: on ? "[&_path]:!stroke-verified" : "[&_path]:!stroke-border",
        };
      }),
    [active],
  );
  return (
    <div
      className={cn("w-full overflow-hidden rounded-lg border bg-background", vertical ? "h-[36rem]" : "h-80")}
      role="img" aria-label={`${copy.agents.pipeline}: ${LAYOUT.map((n) => copy.agents.nodes[n.id].title).join(", ")}`}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode={resolvedTheme === "light" ? "light" : "dark"}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        panOnScroll={false}
        preventScrolling={false}
        className="!bg-transparent"
      >
        <Background gap={24} size={1} />
      </ReactFlow>
    </div>
  );
}

import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { humanize } from "../lib/humanize";
import type { Bundle } from "../lib/types";
import { useInspector } from "../stores/ui";

const COLORS: Record<string, string> = {
  necessary_physical: "#3ddc97",
  observed_temporal: "#7eb6ff",
  inferred: "#e6b450",
  unknown: "#e06c75",
};

export function WorkflowGraph({ bundle }: { bundle: Bundle }) {
  const selectStep = useInspector((state) => state.selectStep);
  const setTime = useInspector((state) => state.setTime);
  const nodes: Node[] = bundle.steps.map((step, index) => ({
    id: step.local_id,
    position: { x: (index % 3) * 260, y: Math.floor(index / 3) * 150 },
    data: {
      label: (
        <div className="text-left text-xs">
          <div className="mono text-[10px] text-[var(--muted)]">{step.number}. {humanize(step.evidence_class)}</div>
          <div className="font-medium">{step.name}</div>
          <div className="text-[var(--muted)]">{step.completion_condition}</div>
        </div>
      ),
    },
    style: {
      background: "#141820",
      color: "#e7ecf3",
      border: "1px solid #2a3140",
      width: 230,
    },
  }));
  const edges: Edge[] = bundle.dependencies.map((dep) => ({
    id: dep.id,
    source: dep.from_step,
    target: dep.to_step,
    label: humanize(dep.category),
    style: { stroke: COLORS[dep.category] ?? "#8b95a8" },
    data: { rationale: dep.rationale },
  }));
  return (
    <div className="h-[520px] overflow-hidden rounded-lg border border-[var(--line)]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        onNodeClick={(_event, node) => {
          selectStep(node.id);
          const step = bundle.steps.find((item) => item.local_id === node.id);
          if (step?.time_start != null) setTime(step.time_start);
        }}
        onEdgeClick={(_event, edge) => {
          window.alert(String(edge.data?.rationale || edge.label || "Dependency"));
        }}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}

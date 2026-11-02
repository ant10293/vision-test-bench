import type { BBox, Bundle } from "./types";

export type RecoveryOutcome = "supported" | "provisional" | "conflicting" | "rejected";
export type RecoveryLayer = "vlm" | "tracker" | "sam" | "recovered" | "canonical";

export interface RecoverySeed {
  seed_id: string;
  object_local_id: string;
  instance_id: string | null;
  timestamp: number;
  bbox: BBox | null;
  points: SeedPoint[];
  trust: "human_verified" | "independently_supported" | "candidate" | "rejected";
  source: string;
  source_ref: string;
  explanation: string;
  checks: { check: string; passed: boolean; detail: string }[];
  manual: boolean;
}

export interface SeedPoint {
  x: number;
  y: number;
  negative?: boolean;
}

export interface RecoveryTarget {
  target_id: string;
  object_local_id: string;
  instance_id: string | null;
  label: string;
  time_start: number;
  time_end: number;
  source: string;
  state: string;
  reasons: string[];
  details: string[];
  seeds?: RecoverySeed[];
  difficulty?: string;
  recoverable?: boolean;
  blocker?: string;
  direction?: string;
  support?: { observations_in_interval: number; measured_in_interval: number; usable_seeds: number };
}

export interface RecoveryCandidates {
  run_id: string;
  revision_id: string;
  targets: RecoveryTarget[];
  seeds: RecoverySeed[];
  seed_counts: Record<string, number>;
  objects: { local_id: string; label: string; instance_id: string | null }[];
  price_per_frame_usd: number | null;
}

export interface PlannedRequest {
  key: string;
  prompt: string;
  direction: string;
  frame_times: number[];
  source_start: number;
  source_end: number;
  billed_frames: number;
  cached: boolean;
  estimated_cost_usd: number | null;
  width: number;
  height: number;
  objects: { target_id: string; object_local_id: string; sam_object_id: number; seed: RecoverySeed; seed_frame_index: number }[];
}

export interface RecoveryPlan {
  plan_hash: string;
  requests: PlannedRequest[];
  blocked: { target_id: string; object_local_id: string; time_start: number; time_end: number; reason: string }[];
  totals: {
    requests: number;
    remote_requests: number;
    cached_requests: number;
    frames: number;
    billed_frames: number;
    estimated_cost_usd: number | null;
    cost_note: string;
  };
  notes: string[];
  violations: string[];
}

export interface ProposalRow {
  timestamp: number;
  frame_index: number;
  bbox: BBox;
  confidence: number | null;
  outcome: RecoveryOutcome;
  association: string;
  partial: boolean;
  context: boolean;
  direction: string;
  seed_trust: string;
  checks: { check: string; passed: boolean; detail: string }[];
}

export interface RecoveryProposal {
  id: string;
  generation: number;
  current: boolean;
  target_id: string;
  object_local_id: string;
  time_start: number;
  time_end: number;
  decision: "pending" | "accepted" | "rejected";
  rows: ProposalRow[];
  applied_revision_id: string | null;
  metrics: {
    counts: Record<RecoveryOutcome | "unobserved_frames", number>;
    previously_missing_frames: number;
    new_supported_frames: number;
    original_rows_in_interval: number;
    remaining_gaps: { start: number; end: number; frames: number }[];
    identity: { sam_object_id: number; association: string; note: string; seed_trust: string }[];
    usage: { requests: number; billed_frames: number; latency_ms: number; input_tokens: number; output_tokens: number };
    synthetic_response: boolean;
    human_box_overlap: { frames: number; mean_iou: number } | null;
    accuracy_note: string;
  };
}

export interface RecoveryExperiment {
  id: string;
  run_id: string;
  status: string;
  mode: string;
  generation: number;
  targets: RecoveryTarget[];
  limits: Record<string, number | boolean>;
  plan: RecoveryPlan | null;
  error: string;
  remote_submitted: boolean;
  recorded_cost_usd: number | null;
  applied_revision_id: string | null;
  cancel_requested: boolean;
  requests: {
    cache_key: string;
    status: string;
    submission: string;
    source: string;
    billed_frames: number;
    latency_ms: number;
    error: string;
  }[];
  proposals: RecoveryProposal[];
}

export interface LayerBox {
  key: string;
  layer: RecoveryLayer;
  objectId: string;
  bbox: BBox;
  timestamp: number;
  /** Tells apart a directly observed box from an estimate or a conflict. */
  dashed: boolean;
  details: string[];
}

export const LAYERS: { key: RecoveryLayer; label: string; color: string }[] = [
  { key: "vlm", label: "Original VLM", color: "#7EB6FF" },
  { key: "tracker", label: "Tracker", color: "#7dcea0" },
  { key: "sam", label: "Original SAM", color: "#d2a24c" },
  { key: "recovered", label: "Recovered SAM", color: "#e879f9" },
  { key: "canonical", label: "Final canonical", color: "#f5f5f5" },
];

export const OUTCOME_COLORS: Record<RecoveryOutcome, string> = {
  supported: "#e879f9",
  provisional: "#c4a5d8",
  conflicting: "#e07070",
  rejected: "#888888",
};

/** The same window the Inspect overlay uses, so a box stays up between its stored times. */
const NEAR_SEC = 0.45;

type Row = {
  object_local_id: string;
  timestamp: number;
  bbox: BBox | null;
  source_model?: string;
  observation_role?: string;
  geometry_origin?: string;
  geometry_authority?: string;
  association_status?: string;
  track_ref?: string;
};

function rowLayer(row: Row): RecoveryLayer {
  if (row.source_model === "sam-3.1") return "sam";
  if (row.source_model === "tracker") return "tracker";
  return "vlm";
}

function rowDetails(row: Row): string[] {
  return [
    `source ${row.source_model || row.geometry_authority || "model"}`,
    row.observation_role ? `role ${row.observation_role}` : "",
    row.geometry_origin ? `origin ${row.geometry_origin}` : "",
    row.association_status ? `association ${row.association_status}` : "",
    row.track_ref ? `track ${row.track_ref}` : "",
  ].filter(Boolean);
}

/** The nearest box per object per layer. A row with no nearby time draws nothing rather than holding its box. */
function nearest(boxes: LayerBox[], time: number): LayerBox[] {
  const best = new Map<string, LayerBox>();
  for (const box of boxes) {
    const gap = Math.abs(box.timestamp - time);
    if (gap > NEAR_SEC) continue;
    const key = `${box.layer}:${box.objectId}`;
    const current = best.get(key);
    if (!current || gap < Math.abs(current.timestamp - time)) best.set(key, box);
  }
  return [...best.values()];
}

/** Boxes for the chosen layers near one time. Original rows stay on their own layer, canonical or not. */
export function layerBoxes(
  bundle: Pick<Bundle, "observations" | "raw_observations" | "tracks">,
  proposals: RecoveryProposal[],
  time: number,
  layers: Record<RecoveryLayer, boolean>,
): LayerBox[] {
  const stored: Row[] = [...bundle.observations, ...(bundle.raw_observations?.rows ?? [])];
  const boxes: LayerBox[] = [];
  stored.forEach((row, index) => {
    if (!row.bbox) return;
    const layer = rowLayer(row);
    const conflict = row.association_status === "conflict";
    if (layers[layer]) {
      boxes.push({
        key: `${layer}-${index}`,
        layer,
        objectId: row.object_local_id,
        bbox: row.bbox,
        timestamp: row.timestamp,
        dashed: layer === "vlm" || conflict,
        details: rowDetails(row),
      });
    }
    if (layers.canonical && row.observation_role === "canonical") {
      boxes.push({
        key: `canonical-${index}`,
        layer: "canonical",
        objectId: row.object_local_id,
        bbox: row.bbox,
        timestamp: row.timestamp,
        dashed: false,
        details: rowDetails(row),
      });
    }
  });
  if (layers.tracker) {
    (bundle.tracks?.points ?? []).forEach((point, index) => {
      if (!point.bbox || point.detector_id === "sam-3.1") return;
      boxes.push({
        key: `track-${index}`,
        layer: "tracker",
        objectId: point.track_ref,
        bbox: point.bbox,
        timestamp: point.timestamp,
        dashed: point.detector_id === "optical_flow",
        details: [`track ${point.track_ref}`, `detector ${point.detector_id || "unknown"}`, point.status ? `status ${point.status}` : ""].filter(Boolean),
      });
    });
  }
  if (layers.recovered) {
    for (const proposal of proposals) {
      proposal.rows.forEach((row, index) => {
        boxes.push({
          key: `recovered-${proposal.id}-${index}`,
          layer: "recovered",
          objectId: proposal.object_local_id,
          bbox: row.bbox,
          timestamp: row.timestamp,
          dashed: row.outcome !== "supported",
          details: [
            `outcome ${row.outcome}`,
            `association ${row.association}`,
            `seed ${row.seed_trust}`,
            `direction ${row.direction}`,
            row.partial ? "partial, visible area only" : "",
            row.confidence != null ? `SAM score ${row.confidence.toFixed(2)}, uncalibrated` : "",
            row.context ? "context frame, outside the interval, never applied" : "",
          ].filter(Boolean),
        });
      });
    }
  }
  return nearest(boxes, time);
}

/** The moments worth stopping on: sampled frames, stored boxes, and recovered boxes inside their interval. */
export function stepTimes(bundle: Pick<Bundle, "frames" | "observations">, proposals: RecoveryProposal[]): number[] {
  const times = [
    ...bundle.frames.map((frame) => frame.timestamp),
    ...bundle.observations.filter((row) => row.bbox).map((row) => row.timestamp),
    ...proposals.flatMap((proposal) => proposal.rows.filter((row) => !row.context).map((row) => row.timestamp)),
  ].sort((a, b) => a - b);
  return times.filter((time, index) => index === 0 || time - times[index - 1] > 0.001);
}

export function nextStep(times: number[], now: number, direction: 1 | -1): number | null {
  if (direction > 0) return times.find((time) => time > now + 0.001) ?? null;
  for (let index = times.length - 1; index >= 0; index -= 1) {
    if (times[index] < now - 0.001) return times[index];
  }
  return null;
}

/** A press and a release on the picture, in normalized coordinates of the displayed frame. */
export function dragBox(startX: number, startY: number, endX: number, endY: number): BBox | null {
  const clamp = (value: number) => Math.min(Math.max(value, 0), 1);
  const x1 = clamp(Math.min(startX, endX));
  const y1 = clamp(Math.min(startY, endY));
  const x2 = clamp(Math.max(startX, endX));
  const y2 = clamp(Math.max(startY, endY));
  if (x2 - x1 < 0.005 || y2 - y1 < 0.005) return null;
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

/** The live button is offered only for a plan with something to send and no limit broken. */
export function canApproveLive(plan: RecoveryPlan | null): boolean {
  return Boolean(plan && plan.totals.remote_requests > 0 && plan.violations.length === 0);
}

export function usage(totals: RecoveryPlan["totals"]): string {
  const cost = totals.estimated_cost_usd == null ? "cost unknown" : `about $${totals.estimated_cost_usd.toFixed(4)}`;
  return `${totals.remote_requests} provider ${totals.remote_requests === 1 ? "request" : "requests"}, about ${totals.billed_frames} billed frames, ${cost}`;
}

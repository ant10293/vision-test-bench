import type { BBox, Observation } from "./types";

export interface UncertainEvent {
  event_id: string;
  object_local_id: string | null;
  timestamp: number | null;
  bbox: BBox | null;
  visibility?: string | null;
  decision: string;
  reason_code: string | null;
  association_score: number | null;
}

/** Model scores are stored from 0 to 1. The overlay shows them as a percent. */
export function formatConfidence(score: number): string {
  return `${Math.round(score * 100)}%`;
}

/** Same gate the locator uses when it refuses a box that covers another object. */
export const OVERLAP_REJECT_IOU = 0.55;
/** A weaker match is not shown as "most likely". */
export const MIN_LIKELY_SCORE = 0.15;
const REFERENCE_WINDOW_SEC = 2.5;
const SAME_FRAME_SEC = 0.05;

/** Rejections that mean "this is not a candidate for an object", so they get no likely-object label. */
export const HARD_REJECT_REASONS = new Set([
  "duplicate_overlap",
  "surface_mismatch",
  "extent_growth_exceeded",
  "extent_shrink_exceeded",
  "area_below_threshold",
  "proposal_budget_exceeded",
]);

export interface LikelyMatch {
  objectId: string;
  /** 0–1 match. The model's own score when this is the object it named and it supplied one. */
  score: number;
}

/**
 * The object an uncertain box most resembles, and how strong that resemblance is.
 * A box that overlaps another object's box enough to be an overlap rejection is not given a guess.
 */
export function likelyObjectForCandidate(observations: Observation[], candidate: Observation): LikelyMatch | null {
  if (!candidate.bbox || candidate.continuity !== "uncertain") return null;
  const box = candidate.bbox;
  const committed = observations.filter(
    (item) => item.bbox && item.continuity !== "uncertain" && item.visibility !== "absent",
  );
  const sameFrame = committed.filter(
    (item) =>
      item.object_local_id !== candidate.object_local_id &&
      Math.abs(item.timestamp - candidate.timestamp) <= SAME_FRAME_SEC,
  );
  if (sameFrame.some((item) => item.bbox && iou(box, item.bbox) >= OVERLAP_REJECT_IOU)) return null;

  const objectIds = [...new Set(committed.map((item) => item.object_local_id))];
  let best: LikelyMatch | null = null;
  for (const objectId of objectIds) {
    const reference = nearestCommitted(committed, objectId, candidate.timestamp);
    if (!reference?.bbox) continue;
    const geometric = resemblance(box, reference.bbox);
    if (!best || geometric > best.score) best = { objectId, score: geometric };
  }
  if (!best || best.score < MIN_LIKELY_SCORE) return null;
  if (best.objectId === candidate.object_local_id && candidate.confidence != null) {
    return { objectId: best.objectId, score: candidate.confidence };
  }
  return best;
}

export function likelyObjectForEvent(observations: Observation[], event: UncertainEvent): LikelyMatch | null {
  if (event.decision !== "uncertain" || (event.reason_code != null && HARD_REJECT_REASONS.has(event.reason_code))) return null;
  if (!event.bbox || event.timestamp == null || !event.object_local_id) return null;
  return likelyObjectForCandidate(observations, {
    id: event.event_id,
    object_local_id: event.object_local_id,
    timestamp: event.timestamp,
    bbox: event.bbox,
    visibility: event.visibility ?? "visible",
    description: "",
    evidence_class: "",
    provenance: "",
    evidence_note: "",
    continuity: "uncertain",
    confidence: event.association_score,
    coordinate_frame: "image",
    camera_compensated: false,
    frame_id: null,
  });
}

export function uncertainCandidatesAt(observations: Observation[], timestamp: number): { observation: Observation; match: LikelyMatch }[] {
  return observations
    .filter((item) => item.continuity === "uncertain" && Math.abs(item.timestamp - timestamp) <= SAME_FRAME_SEC)
    .flatMap((observation) => {
      const match = likelyObjectForCandidate(observations, observation);
      return match ? [{ observation, match }] : [];
    });
}

function nearestCommitted(rows: Observation[], objectId: string, timestamp: number): Observation | null {
  const own = rows.filter(
    (item) => item.object_local_id === objectId && Math.abs(item.timestamp - timestamp) <= REFERENCE_WINDOW_SEC,
  );
  own.sort((left, right) => Math.abs(left.timestamp - timestamp) - Math.abs(right.timestamp - timestamp));
  return own[0] ?? null;
}

function resemblance(candidate: BBox, reference: BBox): number {
  const candidateArea = candidate.w * candidate.h;
  const referenceArea = reference.w * reference.h;
  if (candidateArea <= 0 || referenceArea <= 0) return 0;
  const ratio = Math.min(candidateArea, referenceArea) / Math.max(candidateArea, referenceArea);
  if (ratio < 0.25) return 0;
  const distance = Math.hypot(
    candidate.x + candidate.w / 2 - (reference.x + reference.w / 2),
    candidate.y + candidate.h / 2 - (reference.y + reference.h / 2),
  );
  const scale = Math.max(Math.hypot(reference.w, reference.h), 0.02);
  return ratio * Math.exp(-distance / scale);
}

function iou(left: BBox, right: BBox): number {
  const width = Math.min(left.x + left.w, right.x + right.w) - Math.max(left.x, right.x);
  const height = Math.min(left.y + left.h, right.y + right.h) - Math.max(left.y, right.y);
  if (width <= 0 || height <= 0) return 0;
  const intersection = width * height;
  const union = left.w * left.h + right.w * right.h - intersection;
  return union <= 0 ? 0 : intersection / union;
}

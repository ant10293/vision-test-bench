import { describe, expect, it } from "vitest";
import { formatConfidence, likelyObjectForCandidate, likelyObjectForEvent, type UncertainEvent } from "../lib/likely";
import type { Observation } from "../lib/types";

function box(id: string, objectId: string, timestamp: number, x: number, y: number, w: number, h: number, continuity = "continuous"): Observation {
  return {
    id,
    object_local_id: objectId,
    timestamp,
    bbox: { x, y, w, h },
    visibility: "visible",
    description: "",
    evidence_class: "DIRECTLY_OBSERVED",
    provenance: "AI_PREDICTION",
    evidence_note: "",
    continuity,
    confidence: null,
    coordinate_frame: "image",
    camera_compensated: false,
    frame_id: null,
  };
}

describe("likely object for an uncertain candidate", () => {
  it("shows a model score as a percent", () => {
    expect(formatConfidence(0.42)).toBe("42%");
  });

  it("names the nearby object of a similar size and skips an overlap rejection", () => {
    const nearby = box("b", "Object_2", 1, 0.5, 0.5, 0.08, 0.08);
    const far = box("a", "Object_1", 0, 0.1, 0.1, 0.08, 0.08);
    const candidate = box("c", "Object_1", 1, 0.5, 0.62, 0.08, 0.08, "uncertain");
    const match = likelyObjectForCandidate([far, nearby, candidate], candidate);
    expect(match?.objectId).toBe("Object_2");
    expect(match && match.score).toBeGreaterThan(0.15);

    const overlapping = box("d", "Object_1", 1, 0.5, 0.5, 0.08, 0.08, "uncertain");
    expect(likelyObjectForCandidate([far, nearby, overlapping], overlapping)).toBeNull();
  });

  it("uses the model score when the named object is the best match", () => {
    const reference = box("a", "Object_1", 1, 0.2, 0.2, 0.1, 0.1);
    const candidate = box("c", "Object_1", 1.2, 0.21, 0.21, 0.1, 0.1, "uncertain");
    candidate.confidence = 0.42;
    expect(likelyObjectForCandidate([reference, candidate], candidate)).toEqual({ objectId: "Object_1", score: 0.42 });
  });

  it("does not guess an object for an overlap or extent rejection", () => {
    const reference = box("a", "Object_1", 1, 0.2, 0.2, 0.1, 0.1);
    const event = {
      event_id: "e",
      stage: "recovery_filtering",
      object_local_id: "Object_1",
      timestamp: 1.2,
      bbox: { x: 0.21, y: 0.21, w: 0.1, h: 0.1 },
      decision: "rejected",
      reason_code: "duplicate_overlap",
      association_score: 0.9,
      explanation: "",
    } as UncertainEvent;
    expect(likelyObjectForEvent([reference], event)).toBeNull();
    expect(likelyObjectForEvent([reference], { ...event, decision: "uncertain", reason_code: "extent_growth_exceeded" })).toBeNull();
  });
});

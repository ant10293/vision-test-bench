import { describe, expect, it } from "vitest";
import { observationsAt, frameNear } from "../lib/analysis";
import type { Bundle } from "../lib/types";

const bundle = {
  objects: [
    { local_id: "Object_1", color: "#ff0000", label: "cup" },
  ],
  observations: [
    { id: "a", object_local_id: "Object_1", timestamp: 1, bbox: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 }, visibility: "visible" },
    { id: "b", object_local_id: "Object_1", timestamp: 4, bbox: { x: 0.4, y: 0.1, w: 0.2, h: 0.2 }, visibility: "visible" },
  ],
  frames: [
    { id: "f1", timestamp: 1.1, reason: "uniform", url: "/a" },
    { id: "f2", timestamp: 3.8, reason: "uniform", url: "/b" },
  ],
} as unknown as Bundle;

describe("analysis helpers", () => {
  it("selects the observation nearest the playhead inside the window", () => {
    const hits = observationsAt(bundle, 1.2);
    expect(hits.map((item) => item.id)).toEqual(["a"]);
  });

  it("does not hold a box across a long gap", () => {
    expect(observationsAt(bundle, 2.5)).toEqual([]);
  });

  it("seeks the nearest extracted frame", () => {
    expect(frameNear(bundle, 3.5)?.id).toBe("f2");
  });
});

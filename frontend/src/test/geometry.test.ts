import { describe, expect, it } from "vitest";
import { boxGeometries, formatScale, formatTurn, trackGeometry, type GeometryBox } from "../lib/geometry";
import type { BBox, Bundle } from "../lib/types";

const LEFT: BBox = { x: 0.1, y: 0.1, w: 0.3, h: 0.3 };
const RIGHT: BBox = { x: 0.3, y: 0.1, w: 0.3, h: 0.3 };

function box(object_local_id: string, timestamp: number, bbox: BBox | null = LEFT): GeometryBox {
  return { id: `${object_local_id}@${timestamp}`, object_local_id, timestamp, bbox };
}

function point(track_ref: string, timestamp: number, relative_scale: number | null, angle_deg: number | null) {
  return { track_ref, timestamp, bbox: LEFT, status: "", continuity: "", association_score: null, detector_id: "", scene_chunk: 0, note: "", relative_scale, angle_deg };
}

const bundle = {
  objects: [],
  relationships: [
    { id: "r1", subject_local_id: "Object_3", other_local_id: "Object_1", kind: "in_front_of", time_start: 9, time_end: 9 },
  ],
  object_geometry: {
    status: "measured",
    boxes: [
      { object_local_id: "Object_1", timestamp: 1.002, relative_scale: 1.27, angle_deg: 23.6 },
      { object_local_id: "Object_1", timestamp: 2, relative_scale: null, angle_deg: -4.4 },
      { object_local_id: "Object_2", timestamp: 1.002, relative_scale: 0.8, angle_deg: null },
    ],
    orders: [{ nearer: "Object_2", farther: "Object_1", time_start: 1.002, time_end: 1.002 }],
  },
  track_depth_orders: [{ nearer: "track_5", farther: "track_4", time_start: 5, time_end: 5 }],
} as unknown as Bundle;

describe("box geometry label", () => {
  it("formats scale and a signed turn", () => {
    expect(formatScale(1.27)).toBe("1.3×");
    expect(formatTurn(23.6)).toBe("+24°");
    expect(formatTurn(-4.4)).toBe("-4°");
    expect(formatTurn(0.2)).toBe("0°");
  });

  it("reads the value measured on that object box", () => {
    const found = boxGeometries(bundle, [box("Object_1", 1.002), box("Object_1", 2), box("Object_1", 3)]);
    expect(found.get("Object_1@1.002")?.text).toBe("1.3× · +24°");
    expect(found.get("Object_1@2")?.text).toBe("-4°");
    expect(found.get("Object_1@3")).toEqual({ text: "", front: false });
  });

  it("marks front only for the nearer box while the farther box is visible", () => {
    const both = boxGeometries(bundle, [box("Object_1", 1.002), box("Object_2", 1.002, RIGHT)]);
    expect(both.get("Object_2@1.002")).toEqual({ text: "0.8×", front: true });
    expect(both.get("Object_1@1.002")?.front).toBe(false);
    expect(boxGeometries(bundle, [box("Object_2", 1.002, RIGHT)]).get("Object_2@1.002")?.front).toBe(false);
  });

  it("also reads an in_front_of record on the document", () => {
    const found = boxGeometries(bundle, [box("Object_3", 9), box("Object_1", 9, RIGHT)]);
    expect(found.get("Object_3@9")).toEqual({ text: "", front: true });
  });

  it("labels a tracker box from its own sample and marks front only against a track in view", () => {
    expect(trackGeometry(bundle, point("track_4", 5, 0.8, 31), new Set(["track_4", "track_5"]))).toEqual({ text: "0.8× · +31°", front: false });
    expect(trackGeometry(bundle, point("track_5", 5, 1.5, null), new Set(["track_4", "track_5"]))).toEqual({ text: "1.5×", front: true });
    expect(trackGeometry(bundle, point("track_5", 5, 1.5, null), new Set(["track_5"])).front).toBe(false);
  });
});

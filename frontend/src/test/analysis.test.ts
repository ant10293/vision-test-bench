import { describe, expect, it } from "vitest";
import { frameObjectStills, observationsAt, frameNear, tracksAt } from "../lib/analysis";
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

  it("lists a crop and label for each object in the frame", () => {
    const scene = {
      objects: [
        {
          local_id: "Object_1",
          label: "rod",
          color: "#7EB6FF",
          primary_image_url: "/rod.png",
          images: [{ id: "img-1", kind: "initial", timestamp: 1, url: "/rod.png", score: 1 }],
        },
        { local_id: "Object_2", label: "nut", color: "#e6b450", primary_image_url: "/nut.png", images: [] },
      ],
      observations: [
        { id: "near", object_local_id: "Object_1", timestamp: 1, bbox: { x: 0, y: 0, w: 0.2, h: 0.2 }, frame_id: "f1", visibility: "visible" },
        { id: "later", object_local_id: "Object_2", timestamp: 5, bbox: { x: 0, y: 0, w: 0.2, h: 0.2 }, frame_id: "f2", visibility: "visible" },
      ],
      frames: [],
    } as unknown as Bundle;
    expect(frameObjectStills(scene, 1.1)).toEqual([
      { id: "near", url: "/api/media/observations/near/crop", label: "Rod", color: "#7EB6FF", confidence: null },
    ]);
  });

  it("falls back to the object's still when the observation has no frame", () => {
    expect(frameObjectStills(bundle, 1).map((item) => item.url)).toEqual([]);
    const withStill = {
      ...bundle,
      objects: [{ ...bundle.objects[0], primary_image_url: "/cup.png", images: [] }],
    } as unknown as Bundle;
    expect(frameObjectStills(withStill, 1)).toEqual([
      { id: "a", url: "/cup.png", label: "Cup", color: "#ff0000", confidence: null },
    ]);
  });

  it("keeps one track sample nearest the playhead", () => {
    const hits = tracksAt([
      { track_ref: "track_1", timestamp: 1, bbox: { x: 0, y: 0, w: 0.1, h: 0.1 }, status: "tracked", continuity: "continuous", association_score: 0.8, detector_id: "proposals", scene_chunk: 0, note: "" },
      { track_ref: "track_1", timestamp: 1.1, bbox: { x: 0.1, y: 0, w: 0.1, h: 0.1 }, status: "tracked", continuity: "continuous", association_score: 0.8, detector_id: "proposals", scene_chunk: 0, note: "" },
      { track_ref: "track_2", timestamp: 4, bbox: { x: 0.4, y: 0, w: 0.1, h: 0.1 }, status: "lost", continuity: "uncertain", association_score: null, detector_id: "proposals", scene_chunk: 0, note: "" },
    ], 1.08);
    expect(hits.map((item) => item.track_ref)).toEqual(["track_1"]);
    expect(hits[0]?.timestamp).toBe(1.1);
  });
});

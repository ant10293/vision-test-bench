import { describe, expect, it } from "vitest";
import { photoUV, toThree, verticalFov } from "../lib/sceneCoords";

describe("scene display coordinates", () => {
  it("maps Blender Z-up to three.js Y-up", () => {
    expect(toThree([1, 2, 3])).toEqual([1, 3, -2]);
  });

  it("maps a box face onto the photograph the same way the render does", () => {
    expect(photoUV(-1, -0.5, 2, 1)).toEqual([0, 0]);
    expect(photoUV(1, 0.5, 2, 1)).toEqual([1, 1]);
    expect(photoUV(0, 0, 2, 1)).toEqual([0.5, 0.5]);
  });

  it("keeps a square image's vertical field of view equal to the horizontal one", () => {
    expect(verticalFov(100, 100, 60)).toBeCloseTo(60, 5);
  });
});

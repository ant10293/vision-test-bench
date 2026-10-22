import { describe, expect, it } from "vitest";
import { analysisPercent, uploadPercent } from "../lib/uploadProgress";

describe("asset row progress", () => {
  it("fills the upload step from 0 to 100", () => {
    expect(uploadPercent(0)).toBe(0);
    expect(uploadPercent(0.5)).toBe(50);
    expect(uploadPercent(1)).toBe(100);
  });

  it("resets and fills the analysis step from 0 to 100", () => {
    expect(analysisPercent(0)).toBe(0);
    expect(analysisPercent(50)).toBe(50);
    expect(analysisPercent(100)).toBe(100);
  });
});

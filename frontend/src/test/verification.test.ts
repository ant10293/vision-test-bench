import { describe, expect, it } from "vitest";
import { markerColor, markerKind, verdictClass } from "../lib/verification";

describe("verification display", () => {
  it("keeps pass, fail, and unverified visually distinct", () => {
    expect(verdictClass("PASS")).toBe("observed");
    expect(verdictClass("FAIL")).toBe("unknown");
    expect(verdictClass("CANNOT_VERIFY")).toBe("inferred");
    expect(verdictClass("INCONCLUSIVE")).toBe("inferred");
  });

  it("marks a corrected pass separately from a failure", () => {
    expect(markerKind({ verdict: "PASS", execution_state: "corrected" })).toBe("corrected");
    expect(markerKind({ verdict: "FAIL", error_category: "MISSING_COMPONENT" })).toBe("failure");
    expect(markerKind({ verdict: "CANNOT_VERIFY" })).toBe("uncertain");
    expect(markerKind({ verdict: "PASS" })).toBe("verified");
    expect(markerColor("failure")).not.toBe(markerColor("uncertain"));
  });
});

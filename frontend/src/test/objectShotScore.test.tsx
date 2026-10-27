import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BundleContext } from "../lib/bundleContext";
import type { Bundle } from "../lib/types";
import { ObjectsPage } from "../pages/RunPages";

const bundle = {
  run: { id: "run-1" },
  objects: [
    {
      id: "obj-1",
      local_id: "Object_1",
      label: "rod",
      description: "",
      initial_state: "",
      final_state: "",
      uncertain_identity: false,
      observation_count: 2,
      color: "#fff",
      primary_image_url: "/thumb.png",
      images: [{ id: "img-1", kind: "initial", timestamp: 0, url: "/thumb.png", score: 1 }],
    },
  ],
  observations: [
    {
      id: "obs-1",
      object_local_id: "Object_1",
      timestamp: 0,
      bbox: { x: 0, y: 0, w: 1, h: 1 },
      frame_id: "f1",
      visibility: "visible",
      confidence: 0.42,
    },
    {
      id: "obs-2",
      object_local_id: "Object_1",
      timestamp: 2,
      bbox: { x: 0, y: 0, w: 1, h: 1 },
      frame_id: "f2",
      visibility: "visible",
      confidence: null,
    },
  ],
} as unknown as Bundle;

describe("confidence on the object image grid", () => {
  it("overlays the percent on the top right and skips a still with no score", () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <BundleContext.Provider value={bundle}>
          <ObjectsPage />
        </BundleContext.Provider>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /Rod/ }));

    const score = screen.getByText("42%");
    expect(score).toHaveClass("shot-score");
    expect(score.parentElement).toHaveClass("shot-frame");
    expect(score.previousElementSibling?.tagName).toBe("IMG");
    expect(screen.getByRole("button", { name: "View Initial · 0.00s, 42% confidence" })).toBeInTheDocument();

    const quiet = screen.getByRole("button", { name: "View 2.00s" });
    expect(quiet.querySelector(".shot-score")).toBeNull();
  });
});

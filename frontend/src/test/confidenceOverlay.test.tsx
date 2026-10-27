import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VideoStage } from "../components/VideoStage";
import type { Bundle } from "../lib/types";
import { useInspector } from "../stores/ui";

const bundle = {
  run: { id: "run-1" },
  video: null,
  frames: [],
  objects: [{ local_id: "Object_1", label: "rod", color: "#7EB6FF" }],
  observations: [
    {
      id: "obs-1",
      object_local_id: "Object_1",
      timestamp: 1,
      bbox: { x: 0.2, y: 0.2, w: 0.1, h: 0.1 },
      visibility: "visible",
      continuity: "continuous",
      confidence: 0.42,
    },
  ],
  tracks: { points: [], truncated: false, total: 0 },
} as unknown as Bundle;

function stage() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <VideoStage bundle={bundle} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  HTMLMediaElement.prototype.pause = () => {};
  Object.defineProperty(document, "fonts", {
    configurable: true,
    value: { load: () => Promise.resolve([]) },
  });
  useInspector.setState({
    time: 1,
    playing: false,
    overlay: true,
    layers: { tracks: false, trajectory: false, uncertain: false, confidence: true },
  });
});

afterEach(() => {
  useInspector.setState({ time: 0, playing: false, overlay: true });
});

describe("confidence on the video overlay", () => {
  it("draws the model score as a percent and hides it without changing the box", () => {
    const view = stage();
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(view.container.querySelector("rect")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Hide confidence" }));

    expect(screen.queryByText("42%")).not.toBeInTheDocument();
    expect(view.container.querySelector("rect")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Show confidence" })).toBeInTheDocument();
  });
});

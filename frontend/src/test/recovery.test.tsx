import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BundleContext } from "../lib/bundleContext";
import { canApproveLive, dragBox, layerBoxes, nextStep, stepTimes, usage, type RecoveryPlan, type RecoveryProposal } from "../lib/recovery";
import { api } from "../lib/api";
import { RecoveryPage } from "../pages/Recovery";
import { useInspector } from "../stores/ui";
import type { Bundle } from "../lib/types";

const box = { x: 0.1, y: 0.1, w: 0.2, h: 0.2 };

const row = (extra: Record<string, unknown>) => ({ object_local_id: "Object_1", timestamp: 1, bbox: box, ...extra });

const bundle = {
  run: { id: "run-1" },
  video: { playback_url: "", duration_sec: 10 },
  frames: [{ id: "f1", timestamp: 0.5, reason: "uniform", scene_change: false }, { id: "f2", timestamp: 1, reason: "uniform", scene_change: false }],
  objects: [],
  actions: [],
  transitions: [],
  ambiguities: [],
  steps: [],
  observations: [row({ geometry_origin: "vlm_estimated" }), row({ source_model: "tracker", observation_role: "canonical" })],
  raw_observations: { total: 1, truncated: false, rows: [row({ source_model: "sam-3.1", association_status: "conflict" })] },
  tracks: { total: 1, truncated: false, points: [{ track_ref: "track_9", timestamp: 1, bbox: box, detector_id: "motion", status: "active" }] },
} as unknown as Bundle;

const proposal = {
  id: "p1",
  current: true,
  object_local_id: "Object_1",
  rows: [
    { timestamp: 1, bbox: box, outcome: "provisional", association: "associated", direction: "forward", seed_trust: "human_verified", partial: false, context: false, confidence: 0.9, checks: [], frame_index: 0 },
  ],
} as unknown as RecoveryProposal;

const none = { vlm: false, tracker: false, sam: false, recovered: false, canonical: false };

describe("recovery layers", () => {
  it("draws only the selected layer", () => {
    expect(layerBoxes(bundle, [], 1, none)).toEqual([]);
    const vlm = layerBoxes(bundle, [], 1, { ...none, vlm: true });
    expect(vlm.map((item) => item.layer)).toEqual(["vlm"]);
  });

  it("compares original and recovered boxes on the same frame", () => {
    const shown = layerBoxes(bundle, [proposal], 1, { ...none, vlm: true, recovered: true });
    expect(shown.map((item) => item.layer).sort()).toEqual(["recovered", "vlm"]);
  });

  it("never draws an estimate, conflict, or unconfirmed box as solid", () => {
    expect(layerBoxes(bundle, [], 1, { ...none, vlm: true })[0].dashed).toBe(true);
    expect(layerBoxes(bundle, [], 1, { ...none, sam: true })[0].dashed).toBe(true);
    expect(layerBoxes(bundle, [proposal], 1, { ...none, recovered: true })[0].dashed).toBe(true);
  });

  it("shows a canonical layer and keeps the row on its source layer", () => {
    const shown = layerBoxes(bundle, [], 1, { ...none, canonical: true, tracker: true });
    expect(shown.map((item) => item.layer).sort()).toEqual(["canonical", "tracker", "tracker"]);
  });

  it("keeps a box up between stored times, as Inspect does", () => {
    expect(layerBoxes(bundle, [], 1.4, { ...none, vlm: true })).toHaveLength(1);
  });

  it("steps through frames, stored boxes, and recovered boxes inside the interval", () => {
    const later = { ...proposal, rows: [{ ...proposal.rows[0], timestamp: 2 }, { ...proposal.rows[0], timestamp: 3, context: true }] } as RecoveryProposal;
    const times = stepTimes(bundle, [later]);
    expect(times).toEqual([0.5, 1, 2]);
    expect(nextStep(times, 1, 1)).toBe(2);
    expect(nextStep(times, 1, -1)).toBe(0.5);
    expect(nextStep(times, 2, 1)).toBeNull();
  });

  it("does not hold a box past its time", () => {
    expect(layerBoxes(bundle, [proposal], 5, { vlm: true, tracker: true, sam: true, recovered: true, canonical: true })).toEqual([]);
  });

  it("exposes provenance on a recovered box and labels the score uncalibrated", () => {
    const details = layerBoxes(bundle, [proposal], 1, { ...none, recovered: true })[0].details.join(" ");
    expect(details).toContain("outcome provisional");
    expect(details).toContain("uncalibrated");
  });
});

describe("seed drawing and approval", () => {
  it("normalizes a drag in any direction and clamps it to the frame", () => {
    expect(dragBox(0.5, 0.5, 0.2, 0.1)).toEqual({ x: 0.2, y: 0.1, w: 0.3, h: 0.4 });
    expect(dragBox(0.9, 0.9, 1.4, 1.2)?.w).toBeCloseTo(0.1);
    expect(dragBox(0.5, 0.5, 0.5, 0.5)).toBeNull();
  });

  const plan = (remote: number, violations: string[] = []) => ({
    plan_hash: "h",
    requests: [],
    blocked: [],
    notes: [],
    violations,
    totals: { requests: remote, remote_requests: remote, cached_requests: 0, frames: 10, billed_frames: 50, estimated_cost_usd: null, cost_note: "" },
  }) as RecoveryPlan;

  it("offers a live run only for a plan with requests and no broken limit", () => {
    expect(canApproveLive(null)).toBe(false);
    expect(canApproveLive(plan(0))).toBe(false);
    expect(canApproveLive(plan(1, ["over budget"]))).toBe(false);
    expect(canApproveLive(plan(1))).toBe(true);
  });

  it("says when the cost is unknown", () => {
    expect(usage(plan(1).totals)).toContain("cost unknown");
  });
});

describe("recovery page", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function show() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <BundleContext.Provider value={bundle}>
          <RecoveryPage />
        </BundleContext.Provider>
      </QueryClientProvider>,
    );
  }

  const target = {
    target_id: "t1",
    object_local_id: "Object_1",
    instance_id: null,
    label: "washer",
    time_start: 2,
    time_end: 4,
    source: "automatic",
    state: "unresolved",
    reasons: ["tracking_gap"],
    details: ["No reliable box in this stretch."],
    seeds: [],
    difficulty: "easy",
    recoverable: true,
    blocker: "",
  };

  const planned = {
    id: "experiment-1",
    run_id: "run-1",
    status: "planned",
    mode: "dry_run",
    generation: 0,
    targets: [target],
    limits: {},
    error: "",
    remote_submitted: false,
    recorded_cost_usd: null,
    applied_revision_id: null,
    cancel_requested: false,
    requests: [],
    proposals: [],
    plan: {
      plan_hash: "hash-1",
      requests: [],
      blocked: [],
      notes: [],
      violations: [],
      totals: { requests: 1, remote_requests: 1, cached_requests: 0, frames: 30, billed_frames: 50, estimated_cost_usd: null, cost_note: "Cost unknown." },
    },
  };

  it("shows a still frame with no playback controls and steps with the arrows", async () => {
    vi.spyOn(api, "recoveryCandidates").mockResolvedValue({ run_id: "run-1", revision_id: "r", targets: [], seeds: [], seed_counts: {}, objects: [], price_per_frame_usd: null } as never);
    vi.spyOn(api, "recoveryExperiments").mockResolvedValue([]);
    useInspector.getState().setTime(0);
    const { container } = show();
    expect(container.querySelector("video")?.hasAttribute("controls")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Next frame" }));
    expect(useInspector.getState().time).toBe(0.5);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(useInspector.getState().time).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Previous frame" }));
    expect(useInspector.getState().time).toBe(0.5);
    await screen.findByText(/No interval was flagged/);
  });

  it("keeps the box list in one fixed-height panel whether or not it has boxes", async () => {
    vi.spyOn(api, "recoveryCandidates").mockResolvedValue({ run_id: "run-1", revision_id: "r", targets: [], seeds: [], seed_counts: {}, objects: [], price_per_frame_usd: null } as never);
    vi.spyOn(api, "recoveryExperiments").mockResolvedValue([]);
    useInspector.getState().setTime(1);
    show();
    const panel = screen.getByTestId("box-inspector");
    expect(panel).toHaveClass("h-40", "overflow-auto");
    expect(panel.querySelector("li")).not.toBeNull();
    act(() => useInspector.getState().setTime(8));
    expect(screen.getByTestId("box-inspector")).toBe(panel);
    expect(panel.querySelector("li")).toBeNull();
    await screen.findByText(/No interval was flagged/);
  });

  it("needs a dry run and an explicit approval before a live run", async () => {
    vi.spyOn(api, "recoveryCandidates").mockResolvedValue({ run_id: "run-1", revision_id: "r", targets: [target], seeds: [], seed_counts: {}, objects: [], price_per_frame_usd: null } as never);
    vi.spyOn(api, "recoveryExperiments").mockResolvedValue([]);
    vi.spyOn(api, "recoveryExperiment").mockResolvedValue(planned as never);
    const create = vi.spyOn(api, "createRecovery").mockResolvedValue(planned as never);
    const dry = vi.spyOn(api, "recoveryDryRun").mockResolvedValue(planned as never);
    const live = vi.spyOn(api, "recoveryLive").mockResolvedValue(planned as never);
    show();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Object 1/i }));
    fireEvent.click(screen.getByRole("button", { name: "Create and dry run" }));
    await waitFor(() => expect(dry).toHaveBeenCalledWith("experiment-1"));
    expect(create.mock.calls[0][1]).toMatchObject({ target_ids: ["t1"] });
    const run = await screen.findByRole("button", { name: "Run live SAM recovery" });
    expect(run).toBeDisabled();
    expect(live).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: /I approve/ }));
    fireEvent.click(run);
    await waitFor(() => expect(live).toHaveBeenCalledWith("experiment-1", { confirm: true, plan_hash: "hash-1" }));
  });

  it("shows why a request failed and whether anything was submitted", async () => {
    vi.spyOn(api, "recoveryCandidates").mockResolvedValue({ run_id: "run-1", revision_id: "r", targets: [target], seeds: [], seed_counts: {}, objects: [], price_per_frame_usd: null } as never);
    vi.spyOn(api, "recoveryExperiments").mockResolvedValue([]);
    const failed = { ...planned, status: "failed", error: "Upload failed.", remote_submitted: false };
    vi.spyOn(api, "createRecovery").mockResolvedValue(failed as never);
    vi.spyOn(api, "recoveryDryRun").mockResolvedValue(failed as never);
    vi.spyOn(api, "recoveryExperiment").mockResolvedValue(failed as never);
    show();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Object 1/i }));
    fireEvent.click(screen.getByRole("button", { name: "Create and dry run" }));
    expect(await screen.findByText(/No remote call was submitted/)).toBeInTheDocument();
  });

  it("marks a result built on a synthetic response", async () => {
    vi.spyOn(api, "recoveryCandidates").mockResolvedValue({ run_id: "run-1", revision_id: "r", targets: [target], seeds: [], seed_counts: {}, objects: [], price_per_frame_usd: null } as never);
    vi.spyOn(api, "recoveryExperiments").mockResolvedValue([]);
    const metrics = {
      counts: { supported: 1, provisional: 0, conflicting: 0, rejected: 0, unobserved_frames: 2 },
      previously_missing_frames: 1,
      new_supported_frames: 1,
      original_rows_in_interval: 0,
      remaining_gaps: [{ start: 3, end: 4, frames: 2 }],
      identity: [],
      usage: { requests: 1, billed_frames: 50, latency_ms: 0, input_tokens: 0, output_tokens: 0 },
      synthetic_response: true,
      human_box_overlap: null,
      accuracy_note: "No human-labeled box covers this interval, so accuracy was not measured.",
    };
    const done = {
      ...planned,
      status: "completed",
      proposals: [{ id: "p", generation: 1, current: true, target_id: "t1", object_local_id: "Object_1", time_start: 2, time_end: 4, decision: "pending", rows: [], metrics, applied_revision_id: null }],
    };
    vi.spyOn(api, "createRecovery").mockResolvedValue(done as never);
    vi.spyOn(api, "recoveryDryRun").mockResolvedValue(done as never);
    vi.spyOn(api, "recoveryExperiment").mockResolvedValue(done as never);
    show();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Object 1/i }));
    fireEvent.click(screen.getByRole("button", { name: "Create and dry run" }));
    expect(await screen.findByText(/synthetic offline response/)).toBeInTheDocument();
    expect(screen.getByText(/accuracy was not measured/)).toBeInTheDocument();
    expect(screen.getByText(/not evidence of absence/)).toBeInTheDocument();
  });
});

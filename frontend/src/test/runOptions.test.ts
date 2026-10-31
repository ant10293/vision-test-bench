import { describe, expect, it } from "vitest";
import { analysisSetupLine } from "../lib/runOptions";

describe("analysis setup line", () => {
  it("names the segmentation, perception, prompt family, and pipeline options", () => {
    expect(analysisSetupLine({
      run: {
        provider_id: "openai",
        model_id: "gpt-6-astra",
        modality: "sampled_frames",
        sampling_preset: "standard",
        segmentation_mode: "sam_video",
        analysis_pass: "single",
        cost_usd: 0,
      },
      revision: { layer: "model", version: 2 },
      config: {
        perception: { preset: "C_appearance" },
        prompt_name: "General scene analysis and object identification",
      },
    })).toBe(
      "OpenAI / GPT 6 astra · Sampled frames · Standard · SAM 3.1 video tracking with evidence fusion · C · Appearance-assisted · General scene analysis and object identification · Single pass · Model v2 · $0.0000",
    );
  });

  it("uses the stored prompt family when the run has no separate name", () => {
    expect(analysisSetupLine({
      run: {
        provider_id: "openai",
        model_id: "gpt-6-astra",
        modality: "sampled_frames",
        sampling_preset: "standard",
        segmentation_mode: "boxes_only",
        analysis_pass: "multi_reinspect",
        cost_usd: 0,
        unpriced_calls: 2,
      },
      config: {
        perception: { strategy: "temporal" },
        prompt_key: "scene",
      },
    })).toBe(
      "OpenAI / GPT 6 astra · Sampled frames · Standard · Boxes and crops only · B · Temporal tracking · General scene analysis and object identification · Multi-stage + reinspection · $0.0000 · 2 unpriced",
    );
  });
});
import { formatUsd } from "./api";
import { humanize } from "./humanize";

/** Labels match the experiment form, so a finished run names the option that was chosen. */
export const SEGMENTATION_OPTIONS = [
  ["disabled", "Disabled"],
  ["boxes_only", "Boxes and crops only"],
  ["local_grabcut", "Local GrabCut"],
  ["sam", "SAM 3.1 image segmentation"],
  ["sam_video", "SAM 3.1 video tracking with evidence fusion"],
] as const;

export const PERCEPTION_OPTIONS = [
  ["A_legacy_motion", "A · Motion baseline"],
  ["B_temporal", "B · Temporal tracking"],
  ["C_appearance", "C · Appearance-assisted"],
  ["D_appearance_segmented", "D · Appearance and GrabCut"],
  ["vlm_only", "VLM only"],
] as const;

export const PIPELINE_OPTIONS = [
  ["single", "Single pass"],
  ["multi", "Multi-stage"],
  ["multi_reinspect", "Multi-stage + reinspection"],
] as const;

const STRATEGY_PRESET: Record<string, string> = {
  legacy_motion: "A_legacy_motion",
  temporal: "B_temporal",
  appearance: "C_appearance",
  appearance_segmented: "D_appearance_segmented",
  vlm_only: "vlm_only",
};

/** Names of the built-in families. A run-specific name, when stored, wins over these. */
const PROMPT_FAMILIES: Record<string, string> = {
  foundational: "Foundational evidence rules",
  scene: "General scene analysis and object identification",
  actions: "Temporal action recognition",
  states: "Object state extraction",
  workflow: "Workflow reconstruction",
  ambiguity: "Ambiguity analysis",
  reinspect: "Targeted interval reinspection",
  comparison: "Cross-run comparison",
};

type OptionList = readonly (readonly [string, string])[];

export function optionLabel(options: OptionList, value: string | null | undefined): string {
  if (!value) return "";
  return options.find(([id]) => id === value)?.[1] ?? humanize(value);
}

function perceptionPreset(config: Record<string, unknown> | null | undefined): string {
  const perception = config?.perception;
  if (!perception || typeof perception !== "object") return "";
  const record = perception as { preset?: unknown; strategy?: unknown };
  if (typeof record.preset === "string" && record.preset) return record.preset;
  if (typeof record.strategy === "string" && record.strategy) {
    return STRATEGY_PRESET[record.strategy] ?? record.strategy;
  }
  return "";
}

function promptFamily(config: Record<string, unknown> | null | undefined): string {
  if (!config) return "";
  const name = config.prompt_name;
  if (typeof name === "string" && name.trim()) return name.trim();
  const key = config.prompt_key;
  if (typeof key !== "string" || !key) return "";
  return PROMPT_FAMILIES[key] ?? humanize(key);
}

/** Model, sampling, and the experiment options recorded on the run. */
export function analysisSetupLine(bundle: {
  run: {
    provider_id: string;
    model_id: string;
    modality: string;
    sampling_preset: string;
    segmentation_mode?: string;
    analysis_pass?: string;
    cost_usd: number;
    unpriced_calls?: number;
  };
  revision?: { layer: string; version: number } | null;
  config?: Record<string, unknown> | null;
}): string {
  const parts = [
    `${humanize(bundle.run.provider_id)} / ${humanize(bundle.run.model_id)}`,
    humanize(bundle.run.modality),
    humanize(bundle.run.sampling_preset),
    optionLabel(SEGMENTATION_OPTIONS, bundle.run.segmentation_mode),
    optionLabel(PERCEPTION_OPTIONS, perceptionPreset(bundle.config)),
    promptFamily(bundle.config),
    optionLabel(PIPELINE_OPTIONS, bundle.run.analysis_pass),
  ].filter(Boolean);
  if (bundle.revision) parts.push(`${humanize(bundle.revision.layer)} v${bundle.revision.version}`);
  parts.push(formatUsd(bundle.run.cost_usd));
  if ((bundle.run.unpriced_calls ?? 0) > 0) parts.push(`${bundle.run.unpriced_calls} unpriced`);
  return parts.join(" · ");
}

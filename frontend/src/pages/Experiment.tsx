import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import type { Capability } from "../lib/types";

const MODES = [
  ["fixed_camera", "Fixed camera"],
  ["constrained_head_mounted", "Constrained head-mounted"],
  ["unconstrained_head_mounted", "Unconstrained head-mounted"],
  ["auto", "Auto-characterize"],
] as const;

export function ExperimentPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const videos = useQuery({ queryKey: ["videos"], queryFn: () => api.videos() });
  const providers = useQuery({ queryKey: ["providers"], queryFn: api.providers });
  const prompts = useQuery({ queryKey: ["prompts"], queryFn: api.prompts });
  const [videoId, setVideoId] = useState(params.get("video") ?? "");
  const [providerId, setProviderId] = useState("fixture");
  const [modelId, setModelId] = useState("authored-fixture");
  const [mode, setMode] = useState("fixed_camera");
  const [modality, setModality] = useState("sampled_frames");
  const [preset, setPreset] = useState("standard");
  const [pass, setPass] = useState("single");
  const [segmentation, setSegmentation] = useState("boxes_only");
  const [temperature, setTemperature] = useState("");
  const [reasoning, setReasoning] = useState("");
  const [error, setError] = useState("");
  const models = useMemo(() => {
    const provider = providers.data?.find((item) => item.id === providerId);
    return provider?.models ?? [];
  }, [providers.data, providerId]);
  const selected: Capability | undefined = models.find((item) => item.model_id === modelId) ?? models[0];

  const create = useMutation({
    mutationFn: () =>
      api.createRun({
        name: `${mode} · ${modelId}`,
        video_id: videoId,
        provider_id: providerId,
        model_id: modelId,
        research_mode: mode,
        modality,
        sampling_preset: preset,
        analysis_pass: pass,
        segmentation_mode: segmentation,
        temperature: temperature === "" ? null : Number(temperature),
        reasoning_effort: reasoning || null,
        prompt_key: "scene",
        max_frames: 48,
      }),
    onSuccess: (run) => navigate(`/runs/${run.id}/pipeline`),
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-2xl font-medium">Experiment configuration</h1>
      <p className="text-sm text-[var(--muted)]">
        Unsupported settings stay disabled. The lab will not swap your model or modality.
      </p>
      <Field label="Video">
        <select className="field" value={videoId} onChange={(event) => setVideoId(event.target.value)}>
          <option value="">Choose a video</option>
          {videos.data?.map((video) => <option key={video.id} value={video.id}>{video.filename}</option>)}
        </select>
      </Field>
      <Field label="Research mode">
        <select className="field" value={mode} onChange={(event) => setMode(event.target.value)}>
          {MODES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </Field>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Provider">
          <select className="field" value={providerId} onChange={(event) => {
            const next = event.target.value;
            setProviderId(next);
            const first = providers.data?.find((item) => item.id === next)?.models[0];
            if (first) setModelId(first.model_id);
          }}>
            {providers.data?.map((provider) => <option key={provider.id} value={provider.id}>{provider.id}</option>)}
          </select>
        </Field>
        <Field label="Model">
          <select className="field" value={modelId} onChange={(event) => setModelId(event.target.value)}>
            {models.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_id}</option>)}
          </select>
        </Field>
      </div>
      {selected && (
        <div className="panel space-y-1 p-3 text-xs text-[var(--muted)]">
          <div>Image {yes(selected.image)} · Native video {yes(selected.native_video)} · Structured JSON {yes(selected.structured_output)} · Temperature {yes(selected.supports_temperature)}</div>
          <div>Max images per call {selected.max_images}. Reasoning: {selected.reasoning_efforts.join(", ") || "not offered"}.</div>
          <div>{selected.notes}</div>
        </div>
      )}
      <Field label="Input modality">
        <select className="field" value={modality} onChange={(event) => setModality(event.target.value)}>
          <option value="sampled_frames">Sampled frames</option>
          <option value="native_video" disabled={!selected?.native_video}>Native video {selected?.native_video ? "" : "(unsupported for this model)"}</option>
        </select>
      </Field>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Sampling">
          <select className="field" value={preset} onChange={(event) => setPreset(event.target.value)}>
            {["sparse", "standard", "detailed", "high_detail", "intensive", "adaptive"].map((item) => <option key={item}>{item}</option>)}
          </select>
        </Field>
        <Field label="Pipeline">
          <select className="field" value={pass} onChange={(event) => setPass(event.target.value)}>
            <option value="single">Single pass</option>
            <option value="multi">Multi-stage</option>
            <option value="multi_reinspect">Multi-stage + reinspection</option>
          </select>
        </Field>
        <Field label="Segmentation">
          <select className="field" value={segmentation} onChange={(event) => setSegmentation(event.target.value)}>
            <option value="disabled">Disabled</option>
            <option value="boxes_only">Boxes and crops</option>
            <option value="local_grabcut">Local GrabCut</option>
            <option value="sam">SAM 3.1 (requires Meta key)</option>
          </select>
        </Field>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Temperature">
          <input className="field" disabled={!selected?.supports_temperature} value={temperature} placeholder={selected?.supports_temperature ? "model default" : "unsupported"} onChange={(event) => setTemperature(event.target.value)} />
        </Field>
        <Field label="Reasoning effort">
          <select className="field" value={reasoning} disabled={!selected?.reasoning_efforts.length} onChange={(event) => setReasoning(event.target.value)}>
            <option value="">default</option>
            {selected?.reasoning_efforts.map((effort) => <option key={effort}>{effort}</option>)}
          </select>
        </Field>
      </div>
      <Field label="Prompt family">
        <select className="field" defaultValue="scene">
          {prompts.data?.filter((item, index, all) => all.findIndex((other) => other.key === item.key) === index).map((item) => (
            <option key={item.key} value={item.key}>{item.name}</option>
          ))}
        </select>
      </Field>
      {error && <p className="text-sm text-[var(--rose)]">{error}</p>}
      <button className="btn primary" disabled={!videoId || create.isPending} onClick={() => create.mutate()}>
        {create.isPending ? "Starting…" : "Start analysis"}
      </button>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-[var(--muted)]">{label}</span>
      {children}
    </label>
  );
}

function yes(value: boolean) {
  return value ? "yes" : "no";
}

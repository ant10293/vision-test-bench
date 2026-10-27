import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { SceneViewer } from "../components/SceneViewer";
import { api } from "../lib/api";
import { humanize, humanizeText, labeledId } from "../lib/humanize";
import { verticalFov } from "../lib/sceneCoords";
import type { SimulationSummary } from "../lib/simulation";

const PRESETS = ["A_legacy_motion", "B_temporal", "C_appearance", "D_appearance_segmented"] as const;

function jobText(item: SimulationSummary): string {
  const job = item.job;
  if (!job) return humanize(item.status);
  if (job.frame_total > 0) return `${humanize(job.stage || job.status)}: frame ${job.frame_current} of ${job.frame_total}`;
  return job.stage ? `${humanize(job.status)} · ${humanize(job.stage)}` : humanize(job.status);
}

function scoreText(value: { evaluated?: boolean; status?: string; reason?: string } | undefined): string {
  if (!value || value.evaluated === false) return humanizeText(value?.status || value?.reason || "NOT_EVALUATED");
  return "";
}

function objectName(id: string, label?: string): string {
  if (!label || humanize(label) === humanize(id)) return humanize(id);
  return labeledId(id, label);
}

export function SceneLabPage() {
  const queryClient = useQueryClient();
  const datasets = useQuery({
    queryKey: ["simulations"],
    queryFn: api.simulations,
    refetchInterval: (query) => (query.state.data?.some((item) => item.status === "pending" || item.status === "running") ? 1000 : false),
  });
  const [selectedId, setSelectedId] = useState("");
  const [objectId, setObjectId] = useState("");
  const [frame, setFrame] = useState(0);
  const [view, setView] = useState<"orbit" | "recording">("orbit");
  const [runId, setRunId] = useState("");
  const [error, setError] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);
  const scored = useRef("");

  const selected = datasets.data?.find((item) => item.id === selectedId) ?? datasets.data?.[0];
  const ready = selected?.status === "completed";
  const truth = useQuery({
    queryKey: ["sim-truth", selected?.id],
    queryFn: () => api.simulationTruth(selected!.id),
    enabled: Boolean(selected && ready),
  });
  const frames = truth.data?.frames.frames ?? [];
  const current = frames[Math.min(frame, Math.max(frames.length - 1, 0))];
  const state = useQuery({
    queryKey: ["sim-state", selected?.id, current?.timestamp],
    queryFn: () => api.simulationState(selected!.id, current!.timestamp),
    enabled: Boolean(selected && ready && current),
  });
  const evaluations = useQuery({
    queryKey: ["sim-evals", selected?.id],
    queryFn: () => api.simulationEvaluations(selected!.id),
    enabled: Boolean(selected && ready),
  });
  const watched = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.run.status;
      return status === "completed" || status === "failed" || status === "canceled" ? false : 1500;
    },
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["simulations"] });
  const analyze = useMutation({
    mutationFn: (preset: string) => api.analyzeSimulation(selected!.id, preset),
    onSuccess: (run) => { scored.current = ""; setRunId(run.id); },
    onError: (err: Error) => setError(err.message),
  });
  const score = useMutation({
    mutationFn: () => api.evaluateSimulation(selected!.id, runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sim-evals", selected?.id] }),
    onError: (err: Error) => setError(err.message),
  });

  useEffect(() => {
    if (watched.data?.run.status !== "completed" || !runId || scored.current === runId || !selected) return;
    scored.current = runId;
    score.mutate();
  }, [watched.data?.run.status, runId, selected, score]);

  const seek = (timestamp: number) => {
    const video = videoRef.current;
    if (!video || !selected) return;
    video.currentTime = timestamp;
    setFrame(Math.max(0, Math.min(frames.length - 1, Math.round(timestamp * selected.fps))));
  };

  const fovY = truth.data ? verticalFov(truth.data.scene.width, truth.data.scene.height, truth.data.scene.camera.fov_x_deg) : 42;
  const activeObject = objectId || current?.instances[0]?.instance_id || "";

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-2xl font-medium">Scene Lab</h1>
        <p className="text-sm text-[var(--muted)]">
          One kinematic scene produces the video, the masks, and the physical state. Analysis sees only the video.
        </p>
      </div>

      <section className="panel p-4">
        <h2 className="mb-2 text-sm font-medium">Generation jobs</h2>
        <div className="space-y-2">
          {datasets.data?.map((item) => (
            <div key={item.id} className={`flex items-center gap-2 rounded-md border p-2 text-sm ${item.id === selected?.id ? "border-[var(--teal)]" : "border-[var(--line)]"}`}>
              <button type="button" className="min-w-0 flex-1 text-left" onClick={() => { setSelectedId(item.id); setFrame(0); setRunId(""); }}>
                <div className="flex flex-wrap justify-between gap-2">
                  <span>{humanize(item.variant)} · {humanize(item.camera_mode)} · seed {item.seed}</span>
                  <span className="text-[var(--muted)]">{jobText(item)}</span>
                </div>
                <div className="text-xs text-[var(--muted)]">{item.width}×{item.height} · {item.fps} fps · {item.duration}s · {humanize(item.quality)}{item.pair_key ? ` · pair ${humanizeText(item.pair_key)}` : ""}</div>
                {item.error && <div className="text-xs text-[var(--rose)]">{item.error}</div>}
              </button>
              {(item.status === "failed" || item.status === "canceled") && (
                <button className="btn h-fit self-center" type="button" onClick={() => api.retrySimulation(item.id).then(refresh)}>Retry</button>
              )}
            </div>
          ))}
          {!datasets.data?.length && <p className="text-sm text-[var(--muted)]">No datasets yet.</p>}
        </div>
        {selected?.job && (selected.status === "pending" || selected.status === "running") && (
          <button className="btn mt-3" type="button" onClick={() => api.cancelSimulation(selected.job!.id).then(refresh)}>Cancel</button>
        )}
      </section>

      {selected && ready && truth.data && current && (
        <>
          <section className="panel space-y-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-medium">Simulation ground truth</h2>
              <a className="btn" href={`/api/simulations/${selected.id}/export`}>Export dataset</a>
            </div>
            <p className="text-xs text-[var(--muted)]">
              {humanizeText(truth.data.label)}. {selected.engine || "renderer"} {selected.blender_version}. These boxes and masks come from the rendered instance pass, not from a second animation.
            </p>
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <div className="relative">
                  <video
                    ref={videoRef}
                    className="w-full bg-black"
                    src={selected.video_id ? `/api/media/videos/${selected.video_id}/playback` : undefined}
                    controls
                    onTimeUpdate={(event) => {
                      const next = Math.round(event.currentTarget.currentTime * selected.fps);
                      setFrame(Math.max(0, Math.min(frames.length - 1, next)));
                    }}
                  />
                  {selected.variant !== "objects" && (
                    <img
                      alt=""
                      className="pointer-events-none absolute inset-0 h-full w-full opacity-45"
                      src={`/api/simulations/${selected.id}/frames/${current.index}?kind=instance`}
                    />
                  )}
                  {current.instances.map((instance) => instance.bbox && (
                    <button
                      key={instance.instance_id}
                      type="button"
                      className="absolute overflow-visible border p-0 text-left"
                      style={{
                        left: `${instance.bbox.x * 100}%`,
                        top: `${instance.bbox.y * 100}%`,
                        width: `${instance.bbox.w * 100}%`,
                        height: `${instance.bbox.h * 100}%`,
                        borderColor: activeObject === instance.instance_id ? "var(--teal)" : "white",
                      }}
                      onClick={() => setObjectId(instance.instance_id)}
                    >
                      <span className="pointer-events-none absolute left-0 top-0 whitespace-nowrap text-[10px] leading-none">
                        {humanize(instance.instance_id)}
                      </span>
                    </button>
                  ))}
                </div>
                <input
                  className="mt-2 w-full"
                  type="range"
                  min={0}
                  max={Math.max(frames.length - 1, 0)}
                  value={Math.min(frame, frames.length - 1)}
                  onChange={(event) => seek(Number(event.target.value) / selected.fps)}
                />
                <p className="text-xs text-[var(--muted)]">Frame {current.index} · {current.timestamp.toFixed(3)}s · step {humanize(state.data?.active_step_id || "none")}</p>
              </div>
              <div className="space-y-2 text-sm">
                {current.instances.map((instance) => (
                  <button key={instance.instance_id} type="button" className={`block w-full rounded-md border p-2 text-left ${activeObject === instance.instance_id ? "border-[var(--teal)]" : "border-[var(--line)]"}`} onClick={() => setObjectId(instance.instance_id)}>
                    <span className="font-medium">{objectName(instance.instance_id, truth.data.scene.objects.find((item) => item.instance_id === instance.instance_id)?.label)}</span>
                    <span className="ml-2 text-[var(--muted)]">{humanize(instance.visibility)} · {instance.visible_pixels} px</span>
                  </button>
                ))}
                <div className="text-xs text-[var(--muted)]">
                  {state.data?.relations.map((relation) => (
                    <div key={relation.constraint_id}>{humanize(relation.constraint_id)}: {relation.satisfied ? "satisfied" : "not satisfied"}{relation.unresolved ? " · unresolved" : ""} · {humanizeText(relation.detail)}</div>
                  ))}
                </div>
              </div>
            </div>
            <div>
              <h3 className="mb-1 text-xs uppercase tracking-wide text-[var(--muted)]">Events</h3>
              <div className="flex flex-wrap gap-2">
                {truth.data.events.events.map((event) => (
                  <button key={event.id} type="button" className="btn" onClick={() => seek(event.start)}>
                    {objectName(event.object_id, truth.data.scene.objects.find((item) => item.instance_id === event.object_id)?.label)} {humanize(event.kind)} {humanize(event.outcome)} {event.start.toFixed(2)}–{event.end.toFixed(2)}s
                  </button>
                ))}
              </div>
            </div>
            <div>
              <h3 className="mb-1 text-xs uppercase tracking-wide text-[var(--muted)]">Final physical conditions</h3>
              {truth.data.observability.conditions.map((condition) => (
                <div key={condition.constraint_id} className="text-sm">
                  {humanize(condition.constraint_id)}: physically {condition.satisfied ? "true" : "false"} · {humanize(condition.subject_visibility)} · picture supports {humanize(condition.justified_verdict)}
                </div>
              ))}
              <p className="mt-1 text-xs text-[var(--muted)]">{humanizeText(truth.data.observability.note)}</p>
            </div>
          </section>

          <section className="panel space-y-2 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-medium">Structural state</h2>
              <div className="flex gap-2">
                <button className="btn" type="button" onClick={() => setView("orbit")}>Inspection camera</button>
                <button className="btn" type="button" onClick={() => setView("recording")}>Recording camera</button>
              </div>
            </div>
            <p className="text-xs text-[var(--muted)]">
              Poses are the evaluated scene at {current.timestamp.toFixed(3)}s. Each object wears the same photograph mapped in the render, drawn without the viewer lights. Camera coordinates here are simulator truth and are not a claim about real footage.
            </p>
            {state.data && (
              <SceneViewer
                objects={truth.data.scene.objects}
                poses={state.data.objects}
                cameraMatrix={state.data.camera.matrix}
                fovY={fovY}
                textureUrls={Object.fromEntries((truth.data.textures ?? []).map((id) => [id, `/api/simulations/${selected.id}/textures/${encodeURIComponent(id)}`]))}
                selected={activeObject}
                view={view}
                onSelect={setObjectId}
              />
            )}
            {state.data && (
              <p className="text-xs text-[var(--muted)]">
                Camera {humanize(state.data.camera.mode)} at {state.data.camera.position.map((value) => value.toFixed(3)).join(", ")}. Kinematic {String(state.data.kinematic)}. Rigid body {String(state.data.rigid_body)}.
              </p>
            )}
          </section>

          <section className="panel space-y-3 p-4">
            <h2 className="text-sm font-medium">Evaluate an independent analysis</h2>
            <p className="text-xs text-[var(--muted)]">
              These runs use the deterministic motion baseline on the rendered video. The scene file is not in the request. A score is not evidence that the tracker understood the assembly.
            </p>
            <div className="flex flex-wrap gap-2">
              {PRESETS.map((preset) => (
                <button key={preset} className="btn" type="button" disabled={analyze.isPending} onClick={() => { setError(""); analyze.mutate(preset); }}>{humanize(preset)}</button>
              ))}
            </div>
            {error && <p className="text-sm text-[var(--rose)]">{error}</p>}
            {runId && <p className="text-sm">Run <Link className="text-[var(--teal)]" to={`/runs/${runId}`}>{runId.slice(0, 8)}</Link> · {watched.data?.run.status ?? "starting"}</p>}
            <div className="grid gap-3 md:grid-cols-2">
              {evaluations.data?.map((item) => (
                <article key={item.id} className="rounded-md border border-[var(--line)] p-3 text-sm">
                  <div className="mb-1 font-medium">{humanize(item.perception_preset)}</div>
                  <div>Detection {scoreText(item.result.detection) || `P ${item.result.detection?.precision ?? "—"} R ${item.result.detection?.recall ?? "—"} IoU ${item.result.detection?.mean_iou ?? "—"}`}</div>
                  <div>Identity {item.result.identity?.evaluated === false ? humanizeText(item.result.identity.reason ?? "NOT_EVALUATED") : `IDF1 ${item.result.identity?.idf1 ?? "—"} · switches ${item.result.identity?.identity_switches ?? "—"} · merges ${item.result.identity?.false_merges ?? "—"}`}</div>
                  <div>Camera pose {humanize(item.result.camera_pose?.status ?? "NOT_EVALUATED")}</div>
                  <div>Masks {humanize(item.result.masks?.status ?? "NOT_EVALUATED")}</div>
                  <p className="mt-1 text-xs text-[var(--muted)]">{item.result.outcomes?.note ? humanizeText(item.result.outcomes.note) : ""}</p>
                  {item.result.outcomes?.unsupported_claims?.map((claim, index) => (
                    <button key={`${claim.constraint_id}-${index}`} className="mt-1 block text-left text-xs text-[var(--amber)]" type="button" onClick={() => seek(claim.start ?? 0)}>
                      Unsupported claim on {humanize(claim.constraint_id)}. {humanizeText(claim.reason)}
                    </button>
                  ))}
                </article>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

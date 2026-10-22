import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, Outlet, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { AssetPreview } from "../components/AssetPreview";
import { BeforeAfter } from "../components/BeforeAfter";
import { Timeline } from "../components/Timeline";
import { VideoStage } from "../components/VideoStage";
import { WorkflowGraph } from "../components/WorkflowGraph";
import { api, evidenceClass, formatTime, formatWhen } from "../lib/api";
import { BundleContext, useBundle } from "../lib/bundleContext";
import type { Bundle } from "../lib/types";
import { useInspector } from "../stores/ui";
import { AudioPage } from "./Audio";

function label(tab: string) {
  return tab.charAt(0).toUpperCase() + tab.slice(1);
}

const ANALYSIS_TABS = ["", "objects", "states", "workflow", "steps", "pipeline", "audio", "evaluate", "logs"];

export function RunLayout() {
  const { runId = "" } = useParams();
  return <RunAnalysis runId={runId} />;
}

export function RunAnalysis({ runId, embedded = false }: { runId: string; embedded?: boolean }) {
  const [params] = useSearchParams();
  const [tab, setTab] = useState("objects");
  const query = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    refetchInterval: (q) => {
      const status = q.state.data?.run.status;
      return status === "pending" || status === "running" ? 1200 : false;
    },
  });
  useEffect(() => {
    const time = Number(params.get("t"));
    if (Number.isFinite(time) && params.get("t")) useInspector.getState().setTime(time);
  }, [params, runId]);
  if (query.isError) return <p className="text-[var(--rose)]">{(query.error as Error).message}</p>;
  if (!query.data) return <p className="text-[var(--muted)]">Loading analysis…</p>;
  const bundle = query.data;
  return (
    <BundleContext.Provider value={bundle}>
    <div className="space-y-3">
      <div>
        {!embedded && <Link className="text-sm text-[var(--muted)]" to="/runs">← Back to runs</Link>}
        <header className={`${embedded ? "" : "mt-2 "}flex flex-wrap items-start justify-between gap-3`}>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              {embedded ? (
                <h2 className="text-xl font-medium">{bundle.run.name}</h2>
              ) : (
                <h1 className="text-xl font-medium">{bundle.run.name}</h1>
              )}
              <span className="tag">{bundle.run.status}</span>
              {bundle.run.fixture && <span className="tag fixture">Fixture — authored, not model output</span>}
              <span className="tag">{bundle.run.research_mode.replaceAll("_", " ")}</span>
            </div>
            <p className="mt-1 text-xs text-[var(--muted)]">
              {bundle.run.provider_id} / {bundle.run.model_id} · {bundle.run.modality} · {bundle.run.sampling_preset}
              {bundle.revision ? ` · ${bundle.revision.layer} v${bundle.revision.version}` : ""}
            </p>
            {bundle.coverage?.temporal_coverage_note && (
              <p className="mt-1 max-w-3xl text-xs text-[var(--amber)]">{bundle.coverage.temporal_coverage_note}</p>
            )}
          </div>
          <RunActions bundle={bundle} />
        </header>
      </div>
      <nav className="flex gap-2 overflow-auto text-sm">
        {ANALYSIS_TABS.map((item) => {
          const name = label(item || "inspect");
          if (embedded) {
            const selected = tab === item;
            return (
              <button
                key={item || "inspect"}
                type="button"
                className={`rounded-md px-2 py-1 ${selected ? "bg-[#1a221d] text-[var(--teal)]" : "text-[var(--muted)]"}`}
                onClick={() => setTab(item)}
              >
                {name}
              </button>
            );
          }
          return (
            <NavLink
              key={item || "inspect"}
              end={item === ""}
              to={item ? `/runs/${runId}/${item}` : `/runs/${runId}`}
              className={({ isActive }) => `rounded-md px-2 py-1 ${isActive ? "bg-[#1a221d] text-[var(--teal)]" : "text-[var(--muted)]"}`}
            >
              {name}
            </NavLink>
          );
        })}
      </nav>
      {embedded ? <AnalysisTab tab={tab} /> : <Outlet />}
    </div>
    </BundleContext.Provider>
  );
}

function AnalysisTab({ tab }: { tab: string }) {
  switch (tab) {
    case "objects":
      return <ObjectsPage />;
    case "states":
      return <StatesPage />;
    case "workflow":
      return <WorkflowPage />;
    case "steps":
      return <StepsPage />;
    case "pipeline":
      return <PipelinePage />;
    case "audio":
      return <AudioPage />;
    case "evaluate":
      return <EvaluatePage />;
    case "logs":
      return <LogsPage />;
    default:
      return <InspectPage />;
  }
}

function RunActions({ bundle }: { bundle: Bundle }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const refresh = () => client.invalidateQueries({ queryKey: ["run", bundle.run.id] });
  return (
    <div className="flex flex-wrap gap-2">
      <button className="btn" onClick={async () => { const copy = await api.duplicateRun(bundle.run.id); navigate(`/runs/${copy.id}/pipeline`); }}>Duplicate</button>
      <button className="btn" onClick={async () => { await api.cancelRun(bundle.run.id); await refresh(); }}>Cancel</button>
      <button className="btn" onClick={async () => { await api.deleteRun(bundle.run.id); navigate("/"); }}>Delete</button>
      <a className="btn" href={`/api/runs/${bundle.run.id}/export`}>Export archive</a>
      <a className="btn" href={`/api/runs/${bundle.run.id}/analysis.json`}>Analysis JSON</a>
    </div>
  );
}

export function InspectPage() {
  const bundle = useBundle();
  const actionId = useInspector((state) => state.selectedAction);
  const action = bundle.actions.find((item) => item.local_id === actionId) ?? bundle.actions[0];
  return (
    <div className="grid min-h-[70vh] gap-3 xl:grid-cols-[280px_1fr_320px]">
      <section className="panel max-h-[78vh] space-y-3 overflow-auto p-3">
        <Timeline bundle={bundle} />
        <div className="space-y-1">
          {bundle.actions.map((item, index) => (
            <button
              key={item.id}
              className={`block w-full rounded-md px-2 py-1 text-left text-sm ${action?.id === item.id ? "bg-[#1a221d]" : ""}`}
              onClick={() => {
                useInspector.getState().selectAction(item.local_id);
                useInspector.getState().setTime(item.start);
                useInspector.getState().setLoop({ start: item.start, end: item.end });
              }}
            >
              <div>{index + 1}. {item.action_type} <span className="text-[var(--muted)]">{item.outcome}</span></div>
              <div className="mono text-[10px] text-[var(--muted)]">{formatTime(item.start)}–{formatTime(item.end)}</div>
            </button>
          ))}
        </div>
      </section>
      <section className="panel p-3"><VideoStage bundle={bundle} /></section>
      <section className="panel max-h-[78vh] space-y-3 overflow-auto p-3 text-sm">
        {action ? <ActionDetail bundle={bundle} actionId={action.local_id} /> : <p className="text-[var(--muted)]">No actions yet.</p>}
      </section>
    </div>
  );
}

function ActionDetail({ bundle, actionId }: { bundle: Bundle; actionId: string }) {
  const action = bundle.actions.find((item) => item.local_id === actionId);
  const [label, setLabel] = useState("");
  const client = useQueryClient();
  if (!action) return null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <span className={`tag ${evidenceClass(action.evidence_class)}`}>{action.evidence_class}</span>
        <span className="tag">{action.provenance}</span>
        <span className="tag">{action.outcome}</span>
      </div>
      <h2 className="text-base">{action.action_type}</h2>
      <p>{action.description}</p>
      <p className="text-xs text-[var(--muted)]">{formatTime(action.start)}–{formatTime(action.end)} · uncertainty {action.uncertainty}</p>
      <p><span className="text-[var(--muted)]">Before: </span>{action.initial_state || "not stated"}</p>
      <p><span className="text-[var(--muted)]">After: </span>{action.final_state || "not stated"}</p>
      <p className="text-xs">{action.evidence_note}</p>
      <div className="flex flex-wrap gap-2">
        {action.object_local_ids.map((id) => {
          const object = bundle.objects.find((item) => item.local_id === id);
          return object?.primary_image_url ? <img key={id} src={object.primary_image_url} alt={object.label} title={object.label} className="h-16 w-16 rounded bg-black object-contain" /> : <span key={id} className="tag">{id}</span>;
        })}
      </div>
      <BeforeAfter bundle={bundle} action={action} />
      <label className="block text-xs text-[var(--muted)]">
        Correct description
        <textarea className="field mt-1" value={label} placeholder={action.description} onChange={(event) => setLabel(event.target.value)} />
      </label>
      <button
        className="btn"
        onClick={async () => {
          await api.patchAction(bundle.run.id, action.local_id, { description: label || action.description });
          setLabel("");
          await client.invalidateQueries({ queryKey: ["run", bundle.run.id] });
        }}
      >
        Save correction
      </button>
    </div>
  );
}

export function ObjectsPage() {
  const bundle = useBundle();
  const [open, setOpen] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [mergeDrop, setMergeDrop] = useState("");
  const [preview, setPreview] = useState<number | null>(null);
  const client = useQueryClient();
  const selected = bundle.objects.find((item) => item.local_id === open);
  const shots = selected?.images.filter((image) => image.url) ?? [];
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input className="field max-w-xs" placeholder="Add a missed object" value={label} onChange={(event) => setLabel(event.target.value)} />
        <button className="btn" disabled={!label} onClick={async () => { await api.addObject(bundle.run.id, label, "Added by the researcher."); setLabel(""); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Add</button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {bundle.objects.map((object) => (
          <button key={object.id} className="panel overflow-hidden text-left" onClick={() => { setOpen(object.local_id); setPreview(null); }}>
            {object.primary_image_url ? <img src={object.primary_image_url} alt="" className="h-36 w-full bg-black object-contain" /> : <div className="grid h-36 place-items-center text-xs text-[var(--muted)]">No crop yet</div>}
            <div className="space-y-1 p-3 text-sm">
              <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: object.color }} />{object.local_id}</div>
              <div className="font-medium">{object.label}</div>
              <div className="text-xs text-[var(--muted)]">{object.observation_count} observations · {object.uncertain_identity ? "identity uncertain" : "identity stated"}</div>
            </div>
          </button>
        ))}
      </div>
      {selected && (
        <div className="panel grid gap-4 p-4 lg:grid-cols-[280px_1fr]">
          {selected.primary_image_url && <img src={selected.primary_image_url} alt="" className="w-full rounded" />}
          <div className="space-y-2 text-sm">
            <h2>{selected.local_id} · {selected.label}</h2>
            <p>{selected.description}</p>
            <p className="text-[var(--muted)]">Initial: {selected.initial_state || "—"} · Final: {selected.final_state || "—"}</p>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex max-w-full min-w-0 items-center gap-4" htmlFor={`rename-${selected.local_id}`}>
                <span className="shrink-0 whitespace-nowrap">Rename</span>
                <input
                  key={selected.local_id}
                  className="field max-w-full"
                  style={{ width: "calc((48rem - 0.75rem) / 2)" }}
                  defaultValue={selected.label}
                  id={`rename-${selected.local_id}`}
                />
              </label>
              <button className="btn shrink-0" onClick={async () => {
                const input = document.getElementById(`rename-${selected.local_id}`) as HTMLInputElement;
                await api.patchObject(bundle.run.id, selected.local_id, { label: input.value });
                await client.invalidateQueries({ queryKey: ["run", bundle.run.id] });
              }}>Save label</button>
            </div>
            <div className="flex gap-2">
              <select className="field" value={mergeDrop} onChange={(event) => setMergeDrop(event.target.value)}>
                <option value="">Merge another id into this one</option>
                {bundle.objects.filter((item) => item.local_id !== selected.local_id).map((item) => <option key={item.local_id} value={item.local_id}>{item.local_id}</option>)}
              </select>
              <button className="btn" disabled={!mergeDrop} onClick={async () => { await api.mergeObjects(bundle.run.id, selected.local_id, mergeDrop); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Merge</button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {shots.map((image, index) => (
                <button
                  key={image.id}
                  type="button"
                  className="block w-full border-0 bg-transparent p-0 text-left"
                  aria-label={`View ${image.kind} at ${image.timestamp.toFixed(2)} seconds`}
                  onClick={() => setPreview(index)}
                >
                  <img src={image.url} alt={image.kind} className="h-20 w-full rounded bg-black object-contain" />
                  <div className="text-[10px] text-[var(--muted)]">{image.kind} · {image.timestamp.toFixed(2)}s</div>
                </button>
              ))}
            </div>
            {preview != null && (
              <AssetPreview
                key={`${selected.local_id}:${preview}`}
                images={shots.map((image) => ({
                  src: image.url,
                  filename: `${image.kind} · ${image.timestamp.toFixed(2)}s`,
                }))}
                startIndex={preview}
                onClose={() => setPreview(null)}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function StatesPage() {
  const bundle = useBundle();
  const [localId, setLocalId] = useState(bundle.objects[0]?.local_id ?? "");
  const time = useInspector((state) => state.time);
  const scene = useQuery({
    queryKey: ["scene", bundle.run.id, time.toFixed(2)],
    queryFn: () => api.scene(bundle.run.id, Number(time.toFixed(2))),
  });
  const states = bundle.states.filter((item) => item.object_local_id === localId);
  const transitions = bundle.transitions.filter((item) => item.object_local_id === localId);
  const relations = bundle.relationships.filter((item) => item.subject_local_id === localId || item.other_local_id === localId);
  return (
    <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
      <div className="space-y-1">
        {bundle.objects.map((object) => (
          <button key={object.id} className={`btn block w-full text-left ${localId === object.local_id ? "primary" : ""}`} onClick={() => setLocalId(object.local_id)}>{object.local_id} {object.label}</button>
        ))}
      </div>
      <div className="space-y-3">
        <h2 className="text-lg">State history</h2>
        {states.map((state) => (
          <button key={state.id} className="panel block w-full p-3 text-left text-sm" onClick={() => useInspector.getState().setTime(state.time_start)}>
            <div className="mono text-xs text-[var(--muted)]">{formatTime(state.time_start)}–{formatTime(state.time_end)}</div>
            <div>{state.state_label}</div>
            <div className="text-[var(--muted)]">{state.description}</div>
            <span className={`tag ${evidenceClass(state.evidence_class)}`}>{state.evidence_class}</span>
          </button>
        ))}
        <h3 className="text-sm">Transitions</h3>
        {transitions.map((item) => (
          <div key={item.id} className="text-sm">{formatTime(item.timestamp)} · {item.from_state} → {item.to_state} · <span className={`tag ${evidenceClass(item.evidence_class)}`}>{item.evidence_class}</span></div>
        ))}
        <h3 className="text-sm">Relationships</h3>
        {relations.map((item) => (
          <div key={item.id} className="text-sm">{item.subject_local_id} {item.kind} {item.other_local_id ?? ""} · {item.description}</div>
        ))}
        <div className="panel p-3 text-sm">
          <div className="mb-2 text-xs uppercase tracking-wide text-[var(--muted)]">Scene at {time.toFixed(2)}s</div>
          <pre className="mono max-h-64 overflow-auto text-xs">{JSON.stringify(scene.data ?? {}, null, 2)}</pre>
        </div>
      </div>
    </div>
  );
}

export function WorkflowPage() {
  const bundle = useBundle();
  const [from, setFrom] = useState(bundle.steps[0]?.local_id ?? "");
  const [to, setTo] = useState(bundle.steps[1]?.local_id ?? "");
  const [category, setCategory] = useState("observed_temporal");
  const client = useQueryClient();
  const stepId = useInspector((state) => state.selectedStep);
  const step = bundle.steps.find((item) => item.local_id === stepId);
  return (
    <div className="space-y-3">
      <WorkflowGraph bundle={bundle} />
      <div className="flex flex-wrap gap-2 text-sm">
        <select className="field w-auto" value={from} onChange={(event) => setFrom(event.target.value)}>{bundle.steps.map((item) => <option key={item.local_id}>{item.local_id}</option>)}</select>
        <select className="field w-auto" value={to} onChange={(event) => setTo(event.target.value)}>{bundle.steps.map((item) => <option key={item.local_id}>{item.local_id}</option>)}</select>
        <select className="field w-auto" value={category} onChange={(event) => setCategory(event.target.value)}>
          {["necessary_physical", "observed_temporal", "inferred", "unknown"].map((item) => <option key={item}>{item}</option>)}
        </select>
        <button className="btn" onClick={async () => { await api.addDependency(bundle.run.id, { from_step: from, to_step: to, category, rationale: "Added by the researcher." }); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Add dependency</button>
      </div>
      {step && (
        <div className="panel p-3 text-sm">
          <div className="font-medium">{step.name}</div>
          <p>Completion: {step.completion_condition}</p>
          <p>Observed: {step.observed_result}</p>
          <p className="text-[var(--muted)]">Objects: {step.required_objects.join(", ") || "none"}</p>
        </div>
      )}
      <ul className="space-y-1 text-sm">
        {bundle.dependencies.map((dep) => (
          <li key={dep.id} className="flex items-center justify-between gap-2">
            <span>{dep.from_step} → {dep.to_step} · {dep.category} · {dep.rationale}</span>
            <button className="btn" onClick={async () => { await api.removeDependency(bundle.run.id, dep.from_step, dep.to_step); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Remove</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function StepsPage() {
  const bundle = useBundle();
  const [index, setIndex] = useState(0);
  const step = bundle.steps[index];
  if (!step) return <p className="text-[var(--muted)]">No workflow steps.</p>;
  const start = step.time_start ?? 0;
  const end = step.time_end ?? start;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <button className="btn" onClick={() => setIndex((value) => Math.max(0, value - 1))}>Previous</button>
        <button className="btn" onClick={() => setIndex((value) => Math.min(bundle.steps.length - 1, value + 1))}>Next</button>
        <span className="text-sm text-[var(--muted)]">{index + 1} / {bundle.steps.length}</span>
      </div>
      <h2 className="text-xl">{step.number}. {step.name}</h2>
      <p>{step.expected_action}</p>
      <p className="text-sm text-[var(--muted)]">Expected: {step.completion_condition}</p>
      <p className="text-sm">Observed: {step.observed_result}</p>
      <div className="grid gap-2 md:grid-cols-3">
        {[start, (start + end) / 2, end].map((stamp) => {
          const frame = bundle.frames.reduce((best, item) => Math.abs(item.timestamp - stamp) < Math.abs(best.timestamp - stamp) ? item : best, bundle.frames[0]);
          return frame ? <img key={stamp} src={frame.url} alt="" className="panel aspect-video w-full bg-black object-contain" /> : null;
        })}
      </div>
      <div className="flex gap-2">
        {step.required_objects.map((id) => {
          const object = bundle.objects.find((item) => item.local_id === id);
          return <div key={id} className="text-xs">{object?.primary_image_url && <img src={object.primary_image_url} alt="" className="h-16 w-16 rounded bg-black object-contain" />}<div>{object?.label ?? id}</div></div>;
        })}
      </div>
      <button className="btn" onClick={() => { useInspector.getState().setTime(start); useInspector.getState().selectStep(step.local_id); }}>Show on timeline</button>
    </div>
  );
}

export function PipelinePage() {
  const bundle = useBundle();
  const client = useQueryClient();
  const done = bundle.stages.filter((stage) => stage.status === "completed" || stage.status === "skipped").length;
  const fraction = bundle.stages.length ? done / bundle.stages.length : 0;
  return (
    <div className="space-y-3">
      <div className="h-2 overflow-hidden rounded bg-[#0d1016]">
        <div className="h-full bg-[var(--teal)]" style={{ width: `${fraction * 100}%` }} />
      </div>
      <p className="text-xs text-[var(--muted)]">{done} of {bundle.stages.length} stages finished or skipped. The bar counts stages, not a guessed percentage.</p>
      {bundle.stages.map((stage) => (
        <div key={stage.stage} className="panel grid gap-2 p-3 text-sm md:grid-cols-[180px_120px_1fr_auto]">
          <div className="mono">{stage.stage}</div>
          <div>{stage.status}</div>
          <div className="text-[var(--muted)]">
            {stage.progress_current}/{stage.progress_total || 0}
            {stage.error ? ` · ${stage.error}` : ""}
            {stage.checkpoint?.reason ? ` · ${String(stage.checkpoint.reason)}` : ""}
          </div>
          {stage.status === "failed" && (
            <button className="btn" onClick={async () => { await api.retryStage(bundle.run.id, stage.stage); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Retry</button>
          )}
        </div>
      ))}
    </div>
  );
}

export function EvaluatePage() {
  const bundle = useBundle();
  const client = useQueryClient();
  const truths = useQuery({ queryKey: ["truth", bundle.run.id], queryFn: () => api.groundTruth(bundle.run.id) });
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [iou, setIou] = useState(0.3);
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--muted)]">Promote the current revision, then score another run against it. Matching uses interval overlap. Model confidence is not accuracy.</p>
      <button className="btn" onClick={async () => { await api.promoteTruth(bundle.run.id, `Reference ${bundle.run.name}`); await client.invalidateQueries({ queryKey: ["truth", bundle.run.id] }); }}>Save current revision as ground truth</button>
      <label className="flex w-fit items-center gap-4 text-sm">
        <span className="whitespace-nowrap">Action IoU threshold</span>
        <input className="field max-w-xs" type="number" step={0.05} value={iou} onChange={(event) => setIou(Number(event.target.value))} />
      </label>
      <ul className="space-y-2 text-sm">
        {truths.data?.map((item) => (
          <li key={item.id}>
            <button className="btn" onClick={async () => { const report = await api.evaluate(bundle.run.id, item.id, { action_iou: iou }); setResult(report.metrics); }}>{item.name}</button>
          </li>
        ))}
      </ul>
      {result && <pre className="panel mono overflow-auto p-3 text-xs">{JSON.stringify(result, null, 2)}</pre>}
    </div>
  );
}

export function LogsPage() {
  const bundle = useBundle();
  const rows = useQuery({ queryKey: ["requests", bundle.run.id], queryFn: () => api.requests(bundle.run.id) });
  return (
    <div className="space-y-3">
      <h2 className="text-lg">Requests and raw responses</h2>
      {bundle.validation?.length ? (
        <ul className="space-y-1 text-sm">
          {bundle.validation.map((issue, index) => <li key={index} className={issue.severity === "error" ? "text-[var(--rose)]" : "text-[var(--amber)]"}>{issue.severity} · {issue.code} · {issue.message}</li>)}
        </ul>
      ) : <p className="text-sm text-[var(--muted)]">No validation issues on the active document.</p>}
      {rows.data?.map((row) => (
        <details key={String(row.id)} className="panel p-3 text-sm">
          <summary className="cursor-pointer">{String(row.stage)} · {String(row.model_id)} · {String(row.status)} · {String(row.latency_ms)} ms</summary>
          <pre className="mono mt-2 max-h-64 overflow-auto text-xs">{String(row.response_text || row.error || "")}</pre>
        </details>
      ))}
      {!rows.data?.length && <p className="text-sm text-[var(--muted)]">This run has not recorded a model request. Fixture and deterministic analyses are stored as documents, not provider calls.</p>}
    </div>
  );
}

export function RunsPage() {
  const [status, setStatus] = useState("");
  const runs = useQuery({ queryKey: ["runs", status], queryFn: () => api.runs(status ? { status } : {}) });
  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-medium">Runs</h1>
      <select className="field max-w-xs" value={status} onChange={(event) => setStatus(event.target.value)}>
        <option value="">All statuses</option>
        {["pending", "running", "completed", "failed", "canceled"].map((item) => <option key={item}>{item}</option>)}
      </select>
      <div className="space-y-2">
        {runs.data?.map((run) => {
          const when = formatWhen(run.created_at);
          return (
            <a key={run.id} href={`/runs/${run.id}`} className="panel flex items-baseline justify-between gap-4 p-3 text-sm">
              <span className="min-w-0">
                {run.name} · {run.status} · {run.provider_id}/{run.model_id} · {run.object_count} objects · {run.action_count} actions
              </span>
              {when && <time className="mono shrink-0 text-xs text-[var(--muted)]" dateTime={run.created_at}>{when}</time>}
            </a>
          );
        })}
      </div>
    </div>
  );
}


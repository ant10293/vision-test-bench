import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { AssetPreview } from "../components/AssetPreview";
import { BeforeAfter } from "../components/BeforeAfter";
import { Bone } from "../components/Skeleton";
import { PerceptionPanel } from "../components/PerceptionPanel";
import { Timeline } from "../components/Timeline";
import { VideoStage } from "../components/VideoStage";
import { WorkflowGraph } from "../components/WorkflowGraph";
import { api, evidenceClass, formatTime, formatUsd, formatWhen } from "../lib/api";
import { formatLogText } from "../lib/formatLog";
import { humanize, humanizeText, labeledId } from "../lib/humanize";
import { objectScene } from "../lib/sceneSlice";
import { BundleContext, useBundle } from "../lib/bundleContext";
import type { Bundle, Observation } from "../lib/types";
import { useInspector } from "../stores/ui";
import { AudioPage } from "./Audio";

function label(tab: string) {
  return tab.charAt(0).toUpperCase() + tab.slice(1);
}

const ANALYSIS_TABS = ["", "objects", "states", "workflow", "steps", "pipeline", "audio", "evaluate", "logs"];
const OBJECT_GRID = "grid gap-3 sm:grid-cols-2 xl:grid-cols-4";
const OBJECT_PLACEHOLDERS = 4;
const INSPECT_LAYOUT = "grid min-h-[70vh] min-w-0 gap-3 xl:grid-cols-[280px_minmax(0,1fr)_320px]";
const INSPECT_SIDE = "panel max-h-[78vh] space-y-3 overflow-auto p-3";
const INSPECT_STAGE = "panel min-w-0 p-3";
const INSPECT_DETAIL = "panel max-h-[78vh] space-y-3 overflow-auto p-3 text-sm";
const ACTION_PLACEHOLDERS = 5;

/** The still that stands in for an object. Prefer its first appearance over the leading gallery asset. */
function objectThumbnail(object: Bundle["objects"][number] | null | undefined): string {
  if (!object) return "";
  const initial = object.images.find((image) => image.kind === "initial" && image.url);
  return initial?.url || object.primary_image_url;
}

/** Stills left out of the fallback gallery: boxed frames, and the crop that duplicates unobstructed. */
function isGalleryImage(kind: string) {
  if (kind === "boxed" || kind.endsWith("_boxed")) return false;
  if (kind === "crop" || kind === "primary") return false;
  return true;
}

const NAMED_STILLS = ["unobstructed", "initial", "final", "alternate"] as const;

type ObjectShot = { id: string; url: string; caption: string };

/** Every boxed observation, as a zoomed crop. Named stills keep their labels when the time matches. */
function objectShots(object: Bundle["objects"][number], observations: Observation[]): ObjectShot[] {
  const boxed = observations
    .filter((item) => item.object_local_id === object.local_id && item.bbox && item.frame_id)
    .sort((a, b) => a.timestamp - b.timestamp || a.id.localeCompare(b.id));
  if (boxed.length) {
    return boxed.map((item) => ({
      id: item.id,
      url: `/api/media/observations/${item.id}/crop`,
      caption: observationCaption(item, object.images),
    }));
  }
  return object.images
    .filter((image) => image.url && isGalleryImage(image.kind))
    .map((image) => ({
      id: image.id,
      url: image.url,
      caption: `${humanize(image.kind)} · ${image.timestamp.toFixed(2)}s`,
    }));
}

function observationCaption(observation: Observation, images: Bundle["objects"][number]["images"]): string {
  const time = `${observation.timestamp.toFixed(2)}s`;
  const roles = NAMED_STILLS.filter((kind) =>
    images.some((image) => image.kind === kind && Math.abs(image.timestamp - observation.timestamp) < 0.001),
  ).map((kind) => humanize(kind));
  if (roles.length) return `${roles.join(", ")} · ${time}`;
  if (observation.visibility && observation.visibility !== "visible") return `${humanize(observation.visibility)} · ${time}`;
  return time;
}

function analysisTab(pathname: string, runId: string): string {
  const base = `/runs/${runId}`;
  if (!pathname.startsWith(base)) return "";
  const head = pathname.slice(base.length).replace(/^\//, "").split("/")[0] ?? "";
  return (ANALYSIS_TABS as readonly string[]).includes(head) ? head : "";
}

const OpenInspectContext = createContext<() => void>(() => {});

function useOpenInspect() {
  return useContext(OpenInspectContext);
}

export function RunLayout() {
  const { runId = "" } = useParams();
  return <RunAnalysis runId={runId} />;
}

export function RunAnalysis({ runId, embedded = false }: { runId: string; embedded?: boolean }) {
  const [params] = useSearchParams();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [tab, setTab] = useState("objects");
  const openInspect = () => {
    if (embedded) setTab("");
    else navigate(`/runs/${runId}`);
  };
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
  const activeTab = embedded ? tab : analysisTab(pathname, runId);
  if (query.isError) return <p className="text-[var(--rose)]">{(query.error as Error).message}</p>;
  if (!query.data) return <AnalysisPending embedded={embedded} runId={runId} tab={activeTab} onTab={setTab} />;
  const bundle = query.data;
  return (
    <OpenInspectContext.Provider value={openInspect}>
      <BundleContext.Provider value={bundle}>
        <AnalysisFrame embedded={embedded} runId={runId} tab={activeTab} onTab={setTab} bundle={bundle}>
          {embedded ? <AnalysisTab tab={tab} /> : <Outlet />}
        </AnalysisFrame>
      </BundleContext.Provider>
    </OpenInspectContext.Provider>
  );
}

export function AnalysisPending({
  embedded,
  runId = "",
  tab,
  onTab,
}: {
  embedded: boolean;
  runId?: string;
  tab: string;
  onTab?: (tab: string) => void;
}) {
  return (
    <AnalysisFrame embedded={embedded} runId={runId} tab={tab} onTab={onTab} bundle={null} pending>
      <PendingPane tab={tab} />
    </AnalysisFrame>
  );
}

function AnalysisFrame({
  embedded,
  runId,
  tab,
  onTab,
  bundle,
  pending = false,
  children,
}: {
  embedded: boolean;
  runId: string;
  tab: string;
  onTab?: (tab: string) => void;
  bundle: Bundle | null;
  pending?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="space-y-3" aria-busy={pending || undefined}>
      <div>
        {!embedded && <Link className="text-sm text-[var(--muted)]" to="/runs">← Back to runs</Link>}
        <AnalysisHeader bundle={bundle} embedded={embedded} />
      </div>
      <AnalysisNav embedded={embedded} runId={runId} tab={tab} pending={pending} onTab={onTab} />
      {children}
      {pending && <span className="sr-only">Loading analysis</span>}
    </div>
  );
}

function AnalysisHeader({ bundle, embedded }: { bundle: Bundle | null; embedded: boolean }) {
  const Title = embedded ? "h2" : "h1";
  return (
    <header className={`${embedded ? "" : "mt-2 "}flex flex-wrap items-start justify-between gap-3`}>
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <Title className="text-xl font-medium">
            {bundle ? humanizeText(bundle.run.name) : <Bone width="14rem" />}
          </Title>
          {bundle ? (
            <>
              <span className="tag">{humanize(bundle.run.status)}</span>
              {bundle.run.fixture && <span className="tag fixture">Fixture — authored, not model output</span>}
              <span className="tag">{humanize(bundle.run.research_mode)}</span>
            </>
          ) : (
            <>
              <span className="tag"><Bone width="5.5rem" /></span>
              <span className="tag"><Bone width="6.5rem" /></span>
            </>
          )}
        </div>
        <p className="mt-1 text-xs text-[var(--muted)]">
          {bundle ? (
            <>
              {humanize(bundle.run.provider_id)} / {humanize(bundle.run.model_id)} · {humanize(bundle.run.modality)} · {humanize(bundle.run.sampling_preset)}
              {bundle.revision ? ` · ${humanize(bundle.revision.layer)} v${bundle.revision.version}` : ""}
              {" · "}{formatUsd(bundle.run.cost_usd)}
              {bundle.run.unpriced_calls > 0 ? ` · ${bundle.run.unpriced_calls} unpriced` : ""}
            </>
          ) : (
            <Bone width="28rem" />
          )}
        </p>
        {bundle?.coverage?.temporal_coverage_note && (
          <p className="mt-1 max-w-3xl text-xs text-[var(--amber)]">{bundle.coverage.temporal_coverage_note}</p>
        )}
      </div>
      <RunActions bundle={bundle} embedded={embedded} />
    </header>
  );
}

function AnalysisNav({
  embedded,
  runId,
  tab,
  pending,
  onTab,
}: {
  embedded: boolean;
  runId: string;
  tab: string;
  pending: boolean;
  onTab?: (tab: string) => void;
}) {
  return (
    <nav className="flex gap-2 overflow-auto text-sm">
      {ANALYSIS_TABS.map((item) => {
        const name = label(item || "inspect");
        const key = item || "inspect";
        if (embedded) {
          const selected = tab === item;
          return (
            <button
              key={key}
              type="button"
              disabled={pending}
              className={`rounded-md px-2 py-1 ${selected ? "bg-[#1a221d] text-[var(--teal)]" : "text-[var(--muted)]"}`}
              onClick={() => onTab?.(item)}
            >
              {name}
            </button>
          );
        }
        return (
          <NavLink
            key={key}
            end={item === ""}
            to={item ? `/runs/${runId}/${item}` : `/runs/${runId}`}
            className={({ isActive }) => `rounded-md px-2 py-1 ${isActive ? "bg-[#1a221d] text-[var(--teal)]" : "text-[var(--muted)]"}`}
          >
            {name}
          </NavLink>
        );
      })}
    </nav>
  );
}

function PendingPane({ tab }: { tab: string }) {
  if (tab === "objects") return <ObjectsSkeleton />;
  if (tab === "") return <InspectSkeleton />;
  return <PanelSkeleton />;
}

function ObjectsSkeleton() {
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input className="field max-w-xs" placeholder="Add a missed object" disabled />
        <button className="btn" type="button" disabled>Add</button>
      </div>
      <div className={OBJECT_GRID}>
        {Array.from({ length: OBJECT_PLACEHOLDERS }, (_, index) => <ObjectTile key={index} object={null} />)}
      </div>
    </div>
  );
}

function InspectSkeleton() {
  return (
    <div className={INSPECT_LAYOUT}>
      <section className={INSPECT_SIDE}>
        <Timeline />
        <div className="space-y-1" aria-hidden="true">
          {Array.from({ length: ACTION_PLACEHOLDERS }, (_, index) => (
            <div key={index} className="block w-full rounded-md px-2 py-1 text-left text-sm">
              <Bone width="78%" />
              <div className="mono text-[10px]"><Bone width="6rem" /></div>
            </div>
          ))}
        </div>
      </section>
      <section className={INSPECT_STAGE}>
        <div className="stage-slot flex min-h-0 w-full flex-col gap-2">
          <div className="stage-frame overflow-hidden rounded-lg bg-black">
            <Bone className="bone-fill" />
          </div>
        </div>
      </section>
      <section className={INSPECT_DETAIL} aria-hidden="true">
        <Bone width="32%" />
        <div className="font-medium"><Bone width="70%" /></div>
        <div className="text-xs"><Bone width="48%" /></div>
        <Bone width="92%" />
      </section>
    </div>
  );
}

function PanelSkeleton() {
  return (
    <div className="panel space-y-2 p-3 text-sm" aria-hidden="true">
      <Bone width="36%" />
      <Bone width="88%" />
      <Bone width="70%" />
    </div>
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

function RunActions({ bundle, embedded }: { bundle: Bundle | null; embedded: boolean }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  if (!bundle) {
    return (
      <div className="flex flex-wrap gap-2" aria-hidden="true">
        <span className="btn">Duplicate</span>
        <span className="btn">Cancel</span>
        <span className="btn">Delete</span>
        <span className="btn">Export archive</span>
        <span className="btn">Analysis JSON</span>
      </div>
    );
  }
  const refresh = () => client.invalidateQueries({ queryKey: ["run", bundle.run.id] });
  const remove = async () => {
    await api.deleteRun(bundle.run.id);
    await client.invalidateQueries({ queryKey: ["runs"] });
    await client.invalidateQueries({ queryKey: ["dashboard"] });
    navigate(embedded ? "/" : "/runs");
  };
  return (
    <div className="flex flex-wrap gap-2">
      <button className="btn" onClick={async () => { const copy = await api.duplicateRun(bundle.run.id); navigate(`/runs/${copy.id}/pipeline`); }}>Duplicate</button>
      <button className="btn" onClick={async () => { await api.cancelRun(bundle.run.id); await refresh(); }}>Cancel</button>
      <button className="btn" onClick={remove}>Delete</button>
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
    <div className={INSPECT_LAYOUT}>
      <section className={INSPECT_SIDE}>
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
              <div>{index + 1}. {humanize(item.action_type)} <span className="text-[var(--muted)]">{humanize(item.outcome)}</span></div>
              <div className="mono text-[10px] text-[var(--muted)]">{formatTime(item.start)}–{formatTime(item.end)}</div>
            </button>
          ))}
        </div>
      </section>
      <section className={INSPECT_STAGE}><VideoStage bundle={bundle} /></section>
      <section className={INSPECT_DETAIL}>
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
        <span className={`tag ${evidenceClass(action.evidence_class)}`}>{humanize(action.evidence_class)}</span>
        <span className="tag">{humanize(action.provenance)}</span>
        <span className="tag">{humanize(action.outcome)}</span>
      </div>
      <h2 className="text-base">{humanize(action.action_type)}</h2>
      <p>{action.description}</p>
      <p className="text-xs text-[var(--muted)]">{formatTime(action.start)}–{formatTime(action.end)} · uncertainty {action.uncertainty}</p>
      <p><span className="text-[var(--muted)]">Before: </span>{humanize(action.initial_state) || "not stated"}</p>
      <p><span className="text-[var(--muted)]">After: </span>{humanize(action.final_state) || "not stated"}</p>
      <p className="text-xs">{action.evidence_note}</p>
      <div className="flex flex-wrap gap-2">
        {action.object_local_ids.map((id) => {
          const object = bundle.objects.find((item) => item.local_id === id);
          const thumb = objectThumbnail(object);
          return thumb ? <img key={id} src={thumb} alt={humanize(object?.label ?? id)} title={humanize(object?.label ?? id)} className="h-16 w-16 rounded bg-black object-contain" /> : <span key={id} className="tag">{humanize(id)}</span>;
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

function ObjectTile({ object, onOpen }: { object: Bundle["objects"][number] | null; onOpen?: () => void }) {
  const thumb = objectThumbnail(object);
  const media = thumb ? (
    <img src={thumb} alt="" className="max-h-48 w-auto max-w-full object-contain" />
  ) : object ? (
    <div className="grid h-48 place-items-center text-xs text-[var(--muted)]">No crop yet</div>
  ) : (
    <div className="h-48 w-full"><Bone className="bone-fill" /></div>
  );
  const body = (
    <>
      <div className="flex flex-1 items-center justify-center p-3">{media}</div>
      <div className="mt-auto space-y-1 px-3 pb-3 text-sm">
        {object ? (
          <>
            <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: object.color }} />{humanize(object.local_id)}</div>
            <div className="font-medium">{humanize(object.label)}</div>
            <div className="text-xs text-[var(--muted)]">{object.observation_count} observations · {object.uncertain_identity ? "identity uncertain" : "identity stated"}</div>
          </>
        ) : (
          <>
            <div><Bone width="38%" /></div>
            <div className="font-medium"><Bone width="64%" /></div>
            <div className="text-xs"><Bone width="80%" /></div>
          </>
        )}
      </div>
    </>
  );
  if (!object) return <div className="panel flex h-full flex-col overflow-hidden text-left" aria-hidden="true">{body}</div>;
  return (
    <button type="button" className="panel flex h-full flex-col overflow-hidden text-left" onClick={onOpen}>
      {body}
    </button>
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
  const selectedThumb = objectThumbnail(selected);
  const shots = selected ? objectShots(selected, bundle.observations) : [];
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input className="field max-w-xs" placeholder="Add a missed object" value={label} onChange={(event) => setLabel(event.target.value)} />
        <button className="btn" disabled={!label} onClick={async () => { await api.addObject(bundle.run.id, label, "Added by the researcher."); setLabel(""); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Add</button>
      </div>
      <div className={OBJECT_GRID}>
        {bundle.objects.map((object) => (
          <ObjectTile key={object.id} object={object} onOpen={() => { setOpen(object.local_id); setPreview(null); }} />
        ))}
      </div>
      {selected && (
        <div className="panel grid gap-4 p-4 lg:grid-cols-[280px_1fr]">
          {selectedThumb && <img src={selectedThumb} alt="" className="w-full rounded" />}
          <div className="space-y-2 text-sm">
            <h2>{labeledId(selected.local_id, selected.label)}</h2>
            <p>{selected.description}</p>
            <p className="text-[var(--muted)]">Initial: {humanize(selected.initial_state) || "—"} · Final: {humanize(selected.final_state) || "—"}</p>
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
                {bundle.objects.filter((item) => item.local_id !== selected.local_id).map((item) => <option key={item.local_id} value={item.local_id}>{humanize(item.local_id)}</option>)}
              </select>
              <button className="btn" disabled={!mergeDrop} onClick={async () => { await api.mergeObjects(bundle.run.id, selected.local_id, mergeDrop); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Merge</button>
            </div>
          </div>
          <div className="flex flex-wrap items-start gap-3 lg:col-span-2">
            {shots.map((image, index) => (
              <button
                key={image.id}
                type="button"
                className="block w-fit max-w-full border-0 bg-transparent p-0 text-left"
                aria-label={`View ${image.caption}`}
                onClick={() => setPreview(index)}
              >
                <img src={image.url} alt={image.caption} className="h-64 w-auto max-w-full rounded bg-black object-contain" />
                <div className="mt-1 text-[10px] text-[var(--muted)]">{image.caption}</div>
              </button>
            ))}
          </div>
          {preview != null && (
            <AssetPreview
              key={`${selected.local_id}:${preview}`}
              images={shots.map((image) => ({
                src: image.url,
                filename: image.caption,
              }))}
              startIndex={preview}
              onClose={() => setPreview(null)}
            />
          )}
        </div>
      )}
      <PerceptionPanel bundle={bundle} />
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
  const selected = bundle.objects.find((item) => item.local_id === localId);
  const record = objectScene(scene.data, bundle, localId, time);
  return (
    <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
      <div className="space-y-1">
        {bundle.objects.map((object) => (
          <button key={object.id} className={`btn block w-full text-left ${localId === object.local_id ? "primary" : ""}`} onClick={() => setLocalId(object.local_id)}>{labeledId(object.local_id, object.label)}</button>
        ))}
      </div>
      <div className="space-y-3">
        <h2 className="text-lg">State history</h2>
        {states.map((state) => (
          <button key={state.id} className="panel block w-full p-3 text-left text-sm" onClick={() => useInspector.getState().setTime(state.time_start)}>
            <div className="mono text-xs text-[var(--muted)]">{formatTime(state.time_start)}–{formatTime(state.time_end)}</div>
            <div>{humanize(state.state_label)}</div>
            <div className="text-[var(--muted)]">{state.description}</div>
            <span className={`tag ${evidenceClass(state.evidence_class)}`}>{humanize(state.evidence_class)}</span>
          </button>
        ))}
        <h3 className="text-sm">Transitions</h3>
        {transitions.map((item) => (
          <div key={item.id} className="text-sm">{formatTime(item.timestamp)} · {humanize(item.from_state)} → {humanize(item.to_state)} · <span className={`tag ${evidenceClass(item.evidence_class)}`}>{humanize(item.evidence_class)}</span></div>
        ))}
        <h3 className="text-sm">Relationships</h3>
        {relations.map((item) => (
          <div key={item.id} className="text-sm">{humanize(item.subject_local_id)} {humanize(item.kind)} {humanize(item.other_local_id ?? "")} · {item.description}</div>
        ))}
        <div className="panel p-3 text-sm">
          <div className="mb-2 text-xs uppercase tracking-wide text-[var(--muted)]">
            {selected ? labeledId(selected.local_id, selected.label) : "Object"} · scene at {time.toFixed(2)}s
          </div>
          <pre className="mono max-h-64 overflow-auto text-xs">{JSON.stringify(record, null, 2)}</pre>
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
        <select className="field w-auto" value={from} onChange={(event) => setFrom(event.target.value)}>{bundle.steps.map((item) => <option key={item.local_id} value={item.local_id}>{humanize(item.local_id)}</option>)}</select>
        <select className="field w-auto" value={to} onChange={(event) => setTo(event.target.value)}>{bundle.steps.map((item) => <option key={item.local_id} value={item.local_id}>{humanize(item.local_id)}</option>)}</select>
        <select className="field w-auto" value={category} onChange={(event) => setCategory(event.target.value)}>
          {["necessary_physical", "observed_temporal", "inferred", "unknown"].map((item) => <option key={item} value={item}>{humanize(item)}</option>)}
        </select>
        <button className="btn" onClick={async () => { await api.addDependency(bundle.run.id, { from_step: from, to_step: to, category, rationale: "Added by the researcher." }); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Add dependency</button>
      </div>
      {step && (
        <div className="panel p-3 text-sm">
          <div className="font-medium">{step.name}</div>
          <p>Completion: {step.completion_condition}</p>
          <p>Observed: {step.observed_result}</p>
          <p className="text-[var(--muted)]">Objects: {step.required_objects.map(humanize).join(", ") || "none"}</p>
        </div>
      )}
      <ul className="space-y-1 text-sm">
        {bundle.dependencies.map((dep) => (
          <li key={dep.id} className="flex items-center justify-between gap-2">
            <span>{humanize(dep.from_step)} → {humanize(dep.to_step)} · {humanize(dep.category)} · {dep.rationale}</span>
            <button className="btn" onClick={async () => { await api.removeDependency(bundle.run.id, dep.from_step, dep.to_step); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Remove</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function StepsPage() {
  const bundle = useBundle();
  const openInspect = useOpenInspect();
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
      <p>{humanize(step.expected_action)}</p>
      <p className="text-sm text-[var(--muted)]">Expected: {step.completion_condition}</p>
      <p className="text-sm">Observed: {step.observed_result}</p>
      <div className="grid gap-2 md:grid-cols-3">
        {[start, (start + end) / 2, end].map((stamp) => {
          const frame = bundle.frames.reduce((best, item) => Math.abs(item.timestamp - stamp) < Math.abs(best.timestamp - stamp) ? item : best, bundle.frames[0]);
          return frame ? <img key={stamp} src={frame.url} alt="" className="panel aspect-video w-full bg-black object-contain" /> : null;
        })}
      </div>
      <div className="flex flex-wrap items-start gap-4">
        {step.required_objects.map((id) => {
          const object = bundle.objects.find((item) => item.local_id === id);
          const thumb = objectThumbnail(object);
          return (
            <div key={id} className="w-28 shrink-0 text-xs">
              {thumb && <img src={thumb} alt="" className="mb-2 h-16 w-16 rounded bg-black object-contain" />}
              <div className="leading-snug">{humanize(object?.label ?? id)}</div>
            </div>
          );
        })}
      </div>
      <button className="btn" type="button" onClick={() => {
        const inspector = useInspector.getState();
        const linked = bundle.actions.find((item) => step.action_local_ids.includes(item.local_id));
        const covering = bundle.actions.find((item) => item.start <= end && item.end >= start);
        inspector.setPlaying(false);
        inspector.setTime(start);
        inspector.selectStep(step.local_id);
        inspector.selectAction((linked ?? covering)?.local_id ?? null);
        inspector.setLoop(end > start ? { start, end } : null);
        openInspect();
      }}>Show on timeline</button>
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
          <div>{humanize(stage.stage)}</div>
          <div>{humanize(stage.status)}</div>
          <div className="text-[var(--muted)]">
            {stage.progress_current}/{stage.progress_total || 0}
            {stage.error ? ` · ${humanize(stage.error)}` : ""}
            {stage.checkpoint?.reason ? ` · ${humanize(String(stage.checkpoint.reason))}` : ""}
          </div>
          {stage.status === "failed" && (
            <button className="btn h-fit self-center" onClick={async () => { await api.retryStage(bundle.run.id, stage.stage); await client.invalidateQueries({ queryKey: ["run", bundle.run.id] }); }}>Retry</button>
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
            <button className="btn" onClick={async () => { const report = await api.evaluate(bundle.run.id, item.id, { action_iou: iou }); setResult(report.metrics); }}>{humanizeText(item.name)}</button>
          </li>
        ))}
      </ul>
      {result && <IdentityReport report={result} runId={bundle.run.id} />}
      {result && <pre className="panel mono overflow-auto p-3 text-xs">{JSON.stringify(result, null, 2)}</pre>}
    </div>
  );
}

function IdentityReport({ report, runId }: { report: Record<string, unknown>; runId: string }) {
  const identity = report.identity as Record<string, unknown> | undefined;
  const navigate = useNavigate();
  const setTime = useInspector((state) => state.setTime);
  if (!identity) return null;
  const failures = Array.isArray(identity.failures) ? identity.failures as { kind?: string; timestamp?: number | null; detail?: string }[] : [];
  return (
    <section className="panel space-y-2 p-3 text-sm">
      <h2 className="text-base">Identity</h2>
      {identity.evaluated === false ? (
        <p className="text-[var(--muted)]">{String(identity.reason ?? "Identity accuracy was withheld.")}</p>
      ) : (
        <p>
          IDF1 {numberText(identity.idf1)} · switches {String(identity.identity_switches ?? 0)} · false merges {String(identity.false_merges ?? 0)} · coverage {numberText(identity.tracking_coverage)} · re-id {numberText(identity.reid_accuracy)} · abstention {numberText(identity.abstention_rate)}
        </p>
      )}
      <ul className="space-y-1">
        {failures.map((item, index) => (
          <li key={`${item.kind}-${item.timestamp}-${index}`}>
            <button
              className="btn"
              disabled={item.timestamp == null}
              onClick={() => {
                if (item.timestamp == null) return;
                setTime(item.timestamp);
                navigate(`/runs/${runId}`);
              }}
            >
              {humanize(item.kind ?? "failure")}{item.timestamp == null ? "" : ` · ${item.timestamp.toFixed(2)}s`}
            </button>
            {item.detail ? <span className="ml-2 text-xs text-[var(--muted)]">{item.detail}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function numberText(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

function requestCost(row: Record<string, unknown>): string {
  if (row.status !== "completed") return "";
  const usage = (row.usage ?? {}) as { cost_usd?: number; price_known?: boolean; input_tokens?: number; output_tokens?: number };
  const money = formatUsd(Number(usage.cost_usd ?? 0), Boolean(usage.price_known));
  return ` · ${usage.input_tokens ?? 0} in / ${usage.output_tokens ?? 0} out · ${money}`;
}

export function LogsPage() {
  const bundle = useBundle();
  const rows = useQuery({ queryKey: ["requests", bundle.run.id], queryFn: () => api.requests(bundle.run.id) });
  return (
    <div className="space-y-3">
      <h2 className="text-lg">Requests and raw responses</h2>
      {bundle.validation?.length ? (
        <ul className="space-y-1 text-sm">
          {bundle.validation.map((issue, index) => <li key={index} className={issue.severity === "error" ? "text-[var(--rose)]" : "text-[var(--amber)]"}>{humanize(issue.severity)} · {humanize(issue.code)} · {issue.message}</li>)}
        </ul>
      ) : <p className="text-sm text-[var(--muted)]">No validation issues on the active document.</p>}
      {rows.data?.length ? (
        <ul className="min-w-0 space-y-3">
          {rows.data.map((row) => {
            const lines = formatLogText(String(row.response_text || row.error || "")).split("\n");
            return (
              <li key={String(row.id)} className="min-w-0">
                <details className="panel min-w-0 p-3 text-sm">
                  <summary className="cursor-pointer">{humanize(String(row.stage))} · {humanize(String(row.model_id))} · {humanize(String(row.status))} · {String(row.latency_ms)} ms{requestCost(row)}</summary>
                  {lines.length === 1 && lines[0] === "" ? (
                    <p className="mt-2 text-xs text-[var(--muted)]">No response body.</p>
                  ) : (
                    <div className="log-body mono" role="log">
                      {lines.map((line, index) => (
                        <div key={index}>{line || "\u00a0"}</div>
                      ))}
                    </div>
                  )}
                </details>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-[var(--muted)]">This run has not recorded a model request. Fixture and deterministic analyses are stored as documents, not provider calls.</p>
      )}
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
        {["pending", "running", "completed", "failed", "canceled"].map((item) => <option key={item} value={item}>{humanize(item)}</option>)}
      </select>
      <div className="space-y-2">
        {runs.data?.map((run) => {
          const when = formatWhen(run.created_at);
          return (
            <a key={run.id} href={`/runs/${run.id}`} className="panel flex items-baseline justify-between gap-4 p-3 text-sm">
              <span className="min-w-0">
                {humanizeText(run.name)} · {humanize(run.status)} · {humanize(run.provider_id)}/{humanize(run.model_id)} · {formatUsd(run.cost_usd)} · {run.object_count} objects · {run.action_count} actions
              </span>
              {when && <time className="mono shrink-0 text-xs text-[var(--muted)]" dateTime={run.created_at}>{when}</time>}
            </a>
          );
        })}
      </div>
    </div>
  );
}


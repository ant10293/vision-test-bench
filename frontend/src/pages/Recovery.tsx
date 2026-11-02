import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { PreviewNav } from "../components/AssetPreview";
import { Timeline } from "../components/Timeline";
import { api, formatTime } from "../lib/api";
import { useOptionalBundle } from "../lib/bundleContext";
import { humanize } from "../lib/humanize";
import {
  canApproveLive,
  dragBox,
  LAYERS,
  layerBoxes,
  nextStep,
  stepTimes,
  usage,
  type RecoveryExperiment,
  type RecoveryLayer,
  type RecoveryPlan,
  type RecoveryProposal,
  type RecoverySeed,
  type RecoveryTarget,
  type SeedPoint,
} from "../lib/recovery";
import type { BBox, Bundle } from "../lib/types";
import { useInspector } from "../stores/ui";

type Tool = "none" | "box" | "positive" | "negative";

const ALL_LAYERS: Record<RecoveryLayer, boolean> = { vlm: true, tracker: false, sam: false, recovered: true, canonical: false };
const RUNNING_POLL_MS = 1500;

export function RecoveryPage() {
  const bundle = useOptionalBundle();
  if (!bundle) return <p className="text-sm text-[var(--muted)]">Loading the analysis…</p>;
  return <RecoveryWorkspace bundle={bundle} />;
}

function RecoveryWorkspace({ bundle }: { bundle: Bundle }) {
  const runId = bundle.run.id;
  const client = useQueryClient();
  const time = useInspector((state) => state.time);
  const seek = useInspector((state) => state.setTime);
  const [layers, setLayers] = useState(ALL_LAYERS);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [experimentId, setExperimentId] = useState("");
  const [error, setError] = useState("");
  const [tool, setTool] = useState<Tool>("none");
  const [objectId, setObjectId] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [direction, setDirection] = useState("auto");
  const [drawnBox, setDrawnBox] = useState<BBox | null>(null);
  const [points, setPoints] = useState<SeedPoint[]>([]);
  const [seedTime, setSeedTime] = useState<number | null>(null);
  const [existingSeed, setExistingSeed] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [approved, setApproved] = useState(false);
  const [provisional, setProvisional] = useState(false);
  const [maxRequests, setMaxRequests] = useState("2");
  const [maxFrames, setMaxFrames] = useState("300");
  const [maxBilled, setMaxBilled] = useState("600");
  const [padding, setPadding] = useState("1");
  const [budget, setBudget] = useState("1");
  const candidates = useQuery({ queryKey: ["recovery-candidates", runId], queryFn: () => api.recoveryCandidates(runId) });
  const experiment = useQuery({
    queryKey: ["recovery-experiment", experimentId],
    queryFn: () => api.recoveryExperiment(experimentId),
    enabled: Boolean(experimentId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? RUNNING_POLL_MS : false),
  });
  const history = useQuery({ queryKey: ["recovery-experiments", runId], queryFn: () => api.recoveryExperiments(runId) });
  const data = experiment.data ?? null;
  const proposals = useMemo(() => (data?.proposals ?? []).filter((item) => item.current), [data]);

  function publish(result: RecoveryExperiment) {
    client.setQueryData(["recovery-experiment", result.id], result);
    setExperimentId(result.id);
    void client.invalidateQueries({ queryKey: ["recovery-experiments", runId] });
  }

  const act = useMutation({
    mutationFn: (work: () => Promise<RecoveryExperiment>) => work(),
    onMutate: () => setError(""),
    onSuccess: (result) => {
      publish(result);
      setApproved(false);
      void client.invalidateQueries({ queryKey: ["run", runId] });
      void client.invalidateQueries({ queryKey: ["recovery-candidates", runId] });
    },
    onError: (err: Error) => setError(err.message),
  });

  function limits() {
    return {
      max_requests: Number(maxRequests),
      max_frames_per_request: Number(maxFrames),
      max_total_billed_frames: Number(maxBilled),
      padding_sec: Number(padding),
      cost_budget_usd: Number(budget),
    };
  }

  function manualTarget() {
    const seed = drawnBox || points.length
      ? { timestamp: seedTime ?? time, bbox: drawnBox, points }
      : undefined;
    return {
      object_local_id: objectId,
      time_start: Number(start),
      time_end: Number(end),
      direction,
      ...(seed ? { seed } : {}),
      ...(existingSeed && !seed ? { seed_id: existingSeed, confirm_seed: confirmed } : {}),
    };
  }

  const manualReady = Boolean(objectId && start !== "" && end !== "" && Number(end) >= Number(start));

  function createAndPlan() {
    act.mutate(async () => {
      const created = await api.createRecovery(runId, {
        target_ids: [...selected],
        manual_targets: manualReady ? [manualTarget()] : [],
        limits: limits(),
      });
      return api.recoveryDryRun(created.id);
    });
  }

  function onCanvasEvent(box: BBox | null, point: SeedPoint | null) {
    setSeedTime(time);
    setExistingSeed("");
    if (box) setDrawnBox(box);
    if (point) setPoints((current) => [...current, point]);
  }

  const objectSeeds = (candidates.data?.seeds ?? []).filter((item) => item.object_local_id === objectId);
  const boxes = layerBoxes(bundle, proposals, time, layers);
  const steps = useMemo(() => stepTimes(bundle, proposals), [bundle, proposals]);

  function step(direction: 1 | -1) {
    const next = nextStep(steps, useInspector.getState().time, direction);
    if (next != null) seek(next);
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.key === "ArrowRight") step(1);
      else if (event.key === "ArrowLeft") step(-1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="space-y-4" data-testid="recovery-page">
      <section className="panel space-y-1 p-4 text-sm">
        <h2 className="text-base">Targeted object recovery</h2>
        <p className="text-xs text-[var(--muted)]">
          This works on the saved analysis and never calls the language model or reruns a stage. A dry run and a replay make no provider calls. A live run sends only SAM requests you approve. Nothing changes in the analysis until you accept a proposal, and accepting creates a new revision.
        </p>
      </section>
      <div className="grid min-w-0 gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="min-w-0 space-y-3">
          <RecoveryStage bundle={bundle} boxes={boxes} tool={tool} time={time} onStep={step} drawnBox={drawnBox} points={points} onDraw={onCanvasEvent} />
          <Timeline bundle={bundle} />
          <LayerToggles layers={layers} onChange={setLayers} />
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="mono text-[var(--muted)]">{time.toFixed(3)}s</span>
            <span className="text-xs text-[var(--muted)]">Arrows step between frames</span>
            {(["box", "positive", "negative"] as const).map((item) => (
              <button key={item} className="btn" aria-pressed={tool === item} onClick={() => setTool(tool === item ? "none" : item)}>
                {item === "box" ? "Draw seed box" : item === "positive" ? "Add positive point" : "Add negative point"}
              </button>
            ))}
            <button className="btn" disabled={!drawnBox && !points.length} onClick={() => { setDrawnBox(null); setPoints([]); setSeedTime(null); }}>Clear seed</button>
          </div>
          <BoxInspector boxes={boxes} />
          <ResultsPanel data={data} provisional={provisional} onProvisional={setProvisional} onSeek={seek} onDecide={(id, decision) => act.mutate(() => api.decideRecovery(experimentId, { decision, proposal_ids: [id], include_provisional: provisional }))} />
        </div>
        <div className="min-w-0 space-y-3">
          <CandidatePanel
            targets={candidates.data?.targets ?? []}
            pending={candidates.isPending}
            selected={selected}
            onToggle={(id) => setSelected((current) => {
              const next = new Set(current);
              if (next.has(id)) next.delete(id); else next.add(id);
              return next;
            })}
            onSeek={seek}
          />
          <section className="panel space-y-2 p-4 text-sm">
            <h3 className="text-sm">Manual target</h3>
            <label className="field-label"><span>Object</span>
              <select className="field" value={objectId} onChange={(event) => { setObjectId(event.target.value); setExistingSeed(""); }}>
                <option value="">Choose an object</option>
                {candidates.data?.objects.map((item) => <option key={item.local_id} value={item.local_id}>{humanize(item.local_id)} · {item.label}</option>)}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="field-label"><span>Start (s)</span>
                <input className="field stretch" value={start} onChange={(event) => setStart(event.target.value)} />
              </label>
              <label className="field-label"><span>End (s)</span>
                <input className="field stretch" value={end} onChange={(event) => setEnd(event.target.value)} />
              </label>
            </div>
            <div className="flex gap-2">
              <button className="btn" onClick={() => setStart(time.toFixed(3))}>Start at playhead</button>
              <button className="btn" onClick={() => setEnd(time.toFixed(3))}>End at playhead</button>
            </div>
            <label className="field-label"><span>Propagation</span>
              <select className="field" value={direction} onChange={(event) => setDirection(event.target.value)}>
                <option value="auto">Automatic, from the seed's position</option>
                <option value="forward">Forward</option>
                <option value="backward">Backward</option>
                <option value="both">Both</option>
              </select>
            </label>
            {objectId && <SeedPicker seeds={objectSeeds} value={existingSeed} onChange={(id) => { setExistingSeed(id); setDrawnBox(null); setPoints([]); setConfirmed(false); }} onSeek={seek} confirmed={confirmed} onConfirmed={setConfirmed} />}
            {(drawnBox || points.length > 0) && (
              <p className="text-xs text-[var(--muted)]">Seed drawn on the frame at {(seedTime ?? time).toFixed(3)}s{drawnBox ? " with a box" : ""}{points.length ? ` and ${points.length} ${points.length === 1 ? "point" : "points"}` : ""}. Your drawn seed overrides automatic selection.</p>
            )}
          </section>
          <section className="panel space-y-2 p-4 text-sm">
            <h3 className="text-sm">Limits</h3>
            <div className="grid grid-cols-2 gap-2">
              <Limit label="Provider requests" value={maxRequests} onChange={setMaxRequests} />
              <Limit label="Frames per request" value={maxFrames} onChange={setMaxFrames} />
              <Limit label="Total billed frames" value={maxBilled} onChange={setMaxBilled} />
              <Limit label="Padding (s)" value={padding} onChange={setPadding} />
              <Limit label="Cost budget (USD)" value={budget} onChange={setBudget} />
            </div>
            <button className="btn primary" disabled={act.isPending || (!selected.size && !manualReady)} onClick={createAndPlan}>
              Create and dry run
            </button>
            <p className="text-xs text-[var(--muted)]">A dry run makes no remote calls. It shows the plan and the estimate.</p>
            {(history.data?.length ?? 0) > 0 && (
              <label className="field-label"><span>Earlier experiments</span>
                <select className="field" value={experimentId} onChange={(event) => setExperimentId(event.target.value)}>
                  <option value="">Choose an experiment</option>
                  {history.data?.map((item) => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · {humanize(item.status)} · {item.targets.length} targets</option>)}
                </select>
              </label>
            )}
          </section>
          {error && <p className="text-sm text-[var(--rose)]" role="alert">{error}</p>}
          {data && (
            <PlanPanel
              data={data}
              approved={approved}
              onApproved={setApproved}
              pending={act.isPending}
              onDryRun={() => act.mutate(() => api.recoveryDryRun(data.id))}
              onReplay={() => act.mutate(() => api.recoveryReplay(data.id))}
              onLive={(hash) => act.mutate(() => api.recoveryLive(data.id, { confirm: true, plan_hash: hash }))}
              onResubmit={(hash, keys) => act.mutate(() => api.recoveryLive(data.id, { confirm: true, plan_hash: hash, resubmit_keys: keys }))}
              onCancel={() => act.mutate(() => api.recoveryCancel(data.id))}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function Limit({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="field-label"><span>{label}</span>
      <input className="field stretch" inputMode="decimal" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function RecoveryStage({
  bundle,
  boxes,
  tool,
  time,
  onStep,
  drawnBox,
  points,
  onDraw,
}: {
  bundle: Bundle;
  boxes: ReturnType<typeof layerBoxes>;
  tool: Tool;
  time: number;
  onStep: (direction: 1 | -1) => void;
  drawnBox: BBox | null;
  points: SeedPoint[];
  onDraw: (box: BBox | null, point: SeedPoint | null) => void;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  // The overlay draws in on-screen pixels, so strokes and labels keep their size on a 4K source.
  const [size, setSize] = useState({ x: 0, y: 0, w: 1, h: 1 });
  const [drag, setDrag] = useState<{ x: number; y: number; ex: number; ey: number } | null>(null);
  useEffect(() => {
    const frame = frameRef.current;
    const video = videoRef.current;
    if (!frame || !video) return;
    const apply = () => {
      if (!video.videoWidth) return;
      const rect = frame.getBoundingClientRect();
      const scale = Math.min(rect.width / video.videoWidth, rect.height / video.videoHeight);
      const w = video.videoWidth * scale;
      const h = video.videoHeight * scale;
      setSize({ x: (rect.width - w) / 2, y: (rect.height - h) / 2, w, h });
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(frame);
    video.addEventListener("loadedmetadata", apply);
    return () => {
      observer.disconnect();
      video.removeEventListener("loadedmetadata", apply);
    };
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const show = () => {
      if (Number.isFinite(time) && Math.abs(video.currentTime - time) > 0.001) video.currentTime = time;
    };
    show();
    video.addEventListener("loadedmetadata", show);
    return () => video.removeEventListener("loadedmetadata", show);
  }, [time]);

  function normalized(event: PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height };
  }

  const preview = drag ? dragBox(drag.x, drag.y, drag.ex, drag.ey) : null;
  const shown = preview ?? drawnBox;
  const color = (layer: RecoveryLayer) => LAYERS.find((item) => item.key === layer)?.color ?? "#fff";
  return (
    <div>
      <div ref={frameRef} className="stage-frame rounded-lg bg-black">
        <video ref={videoRef} src={bundle.video?.playback_url ?? ""} muted preload="auto" />
        <div
          className="absolute"
          data-testid="recovery-overlay"
          style={{ left: size.x, top: size.y, width: size.w, height: size.h, pointerEvents: tool === "none" ? "none" : "auto", cursor: tool === "none" ? "default" : "crosshair", touchAction: "none" }}
          onPointerDown={(event) => {
            const at = normalized(event);
            if (tool === "box") {
              event.currentTarget.setPointerCapture(event.pointerId);
              setDrag({ x: at.x, y: at.y, ex: at.x, ey: at.y });
            } else if (tool === "positive" || tool === "negative") {
              onDraw(null, { x: at.x, y: at.y, negative: tool === "negative" });
            }
          }}
          onPointerMove={(event) => {
            if (!drag) return;
            const at = normalized(event);
            setDrag({ ...drag, ex: at.x, ey: at.y });
          }}
          onPointerUp={() => {
            if (preview) onDraw(preview, null);
            setDrag(null);
          }}
        >
          <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${size.w} ${size.h}`}>
            {boxes.map((box) => (
              <g key={box.key} data-layer={box.layer}>
                <rect
                  x={box.bbox.x * size.w}
                  y={box.bbox.y * size.h}
                  width={box.bbox.w * size.w}
                  height={box.bbox.h * size.h}
                  fill="none"
                  stroke={color(box.layer)}
                  strokeWidth={2}
                  strokeDasharray={box.dashed ? "6 4" : undefined}
                />
                <text x={box.bbox.x * size.w} y={Math.max(13, box.bbox.y * size.h - 4)} fill={color(box.layer)} fontSize={13} fontFamily="IBM Plex Sans, sans-serif">
                  {humanize(box.objectId)}
                </text>
              </g>
            ))}
            {shown && (
              <rect data-testid="drawn-seed" x={shown.x * size.w} y={shown.y * size.h} width={shown.w * size.w} height={shown.h * size.h} fill="#ffffff22" stroke="#fff" strokeWidth={2} />
            )}
            {points.map((point, index) => (
              <circle key={index} cx={point.x * size.w} cy={point.y * size.h} r={5} fill={point.negative ? "#e07070" : "#7dcea0"} stroke="#000" />
            ))}
          </svg>
        </div>
        <PreviewNav direction="prev" label="Previous frame" onClick={() => onStep(-1)} />
        <PreviewNav direction="next" label="Next frame" onClick={() => onStep(1)} />
      </div>
      <span className="sr-only">Playhead at {time.toFixed(3)} seconds</span>
    </div>
  );
}

function LayerToggles({ layers, onChange }: { layers: Record<RecoveryLayer, boolean>; onChange: (layers: Record<RecoveryLayer, boolean>) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" role="group" aria-label="Box layers">
      {LAYERS.map((layer) => (
        <button
          key={layer.key}
          className="btn"
          aria-pressed={layers[layer.key]}
          style={layers[layer.key] ? { borderColor: layer.color, color: layer.color } : undefined}
          onClick={() => onChange({ ...layers, [layer.key]: !layers[layer.key] })}
        >
          {layers[layer.key] ? "● " : "○ "}{layer.label}
        </button>
      ))}
      <span className="text-xs text-[var(--muted)]">Dashed boxes are estimates, conflicts, or unconfirmed rows, not direct observations.</span>
    </div>
  );
}

/** Fixed height: this list changes with the playhead, and a page that resized here would move the scroll position. */
function BoxInspector({ boxes }: { boxes: ReturnType<typeof layerBoxes> }) {
  return (
    <section className="panel h-40 space-y-1 overflow-auto p-3 text-xs" data-testid="box-inspector">
      <h3 className="text-sm">Boxes near the playhead</h3>
      {boxes.length === 0 ? (
        <p className="text-[var(--muted)]">No selected layer has a box near the playhead. A missing box is unobserved, not absence.</p>
      ) : (
        <ul className="space-y-1">
          {boxes.map((box) => (
            <li key={box.key}>
              <span className="font-medium">{LAYERS.find((item) => item.key === box.layer)?.label}</span> · {humanize(box.objectId)} · {formatTime(box.timestamp)} · {box.details.join(" · ")}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CandidatePanel({
  targets,
  pending,
  selected,
  onToggle,
  onSeek,
}: {
  targets: RecoveryTarget[];
  pending: boolean;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onSeek: (time: number) => void;
}) {
  return (
    <section className="panel space-y-2 p-4 text-sm" data-testid="recovery-candidates">
      <h3 className="text-sm">Problem intervals</h3>
      {pending && <p className="text-xs text-[var(--muted)]">Looking through the saved analysis…</p>}
      {!pending && targets.length === 0 && (
        <p className="text-xs text-[var(--muted)]">No interval was flagged. You can still add a manual target. This does not mean every box is correct.</p>
      )}
      <ul className="max-h-96 space-y-2 overflow-auto">
        {targets.map((target) => (
          <li key={target.target_id} className="rounded border border-[var(--line)] p-2">
            <label className="flex items-start gap-2">
              <input type="checkbox" disabled={!target.recoverable} checked={selected.has(target.target_id)} onChange={() => onToggle(target.target_id)} />
              <span className="min-w-0">
                <span className="font-medium">{humanize(target.object_local_id)}</span> · {target.label}
                <span className="block text-xs text-[var(--muted)]">
                  {target.reasons.map(humanize).join(", ")} · {humanize(target.state)} · {humanize(target.difficulty ?? "")} · {target.seeds?.length ?? 0} usable seeds
                </span>
                {target.support && <span className="block text-xs text-[var(--muted)]">{target.support.observations_in_interval} stored boxes in the interval, {target.support.measured_in_interval} measured</span>}
                {target.details.slice(0, 2).map((detail) => <span key={detail} className="block text-xs">{detail}</span>)}
                {target.blocker && <span className="block text-xs text-[var(--amber)]">{target.blocker}</span>}
              </span>
            </label>
            <button className="btn mt-1" onClick={() => onSeek(target.time_start)}>Jump to {formatTime(target.time_start)}–{formatTime(target.time_end)}</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SeedPicker({
  seeds,
  value,
  onChange,
  onSeek,
  confirmed,
  onConfirmed,
}: {
  seeds: RecoverySeed[];
  value: string;
  onChange: (id: string) => void;
  onSeek: (time: number) => void;
  confirmed: boolean;
  onConfirmed: (value: boolean) => void;
}) {
  const chosen = seeds.find((item) => item.seed_id === value);
  if (!seeds.length) return <p className="text-xs text-[var(--muted)]">No stored box can seed this object. Draw one on the frame.</p>;
  return (
    <div className="space-y-1">
      <label className="field-label"><span>Existing box</span>
        <select className="field" value={value} onChange={(event) => { onChange(event.target.value); const seed = seeds.find((item) => item.seed_id === event.target.value); if (seed) onSeek(seed.timestamp); }}>
          <option value="">Automatic, or draw one</option>
          {seeds.slice(0, 80).map((seed) => <option key={seed.seed_id} value={seed.seed_id}>{formatTime(seed.timestamp)} · {humanize(seed.trust)} · {seed.source}</option>)}
        </select>
      </label>
      {chosen && (
        <div className="space-y-1 text-xs">
          <p>{chosen.explanation}</p>
          {chosen.checks.filter((item) => !item.passed).map((item) => <p key={item.check} className="text-[var(--amber)]">{humanize(item.check)}: {item.detail}</p>)}
          {chosen.trust === "candidate" && (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={confirmed} onChange={(event) => onConfirmed(event.target.checked)} />
              I looked at this box and confirm it marks this object.
            </label>
          )}
        </div>
      )}
    </div>
  );
}

function PlanPanel({
  data,
  approved,
  onApproved,
  pending,
  onDryRun,
  onReplay,
  onLive,
  onResubmit,
  onCancel,
}: {
  data: RecoveryExperiment;
  approved: boolean;
  onApproved: (value: boolean) => void;
  pending: boolean;
  onDryRun: () => void;
  onReplay: () => void;
  onLive: (hash: string) => void;
  onResubmit: (hash: string, keys: string[]) => void;
  onCancel: () => void;
}) {
  const plan: RecoveryPlan | null = data.plan;
  const running = data.status === "running";
  const uncertain = data.requests.filter((item) => item.submission === "submitted" && item.status !== "running").map((item) => item.cache_key);
  return (
    <section className="panel space-y-2 p-4 text-sm" data-testid="recovery-plan">
      <h3 className="text-sm">Experiment {data.id.slice(0, 8)} · {humanize(data.status)}{data.mode ? ` · ${humanize(data.mode)}` : ""}</h3>
      {data.error && (
        <p className="text-xs text-[var(--amber)]" role="status">
          {data.error} {data.remote_submitted ? "A remote call was submitted for this experiment." : "No remote call was submitted."}
        </p>
      )}
      {plan ? (
        <>
          <p className="text-xs">{usage(plan.totals)}.</p>
          <p className="text-xs text-[var(--muted)]">{plan.totals.cost_note}</p>
          {plan.totals.cached_requests > 0 && <p className="text-xs text-[var(--muted)]">{plan.totals.cached_requests} planned {plan.totals.cached_requests === 1 ? "request has" : "requests have"} a stored response and will not be sent.</p>}
          {plan.notes.map((note) => <p key={note} className="text-xs text-[var(--muted)]">{note}</p>)}
          <ul className="space-y-2">
            {plan.requests.map((spec) => (
              <li key={spec.key} className="rounded border border-[var(--line)] p-2 text-xs">
                <div>{humanize(spec.direction)} · {formatTime(spec.source_start)}–{formatTime(spec.source_end)} · {spec.frame_times.length} frames · about {spec.billed_frames} billed{spec.cached ? " · stored response" : ""}</div>
                {spec.objects.map((item) => (
                  <div key={`${item.target_id}-${item.seed.seed_id}`}>
                    SAM id {item.sam_object_id} = {humanize(item.object_local_id)}, clip frame {item.seed_frame_index} (source {item.seed.timestamp.toFixed(3)}s), {humanize(item.seed.trust)}. {item.seed.explanation}
                  </div>
                ))}
                <code className="mono mt-1 block break-all text-[var(--muted)]">{spec.prompt}</code>
              </li>
            ))}
          </ul>
          {plan.blocked.map((item) => (
            <p key={`${item.target_id}-${item.reason}`} className="text-xs text-[var(--amber)]">{humanize(item.object_local_id)} {formatTime(item.time_start)}–{formatTime(item.time_end)} cannot run. {item.reason}</p>
          ))}
          {plan.violations.map((item) => <p key={item} className="text-xs text-[var(--rose)]">{item}</p>)}
        </>
      ) : <p className="text-xs text-[var(--muted)]">No plan yet.</p>}
      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={pending || running} onClick={onDryRun}>Dry run</button>
        <button className="btn" disabled={pending || running} onClick={onReplay}>Replay stored responses</button>
        {running && <button className="btn" disabled={data.cancel_requested} onClick={onCancel}>{data.cancel_requested ? "Canceling…" : "Cancel"}</button>}
      </div>
      {plan && canApproveLive(plan) && !running && (
        <div className="space-y-1 rounded border border-[var(--line)] p-2">
          <label className="flex items-start gap-2 text-xs">
            <input type="checkbox" checked={approved} onChange={(event) => onApproved(event.target.checked)} />
            I approve {usage(plan.totals)}. These are SAM requests only and may be billed by Meta.
          </label>
          <button className="btn primary" disabled={!approved || pending} onClick={() => onLive(plan.plan_hash)}>Run live SAM recovery</button>
          {uncertain.length > 0 && (
            <button className="btn" disabled={!approved || pending} onClick={() => onResubmit(plan.plan_hash, uncertain)}>
              Also resend {uncertain.length} {uncertain.length === 1 ? "request" : "requests"} that may have been billed
            </button>
          )}
        </div>
      )}
      {data.requests.length > 0 && (
        <ul className="space-y-1 text-xs text-[var(--muted)]">
          {data.requests.map((item) => (
            <li key={item.cache_key}>{item.cache_key.slice(0, 8)} · {humanize(item.status)} · {humanize(item.submission)}{item.source ? ` · ${item.source}` : ""}{item.latency_ms ? ` · ${item.latency_ms} ms` : ""}{item.error ? ` · ${item.error}` : ""}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ResultsPanel({
  data,
  provisional,
  onProvisional,
  onSeek,
  onDecide,
}: {
  data: RecoveryExperiment | null;
  provisional: boolean;
  onProvisional: (value: boolean) => void;
  onSeek: (time: number) => void;
  onDecide: (id: string, decision: "accepted" | "rejected") => void;
}) {
  const current = (data?.proposals ?? []).filter((item) => item.current);
  if (!data || !current.length) return null;
  return (
    <section className="panel space-y-3 p-4 text-sm" data-testid="recovery-results">
      <h3 className="text-sm">Results awaiting review</h3>
      {data.recorded_cost_usd != null && <p className="text-xs text-[var(--muted)]">Recorded estimate ${data.recorded_cost_usd.toFixed(4)}. Meta's invoice decides the real charge.</p>}
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={provisional} onChange={(event) => onProvisional(event.target.checked)} />
        Also add provisional boxes when accepting. They stay non-canonical.
      </label>
      {current.map((proposal) => <ProposalCard key={proposal.id} proposal={proposal} onSeek={onSeek} onDecide={onDecide} />)}
    </section>
  );
}

function ProposalCard({
  proposal,
  onSeek,
  onDecide,
}: {
  proposal: RecoveryProposal;
  onSeek: (time: number) => void;
  onDecide: (id: string, decision: "accepted" | "rejected") => void;
}) {
  const metrics = proposal.metrics;
  return (
    <div className="space-y-1 rounded border border-[var(--line)] p-2 text-xs">
      <div className="font-medium">
        <button onClick={() => onSeek(proposal.time_start)}>{humanize(proposal.object_local_id)} · {formatTime(proposal.time_start)}–{formatTime(proposal.time_end)}</button> · {humanize(proposal.decision)}
      </div>
      {metrics.synthetic_response && <p className="text-[var(--amber)]">Built from a synthetic offline response. It only exercises the plumbing and says nothing about SAM's accuracy.</p>}
      <table className="w-full text-left">
        <thead className="text-[var(--muted)]">
          <tr><th className="font-normal">Previously missing</th><th className="font-normal">New supported</th><th className="font-normal">Supported</th><th className="font-normal">Provisional</th><th className="font-normal">Conflicting</th><th className="font-normal">Rejected</th><th className="font-normal">Unobserved</th></tr>
        </thead>
        <tbody>
          <tr>
            <td>{metrics.previously_missing_frames}</td>
            <td>{metrics.new_supported_frames}</td>
            <td>{metrics.counts.supported}</td>
            <td>{metrics.counts.provisional}</td>
            <td>{metrics.counts.conflicting}</td>
            <td>{metrics.counts.rejected}</td>
            <td>{metrics.counts.unobserved_frames}</td>
          </tr>
        </tbody>
      </table>
      {metrics.remaining_gaps.slice(0, 6).map((gap) => (
        <p key={gap.start}><button onClick={() => onSeek(gap.start)}>{formatTime(gap.start)}–{formatTime(gap.end)}</button> remains unobserved ({gap.frames} frames). This is not evidence of absence.</p>
      ))}
      {metrics.identity.map((item, index) => (
        <p key={index} className="text-[var(--muted)]">Identity: SAM id {item.sam_object_id}, {humanize(item.association)}, seed {humanize(item.seed_trust)}. {item.note}</p>
      ))}
      <p className="text-[var(--muted)]">
        {metrics.usage.requests} {metrics.usage.requests === 1 ? "request" : "requests"} · about {metrics.usage.billed_frames} billed frames · {metrics.usage.latency_ms} ms · {metrics.usage.input_tokens + metrics.usage.output_tokens} tokens
      </p>
      <p className="text-[var(--muted)]">
        {metrics.human_box_overlap ? `Mean overlap with human boxes ${metrics.human_box_overlap.mean_iou.toFixed(2)} on ${metrics.human_box_overlap.frames} frames. ` : ""}{metrics.accuracy_note}
      </p>
      {proposal.decision === "pending" ? (
        <div className="flex gap-2">
          <button className="btn primary" onClick={() => onDecide(proposal.id, "accepted")}>Accept as new revision</button>
          <button className="btn" onClick={() => onDecide(proposal.id, "rejected")}>Reject</button>
        </div>
      ) : proposal.applied_revision_id ? <p className="text-[var(--muted)]">Applied to a new revision. The earlier revision is unchanged.</p> : null}
    </div>
  );
}

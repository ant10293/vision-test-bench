import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { PreviewNav } from "../components/AssetPreview";
import { Bone, FieldBone } from "../components/Skeleton";
import { StageZoomControl, useStageZoom } from "../components/StageZoom";
import { api, formatTime, formatWhen } from "../lib/api";
import { useOptionalBundle } from "../lib/bundleContext";
import { humanize } from "../lib/humanize";
import {
  boxAt,
  dragBox,
  LANE_COLORS,
  laneSpans,
  LAYERS,
  layerBoxes,
  manualTargets,
  nextStep,
  placeSeed,
  runCoverage,
  stepTimes,
  trackRange,
  usage,
  type RecoveryExperiment,
  type RecoveryLayer,
  type RecoveryProposal,
  type RecoveryTarget,
  type SeedDraft,
  type TrackDirection,
} from "../lib/recovery";
import type { BBox, Bundle } from "../lib/types";
import { useInspector } from "../stores/ui";

type PointTool = "none" | "positive" | "negative";

const START_LAYERS: Record<RecoveryLayer, boolean> = { vlm: true, tracker: false, sam: false, recovered: true, canonical: true };
const RUNNING_POLL_MS = 1500;
const NEAR_SEC = 0.45;
const INSPECT_LABEL_SIZE = 14;
const MIN_LABEL_SIZE = 10;
const SQUARE_BUTTON = { width: 28, height: 28, minWidth: 28, minHeight: 28, display: "inline-flex", alignItems: "center", justifyContent: "center" };
const OBJECT_PLACEHOLDERS = 3;

export function RecoveryPage() {
  const bundle = useOptionalBundle();
  if (!bundle) return <RecoverySkeleton />;
  return <RecoveryWorkspace bundle={bundle} />;
}

function RecoverySkeleton() {
  const objects = Array.from({ length: OBJECT_PLACEHOLDERS }, (_, index) => index);
  return (
    <div className="space-y-3" aria-busy="true">
      <p className="sr-only">Loading recovery</p>
      <div className="grid min-w-0 gap-3 xl:grid-cols-[minmax(0,1fr)_400px]" aria-hidden="true">
        <section className="panel min-w-0 space-y-2 p-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {objects.map((index) => <span key={index} className="btn"><Bone width="4rem" /></span>)}
          </div>
          <p className="text-xs"><Bone width="80%" /></p>
          <div className="stage-frame overflow-hidden rounded-lg bg-black">
            <Bone className="bone-fill" />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="mono"><Bone width="3rem" /></span>
            {LAYERS.map((layer) => <span key={layer.key} className="btn">{layer.label}</span>)}
            <span className="btn">+ point</span>
            <span className="btn">− point</span>
          </div>
          <div className="space-y-1 border-t border-[var(--line)] pt-2">
            {objects.map((index) => (
              <div key={index} className="grid grid-cols-[110px_1fr] items-center gap-2">
                <span className="text-xs"><Bone width="70%" /></span>
                <div className="h-5 overflow-hidden rounded"><Bone className="bone-fill" /></div>
              </div>
            ))}
          </div>
        </section>
        <div className="min-w-0 space-y-3">
          <section className="panel space-y-2 p-4 text-sm">
            <h2 className="text-base">Seeds</h2>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <FieldBone compact />
              <FieldBone compact />
            </div>
            <p className="text-xs"><Bone width="90%" /></p>
            <div className="border-t border-[var(--line)] pt-3">
              <span className="btn primary"><Bone width="8rem" /></span>
            </div>
          </section>
          <div className="panel p-3 text-sm"><Bone width="10rem" /></div>
        </div>
      </div>
    </div>
  );
}

function RecoveryWorkspace({ bundle }: { bundle: Bundle }) {
  const runId = bundle.run.id;
  const client = useQueryClient();
  const time = useInspector((state) => state.time);
  const seek = useInspector((state) => state.setTime);
  const [objectId, setObjectId] = useState("");
  const [drafts, setDrafts] = useState<SeedDraft[]>([]);
  const [seconds, setSeconds] = useState(2);
  const [direction, setDirection] = useState<TrackDirection>("forward");
  const [pointTool, setPointTool] = useState<PointTool>("none");
  const [layers, setLayers] = useState(START_LAYERS);
  const [experimentId, setExperimentId] = useState("");
  const [plannedKey, setPlannedKey] = useState("");
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");
  const [provisional, setProvisional] = useState(false);
  const [maxRequests, setMaxRequests] = useState("4");
  const [maxFrames, setMaxFrames] = useState("300");
  const [maxBilled, setMaxBilled] = useState("600");
  const [budget, setBudget] = useState("1");
  const candidates = useQuery({ queryKey: ["recovery-candidates", runId], queryFn: () => api.recoveryCandidates(runId) });
  const history = useQuery({ queryKey: ["recovery-experiments", runId], queryFn: () => api.recoveryExperiments(runId) });
  const experiment = useQuery({
    queryKey: ["recovery-experiment", experimentId],
    queryFn: () => api.recoveryExperiment(experimentId),
    enabled: Boolean(experimentId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? RUNNING_POLL_MS : false),
  });
  const data = experiment.data ?? null;
  const proposals = useMemo(() => (data?.proposals ?? []).filter((item) => item.current), [data]);
  const allTargets = candidates.data?.targets ?? [];
  const targets = useMemo(() => allTargets.filter((item) => !item.dismissed), [allTargets]);
  const dismissed = useMemo(() => allTargets.filter((item) => item.dismissed), [allTargets]);
  const objectIds = useMemo(() => new Set(bundle.objects.map((item) => item.local_id)), [bundle.objects]);
  const draftsKey = JSON.stringify([drafts, maxRequests, maxFrames, maxBilled, budget]);
  const plan = data?.plan && plannedKey === draftsKey ? data.plan : null;
  const boxes = layerBoxes(bundle, proposals, time, layers);
  const steps = useMemo(() => stepTimes(bundle, proposals), [bundle, proposals]);

  function step(delta: 1 | -1) {
    const next = nextStep(steps, useInspector.getState().time, delta);
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

  function publish(result: RecoveryExperiment) {
    client.setQueryData(["recovery-experiment", result.id], result);
    setExperimentId(result.id);
    void client.invalidateQueries({ queryKey: ["recovery-experiments", runId] });
  }

  const act = useMutation({
    mutationFn: (work: () => Promise<RecoveryExperiment>) => work(),
    onMutate: () => setError(""),
    onSuccess: publish,
    onError: (err: Error) => setError(err.message),
  });

  const problemAction = useMutation({
    mutationFn: ({ targetId, restore }: { targetId: string; restore: boolean }) =>
      restore ? api.restoreRecoveryProblem(runId, targetId) : api.dismissRecoveryProblem(runId, targetId),
    onMutate: () => setError(""),
    onSuccess: (result) => client.setQueryData(["recovery-candidates", runId], result),
    onError: (err: Error) => setError(err.message),
  });

  function addSeed(target: string, at: number, bbox: BBox | null, origin: string) {
    const range = trackRange(at, seconds, direction);
    setDrafts((current) => placeSeed(current, { objectId: target, timestamp: at, bbox, points: [], ...range, direction, origin }));
    setObjectId(target);
    setHint("");
  }

  function addPoint(x: number, y: number) {
    if (!objectId) {
      setHint("Choose an object above first.");
      return;
    }
    const negative = pointTool === "negative";
    const existing = drafts.find((item) => item.objectId === objectId && Math.abs(item.timestamp - time) <= 0.05);
    if (existing) {
      setDrafts((current) => current.map((item) => item.id === existing.id ? { ...item, points: [...item.points, { x, y, negative }] } : item));
    } else if (negative) {
      setHint("Add a box or a positive point first.");
    } else {
      const range = trackRange(time, seconds, direction);
      setDrafts((current) => placeSeed(current, { objectId, timestamp: time, bbox: null, points: [{ x, y }], ...range, direction, origin: "points" }));
    }
  }

  function onPicture(box: BBox | null, x: number, y: number) {
    if (pointTool !== "none") {
      addPoint(x, y);
    } else if (box) {
      if (objectId) addSeed(objectId, time, box, "drawn");
      else setHint("Choose which object you are drawing, then drag again.");
    } else {
      const hit = boxAt(boxes, objectIds, x, y);
      if (hit) {
        seek(hit.timestamp);
        addSeed(hit.objectId, hit.timestamp, hit.bbox, `${LAYERS.find((item) => item.key === hit.layer)?.label ?? "stored"} box`);
      }
    }
  }

  function useSuggested(target: RecoveryTarget) {
    const seed = target.seeds?.[0];
    setObjectId(target.object_local_id);
    seek(seed?.timestamp ?? target.time_start);
    if (!seed?.bbox) return;
    setDrafts((current) => placeSeed(current, {
      objectId: target.object_local_id,
      timestamp: seed.timestamp,
      bbox: seed.bbox,
      points: [],
      start: target.time_start,
      end: target.time_end,
      direction: "auto",
      origin: `suggested, ${humanize(seed.trust)}`,
    }));
  }

  function preview() {
    const key = draftsKey;
    act.mutate(async () => {
      const created = await api.createRecovery(runId, {
        manual_targets: manualTargets(drafts),
        limits: {
          max_requests: Number(maxRequests),
          max_frames_per_request: Number(maxFrames),
          max_total_billed_frames: Number(maxBilled),
          cost_budget_usd: Number(budget),
          padding_sec: 0,
        },
      });
      const planned = await api.recoveryDryRun(created.id);
      setPlannedKey(key);
      return planned;
    });
  }

  function decide(decision: "accepted" | "rejected") {
    const pending = proposals.filter((item) => item.decision === "pending").map((item) => item.id);
    act.mutate(async () => {
      const result = await api.decideRecovery(experimentId, { decision, proposal_ids: pending, include_provisional: provisional });
      if (decision === "accepted") {
        setDrafts([]);
        await client.invalidateQueries({ queryKey: ["run", runId] });
        await client.invalidateQueries({ queryKey: ["recovery-candidates", runId] });
      }
      return result;
    });
  }

  return (
    <div className="space-y-3" data-testid="recovery-page">
      <div className="grid min-w-0 gap-3 xl:grid-cols-[minmax(0,1fr)_400px]">
        <section className="panel min-w-0 space-y-2 p-3">
          <div className="flex flex-wrap items-center gap-2 text-sm" role="group" aria-label="Object to seed">
            {bundle.objects.map((object) => (
              <button
                key={object.local_id}
                className="btn"
                aria-pressed={objectId === object.local_id}
                title={object.label}
                style={objectId === object.local_id ? { borderColor: object.color, color: object.color } : undefined}
                onClick={() => setObjectId(objectId === object.local_id ? "" : object.local_id)}
              >
                {humanize(object.local_id)}
              </button>
            ))}
          </div>
          <p className="text-xs text-[var(--muted)]">
            Click a box on the frame to use it as a seed, or choose an object and drag to draw one. Add as many as you need, then send them together. Scroll or use − / + to zoom, and Shift-drag to pan.
          </p>
          {hint && <p className="text-xs text-[var(--amber)]" role="status">{hint}</p>}
          <RecoveryStage bundle={bundle} boxes={boxes} drafts={drafts} objectId={objectId} time={time} pointTool={pointTool} onPicture={onPicture} onStep={step} />
          <div className="flex flex-wrap items-center gap-2 text-sm" role="group" aria-label="Box layers">
            <span className="mono text-[var(--muted)]">{time.toFixed(2)}s</span>
            {LAYERS.map((layer) => (
              <button
                key={layer.key}
                className="btn"
                aria-pressed={layers[layer.key]}
                style={layers[layer.key] ? { borderColor: layer.color, color: layer.color } : undefined}
                onClick={() => setLayers({ ...layers, [layer.key]: !layers[layer.key] })}
              >
                {layer.label}
              </button>
            ))}
            <button className="btn" aria-pressed={pointTool === "positive"} onClick={() => setPointTool(pointTool === "positive" ? "none" : "positive")}>+ point</button>
            <button className="btn" aria-pressed={pointTool === "negative"} onClick={() => setPointTool(pointTool === "negative" ? "none" : "negative")}>− point</button>
          </div>
          <ObjectLanes bundle={bundle} targets={targets} proposals={proposals} drafts={drafts} time={time} objectId={objectId} onSeek={seek} onObject={setObjectId} />
          <details className="border-t border-[var(--line)] pt-2 text-xs">
            <summary className="cursor-pointer text-sm">What is at this frame</summary>
            <div className="mt-2 h-40 space-y-1 overflow-auto" data-testid="box-inspector">
              {boxes.length === 0 ? (
                <p className="text-[var(--muted)]">No selected layer has a box near this frame. A missing box is unobserved, not absence.</p>
              ) : (
                <ul className="space-y-1">
                  {boxes.map((box) => (
                    <li key={box.key}>
                      <span className="font-medium">{LAYERS.find((item) => item.key === box.layer)?.label}</span> · {humanize(box.objectId)} · {formatTime(box.timestamp)} · {box.details.join(" · ")}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </details>
        </section>
        <div className="min-w-0 space-y-3">
          <SeedList
            drafts={drafts}
            seconds={seconds}
            direction={direction}
            onSeconds={setSeconds}
            onDirection={setDirection}
            onSeek={seek}
            onChange={setDrafts}
          >
            <details className="text-xs" data-testid="recovery-limits">
              <summary className="cursor-pointer text-[var(--muted)]">Limits</summary>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Limit label="Provider requests" value={maxRequests} onChange={setMaxRequests} />
                <Limit label="Frames per request" value={maxFrames} onChange={setMaxFrames} />
                <Limit label="Total billed frames" value={maxBilled} onChange={setMaxBilled} />
                <Limit label="Cost budget (USD)" value={budget} onChange={setBudget} />
              </div>
            </details>
            <RunPanel
              drafts={drafts}
              data={data}
              plan={plan}
              pending={act.isPending}
              error={error}
              onPreview={preview}
              onLive={(hash, keys) => act.mutate(() => api.recoveryLive(experimentId, { confirm: true, plan_hash: hash, ...(keys ? { resubmit_keys: keys } : {}) }))}
              onReplay={() => act.mutate(() => api.recoveryReplay(experimentId))}
              onCancel={() => act.mutate(() => api.recoveryCancel(experimentId))}
            />
          </SeedList>
          <ResultsPanel data={data} proposals={proposals} provisional={provisional} onProvisional={setProvisional} pending={act.isPending} onDecide={decide} onSeek={seek} />
          <ProblemList
            targets={targets}
            dismissed={dismissed}
            pending={candidates.isPending}
            busy={problemAction.isPending}
            error={problemAction.isError ? error : ""}
            onSeek={seek}
            onObject={setObjectId}
            onSuggested={useSuggested}
            onDismiss={(targetId) => problemAction.mutate({ targetId, restore: false })}
            onRestore={(targetId) => problemAction.mutate({ targetId, restore: true })}
          />
          <EarlierRuns
            runs={history.data ?? []}
            selected={experimentId}
            onOpen={(id) => {
              setExperimentId(id);
              setPlannedKey("");
            }}
          />
        </div>
      </div>
    </div>
  );
}

function EarlierRuns({
  runs,
  selected,
  onOpen,
}: {
  runs: RecoveryExperiment[];
  selected: string;
  onOpen: (id: string) => void;
}) {
  if (!runs.length) return null;
  return (
    <details className="panel p-3 text-sm" data-testid="earlier-runs">
      <summary className="cursor-pointer">Earlier runs ({runs.length})</summary>
      <ul className="mt-2 max-h-72 space-y-1 overflow-auto">
        {runs.map((run) => (
          <li key={run.id}>
            <button
              className="w-full rounded border border-[var(--line)] p-2 text-left text-xs"
              aria-current={run.id === selected ? "true" : undefined}
              style={run.id === selected ? { borderColor: "var(--teal)" } : undefined}
              onClick={() => onOpen(run.id)}
            >
              <span className="block font-medium">{formatWhen(run.created_at)} · {runCoverage(run.targets)}</span>
              <span className="block text-[var(--muted)]">
                {humanize(run.status)}{run.pending_proposals > 0 ? ` · ${run.pending_proposals} pending review` : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </details>
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
  drafts,
  objectId,
  time,
  pointTool,
  onPicture,
  onStep,
}: {
  bundle: Bundle;
  boxes: ReturnType<typeof layerBoxes>;
  drafts: SeedDraft[];
  objectId: string;
  time: number;
  pointTool: PointTool;
  onPicture: (box: BBox | null, x: number, y: number) => void;
  onStep: (delta: 1 | -1) => void;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  // The overlay draws in on-screen pixels, so strokes and labels keep their size on a 4K source.
  const [size, setSize] = useState({ x: 0, y: 0, w: 1, h: 1, vw: 1 });
  const [drag, setDrag] = useState<{ x: number; y: number; ex: number; ey: number } | null>(null);
  const { zoom, pan, panning, stepZoom, frameClass, frameHandlers, frameTransform } = useStageZoom(frameRef, size);
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
      setSize({ x: (rect.width - w) / 2, y: (rect.height - h) / 2, w, h, vw: video.videoWidth });
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
  const color = (layer: RecoveryLayer) => LAYERS.find((item) => item.key === layer)?.color ?? "#fff";
  const shown = drafts.filter((item) => Math.abs(item.timestamp - time) <= NEAR_SEC);
  // Inspect's label size on this video, with a floor so it stays readable on a high-resolution source.
  const labelSize = Math.max(INSPECT_LABEL_SIZE * (size.w / size.vw), MIN_LABEL_SIZE);
  // Sized rather than scaled with the video, so a zoomed frame keeps thin strokes for precise seeds.
  const drawn = { w: size.w * zoom, h: size.h * zoom };
  return (
    <div ref={frameRef} className={`stage-frame rounded-lg bg-black${frameClass}`} {...frameHandlers}>
      <video ref={videoRef} src={bundle.video?.playback_url ?? ""} muted preload="auto" style={frameTransform} />
      <div
        className="absolute"
        data-testid="recovery-overlay"
        style={{
          left: size.x + (size.w - drawn.w) / 2 + pan.x,
          top: size.y + (size.h - drawn.h) / 2 + pan.y,
          width: drawn.w,
          height: drawn.h,
          cursor: panning ? "grabbing" : "crosshair",
          touchAction: "none",
        }}
        onPointerDown={(event) => {
          // Shift-drag falls through to the frame, which pans.
          if (event.shiftKey) return;
          event.stopPropagation();
          const at = normalized(event);
          event.currentTarget.setPointerCapture?.(event.pointerId);
          setDrag({ x: at.x, y: at.y, ex: at.x, ey: at.y });
        }}
        onPointerMove={(event) => {
          if (!drag) return;
          const at = normalized(event);
          setDrag({ ...drag, ex: at.x, ey: at.y });
        }}
        onPointerUp={() => {
          if (drag) onPicture(pointTool === "none" ? preview : null, drag.x, drag.y);
          setDrag(null);
        }}
      >
        <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${drawn.w} ${drawn.h}`}>
          {boxes.map((box) => (
            <g key={box.key} data-layer={box.layer}>
              <rect
                x={box.bbox.x * drawn.w}
                y={box.bbox.y * drawn.h}
                width={box.bbox.w * drawn.w}
                height={box.bbox.h * drawn.h}
                fill={box.objectId === objectId ? "#ffffff14" : "none"}
                stroke={color(box.layer)}
                strokeWidth={box.objectId === objectId ? 3 : 1.5}
                strokeDasharray={box.dashed ? "6 4" : undefined}
              />
              <text x={box.bbox.x * drawn.w} y={Math.max(13, box.bbox.y * drawn.h - 4)} fill={color(box.layer)} fontSize={labelSize} fontFamily="IBM Plex Sans, sans-serif">
                {humanize(box.objectId)}
              </text>
            </g>
          ))}
          {shown.map((seed) => (
            <g key={seed.id}>
              {seed.bbox && (
                <>
                  <rect data-testid="drawn-seed" x={seed.bbox.x * drawn.w} y={seed.bbox.y * drawn.h} width={seed.bbox.w * drawn.w} height={seed.bbox.h * drawn.h} fill="#ffffff22" stroke="#fff" strokeWidth={3} />
                  <text x={seed.bbox.x * drawn.w} y={(seed.bbox.y + seed.bbox.h) * drawn.h + 15} fill="#fff" fontSize={labelSize} fontFamily="IBM Plex Sans, sans-serif">Seed · {humanize(seed.objectId)}</text>
                </>
              )}
              {seed.points.map((point, index) => (
                <circle key={index} cx={point.x * drawn.w} cy={point.y * drawn.h} r={5} fill={point.negative ? "#e07070" : "#7dcea0"} stroke="#000" />
              ))}
            </g>
          ))}
          {preview && <rect x={preview.x * drawn.w} y={preview.y * drawn.h} width={preview.w * drawn.w} height={preview.h * drawn.h} fill="#ffffff22" stroke="#fff" strokeWidth={2} />}
        </svg>
      </div>
      <StageZoomControl zoom={zoom} onStep={stepZoom} />
      <PreviewNav direction="prev" label="Previous frame" onClick={() => onStep(-1)} />
      <PreviewNav direction="next" label="Next frame" onClick={() => onStep(1)} />
    </div>
  );
}

function ObjectLanes({
  bundle,
  targets,
  proposals,
  drafts,
  time,
  objectId,
  onSeek,
  onObject,
}: {
  bundle: Bundle;
  targets: RecoveryTarget[];
  proposals: RecoveryProposal[];
  drafts: SeedDraft[];
  time: number;
  objectId: string;
  onSeek: (time: number) => void;
  onObject: (id: string) => void;
}) {
  const duration = Math.max(bundle.video?.duration_sec ?? 1, 0.1);
  const percent = (value: number) => `${Math.min(Math.max(value / duration, 0), 1) * 100}%`;
  return (
    <div className="space-y-1 border-t border-[var(--line)] pt-2" data-testid="object-lanes">
      <div className="grid grid-cols-[110px_1fr] items-center gap-2">
        <span aria-hidden="true" />
        <input
          aria-label="Seek"
          className="seek"
          type="range"
          min={0}
          max={duration}
          step={0.01}
          value={Math.min(time, duration)}
          onChange={(event) => onSeek(Number(event.target.value))}
        />
      </div>
      {bundle.objects.map((object) => (
        <div key={object.local_id} className="grid grid-cols-[110px_1fr] items-center gap-2">
          <button
            className={`truncate text-left text-xs ${objectId === object.local_id ? "font-medium" : "text-[var(--muted)]"}`}
            style={objectId === object.local_id ? { color: object.color } : undefined}
            title={object.label}
            onClick={() => onObject(object.local_id)}
          >
            {humanize(object.local_id)}
          </button>
          <div
            className="relative h-5 cursor-pointer overflow-hidden rounded bg-[#0d1016]"
            data-testid={`lane-${object.local_id}`}
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              onObject(object.local_id);
              onSeek(((event.clientX - rect.left) / rect.width) * duration);
            }}
          >
            {laneSpans(bundle, object.local_id, targets, proposals).map((span, index) => {
              const problem = span.kind === "problem";
              return (
                <div
                  key={index}
                  data-kind={span.kind}
                  className="absolute rounded-sm"
                  title={`${humanize(span.kind)} · ${formatTime(span.start)}–${formatTime(span.end)}`}
                  style={{
                    left: percent(span.start),
                    width: `max(2px, ${(span.end - span.start) / duration * 100}%)`,
                    top: problem ? 0 : span.kind === "stored" ? 7 : 3,
                    bottom: problem ? 0 : span.kind === "stored" ? 7 : 3,
                    background: problem ? "#b7791f55" : LANE_COLORS[span.kind],
                  }}
                />
              );
            })}
            {drafts.filter((item) => item.objectId === object.local_id).map((item) => (
              <div key={item.id} data-kind="seed" className="pointer-events-none absolute inset-y-0 w-[3px] bg-white" style={{ left: percent(item.timestamp) }} />
            ))}
            <div className="pointer-events-none absolute inset-y-0 w-px bg-[var(--teal)]" style={{ left: percent(time) }} />
          </div>
        </div>
      ))}
      <div className="flex flex-wrap gap-3 pt-1 text-[11px] text-[var(--muted)]">
        <Legend color="#465066" label="Stored boxes" />
        <Legend color="#b7791f" label="Problem" />
        <Legend color="#ffffff" label="Your seed" />
        <Legend color={LANE_COLORS.supported} label="Supported" />
        <Legend color={LANE_COLORS.provisional} label="Provisional" />
        <Legend color={LANE_COLORS.conflicting} label="Conflicting" />
      </div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return <span className="inline-flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: color }} />{label}</span>;
}

function SeedList({
  drafts,
  seconds,
  direction,
  onSeconds,
  onDirection,
  onSeek,
  onChange,
  children,
}: {
  drafts: SeedDraft[];
  seconds: number;
  direction: TrackDirection;
  onSeconds: (value: number) => void;
  onDirection: (value: TrackDirection) => void;
  onSeek: (time: number) => void;
  onChange: (drafts: SeedDraft[]) => void;
  children: ReactNode;
}) {
  return (
    <section className="panel space-y-2 p-4 text-sm" data-testid="seed-list">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base">Seeds ({drafts.length})</h2>
        {drafts.length > 0 && <button className="btn" onClick={() => onChange([])}>Clear all</button>}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
        <span>New seeds track</span>
        <select aria-label="Track direction" className="field w-auto" value={direction} onChange={(event) => onDirection(event.target.value as TrackDirection)}>
          <option value="forward">forward</option>
          <option value="backward">backward</option>
          <option value="both">both ways</option>
        </select>
        <span>for</span>
        <select aria-label="Track seconds" className="field w-auto" value={seconds} onChange={(event) => onSeconds(Number(event.target.value))}>
          {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value}s</option>)}
        </select>
      </div>
      {drafts.length === 0 ? (
        <p className="text-xs text-[var(--muted)]">No seeds yet. Click a correct box on the frame, or choose an object and drag around it.</p>
      ) : (
        <ul className="space-y-1">
          {drafts.map((draft) => (
            <li key={draft.id} className="flex items-center gap-2 rounded border border-[var(--line)] p-2 text-xs">
              <button className="min-w-0 flex-1 text-left" onClick={() => onSeek(draft.timestamp)}>
                <span className="font-medium">{humanize(draft.objectId)}</span> · {draft.timestamp.toFixed(2)}s
                <span className="block text-[var(--muted)]">
                  {draft.origin} · tracks {draft.start.toFixed(2)}–{draft.end.toFixed(2)}s{draft.direction === "auto" ? "" : `, ${draft.direction}`}{draft.points.length ? ` · ${draft.points.length} points` : ""}
                </span>
              </button>
              <button className="btn shrink-0 !p-0" style={SQUARE_BUTTON} aria-label={`Remove seed for ${humanize(draft.objectId)} at ${draft.timestamp.toFixed(2)}s`} onClick={() => onChange(drafts.filter((item) => item.id !== draft.id))}><X size={14} strokeWidth={1.75} /></button>
            </li>
          ))}
        </ul>
      )}
      {children}
    </section>
  );
}

function RunPanel({
  drafts,
  data,
  plan,
  pending,
  error,
  onPreview,
  onLive,
  onReplay,
  onCancel,
}: {
  drafts: SeedDraft[];
  data: RecoveryExperiment | null;
  plan: RecoveryExperiment["plan"];
  pending: boolean;
  error: string;
  onPreview: () => void;
  onLive: (hash: string, keys?: string[]) => void;
  onReplay: () => void;
  onCancel: () => void;
}) {
  const running = data?.status === "running";
  const uncertain = (data?.requests ?? []).filter((item) => item.submission === "submitted" && item.status !== "running").map((item) => item.cache_key);
  const remote = plan?.totals.remote_requests ?? 0;
  const cached = plan?.totals.cached_requests ?? 0;
  const canRun = Boolean(plan && remote > 0 && plan.violations.length === 0);
  return (
    <div className="space-y-2 border-t border-[var(--line)] pt-3" data-testid="recovery-plan">
      {running ? (
        <>
          <p>SAM is running. Cutting the clip and uploading can take a minute.</p>
          <button className="btn" disabled={data?.cancel_requested} onClick={onCancel}>{data?.cancel_requested ? "Canceling…" : "Cancel"}</button>
        </>
      ) : plan ? (
        <>
          <p className="text-xs">{usage(plan.totals)}{cached ? `, ${cached} already stored` : ""}.</p>
          {plan.totals.estimated_cost_usd == null && <p className="text-xs text-[var(--muted)]">No per-frame rate is set, so the cost is unknown. Frame limits still apply.</p>}
          {plan.blocked.map((item) => (
            <p key={`${item.target_id}-${item.reason}`} className="text-xs text-[var(--amber)]">{humanize(item.object_local_id)}: {item.reason}</p>
          ))}
          {plan.violations.map((item) => <p key={item} className="text-xs text-[var(--rose)]">{item}</p>)}
          <div className="flex flex-wrap gap-2">
            {canRun && <button className="btn primary" disabled={pending} onClick={() => onLive(plan.plan_hash)}>Run SAM: {remote} {remote === 1 ? "request" : "requests"}, about {plan.totals.billed_frames} frames</button>}
            {cached > 0 && <button className="btn" disabled={pending} onClick={onReplay}>Use stored responses (free)</button>}
            {canRun && uncertain.length > 0 && <button className="btn" disabled={pending} onClick={() => onLive(plan.plan_hash, uncertain)}>Also resend {uncertain.length} that may have been billed</button>}
          </div>
          <p className="text-xs text-[var(--muted)]">Run SAM may be billed by Meta. It sends only these SAM requests.</p>
          <details className="text-xs">
            <summary className="cursor-pointer text-[var(--muted)]">Request details</summary>
            <ul className="mt-1 space-y-1">
              {plan.requests.map((spec) => (
                <li key={spec.key} className="rounded border border-[var(--line)] p-2">
                  {humanize(spec.direction)} · {spec.source_start.toFixed(2)}–{spec.source_end.toFixed(2)}s · {spec.frame_times.length} frames · {spec.objects.length} seeds
                  <code className="mono mt-1 block break-all text-[var(--muted)]">{spec.prompt}</code>
                </li>
              ))}
            </ul>
          </details>
        </>
      ) : (
        <button className="btn primary" disabled={pending || drafts.length === 0} onClick={onPreview}>
          {pending ? "Checking…" : `Check ${drafts.length || ""} ${drafts.length === 1 ? "seed" : "seeds"} (free)`}
        </button>
      )}
      {!running && !plan && Boolean(data?.plan?.requests.length) && (
        <button className="btn" disabled={pending} onClick={onReplay}>Replay this run's stored responses (free)</button>
      )}
      {data?.error && (
        <p className="text-xs text-[var(--amber)]" role="status">
          {data.error} {data.remote_submitted ? "A remote call was submitted for this run." : "No remote call was submitted."}
        </p>
      )}
      {error && <p className="text-xs text-[var(--rose)]" role="alert">{error}</p>}
    </div>
  );
}

function ResultsPanel({
  data,
  proposals,
  provisional,
  onProvisional,
  pending,
  onDecide,
  onSeek,
}: {
  data: RecoveryExperiment | null;
  proposals: RecoveryProposal[];
  provisional: boolean;
  onProvisional: (value: boolean) => void;
  pending: boolean;
  onDecide: (decision: "accepted" | "rejected") => void;
  onSeek: (time: number) => void;
}) {
  if (!data || !proposals.length) return null;
  const open = proposals.filter((item) => item.decision === "pending");
  return (
    <section className="panel space-y-2 p-4 text-sm" data-testid="recovery-results">
      <h2 className="text-base">Results</h2>
      {proposals.some((item) => item.metrics.synthetic_response) && (
        <p className="text-xs text-[var(--amber)]">Built from a synthetic offline response. It only exercises the plumbing and says nothing about SAM's accuracy.</p>
      )}
      <ul className="space-y-1 text-xs">
        {proposals.map((proposal) => {
          const counts = proposal.metrics.counts;
          return (
            <li key={proposal.id} className="rounded border border-[var(--line)] p-2">
              <button className="font-medium" onClick={() => onSeek(proposal.time_start)}>{humanize(proposal.object_local_id)} · {proposal.time_start.toFixed(2)}–{proposal.time_end.toFixed(2)}s</button>
              {proposal.decision !== "pending" && ` · ${humanize(proposal.decision)}`}
              <div>
                <span style={{ color: LANE_COLORS.supported }}>{counts.supported} supported</span>
                {" · "}<span style={{ color: LANE_COLORS.provisional }}>{counts.provisional} provisional</span>
                {" · "}<span style={{ color: LANE_COLORS.conflicting }}>{counts.conflicting} conflicting</span>
                {" · "}{counts.rejected} rejected · {counts.unobserved_frames} unobserved
              </div>
              {proposal.metrics.remaining_gaps.slice(0, 3).map((gap) => (
                <button key={gap.start} className="block text-[var(--muted)]" onClick={() => onSeek(gap.start)}>{gap.start.toFixed(2)}–{gap.end.toFixed(2)}s has no box. That is not evidence of absence.</button>
              ))}
            </li>
          );
        })}
      </ul>
      {open.length > 0 && (
        <>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={provisional} onChange={(event) => onProvisional(event.target.checked)} />
            Include provisional boxes. They stay non-canonical.
          </label>
          <div className="flex flex-wrap gap-2">
            <button className="btn primary" disabled={pending} onClick={() => onDecide("accepted")}>Accept as a new revision</button>
            <button className="btn" disabled={pending} onClick={() => onDecide("rejected")}>Reject all</button>
          </div>
        </>
      )}
      <p className="text-xs text-[var(--muted)]">{proposals[0].metrics.accuracy_note}</p>
    </section>
  );
}

function ProblemList({
  targets,
  dismissed,
  pending,
  busy,
  error,
  onSeek,
  onObject,
  onSuggested,
  onDismiss,
  onRestore,
}: {
  targets: RecoveryTarget[];
  dismissed: RecoveryTarget[];
  pending: boolean;
  busy: boolean;
  error: string;
  onSeek: (time: number) => void;
  onObject: (id: string) => void;
  onSuggested: (target: RecoveryTarget) => void;
  onDismiss: (targetId: string) => void;
  onRestore: (targetId: string) => void;
}) {
  const [showDismissed, setShowDismissed] = useState(false);
  const describe = (target: RecoveryTarget) => `${humanize(target.object_local_id)} at ${target.time_start.toFixed(2)}s`;
  return (
    <details className="panel p-3 text-sm" data-testid="recovery-candidates">
      <summary className="cursor-pointer">Suspected problems ({pending ? <Bone width="2ch" /> : targets.length})</summary>
      <ul className="mt-2 max-h-72 space-y-1 overflow-auto">
        {targets.map((target) => (
          <li key={target.target_id} className="flex items-center gap-2 text-xs">
            <button
              className="min-w-0 flex-1 text-left"
              onClick={() => {
                onObject(target.object_local_id);
                onSeek(target.time_start);
              }}
            >
              <span className="font-medium">{humanize(target.object_local_id)}</span> · {target.time_start.toFixed(2)}–{target.time_end.toFixed(2)}s
              <span className="block text-[var(--muted)]">{target.reasons.map(humanize).join(", ")}</span>
            </button>
            {target.recoverable && (target.seeds?.length ?? 0) > 0 && <button className="btn" onClick={() => onSuggested(target)}>Use suggested seed</button>}
            <button className="btn shrink-0 !p-0" style={SQUARE_BUTTON} disabled={busy} title="Not a problem" aria-label={`Dismiss problem for ${describe(target)}`} onClick={() => onDismiss(target.target_id)}>
              <X size={14} strokeWidth={1.75} />
            </button>
          </li>
        ))}
      </ul>
      {!pending && targets.length === 0 && <p className="mt-2 text-xs text-[var(--muted)]">Nothing is flagged. That does not mean every box is correct.</p>}
      {dismissed.length > 0 && (
        <div className="mt-2 space-y-1 text-xs">
          <button className="text-[var(--muted)] underline" aria-expanded={showDismissed} onClick={() => setShowDismissed(!showDismissed)}>
            {showDismissed ? "Hide" : "Show"} dismissed ({dismissed.length})
          </button>
          {showDismissed && (
            <ul className="space-y-1">
              {dismissed.map((target) => (
                <li key={target.target_id} className="flex items-center gap-2 text-[var(--muted)]">
                  <span className="min-w-0 flex-1">
                    {humanize(target.object_local_id)} · {target.time_start.toFixed(2)}–{target.time_end.toFixed(2)}s · {target.reasons.map(humanize).join(", ")}
                  </span>
                  <button className="btn" disabled={busy} aria-label={`Restore problem for ${describe(target)}`} onClick={() => onRestore(target.target_id)}>Restore</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && <p className="mt-2 text-xs text-[var(--rose)]" role="alert">{error}</p>}
    </details>
  );
}

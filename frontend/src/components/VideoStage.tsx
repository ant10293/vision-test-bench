import { useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type MouseEvent, type PointerEvent } from "react";
import { AssetPreview } from "./AssetPreview";
import { api } from "../lib/api";
import type { Bundle, Observation, TrackPointSample } from "../lib/types";
import { coverageAt, frameObjectStills, overlayObservations, tracksAt } from "../lib/analysis";
import { formatConfidence } from "../lib/confidence";
import { humanize, labeledId } from "../lib/humanize";
import { panStage, stageViewAt, stageZoomByWheel, clampStageView, MAX_STAGE_ZOOM, MIN_STAGE_ZOOM, type StageView } from "../lib/stageZoom";
import { ZoomStepper } from "./ZoomStepper";
import { wrapLines } from "../lib/wrapText";
import { useInspector } from "../stores/ui";

const LABEL_SIZE = 14;
const STAGE_ZOOM_STEP = 0.25;
const LABEL_LINE = LABEL_SIZE * 1.2;
const LABEL_FONT = `${LABEL_SIZE}px "IBM Plex Sans", sans-serif`;
const CONFIDENCE_INSET = 4;

let labelCanvas: HTMLCanvasElement | null = null;

function labelWidth(text: string): number {
  labelCanvas ??= document.createElement("canvas");
  const context = labelCanvas.getContext("2d");
  if (!context) return text.length * LABEL_SIZE * 0.5;
  context.font = LABEL_FONT;
  return context.measureText(text).width;
}

export function VideoStage({ bundle }: { bundle: Bundle }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const client = useQueryClient();
  const { time, setTime, playing, setPlaying, speed, overlay, layers, seedMode, loop, selectedObject } = useInspector();
  const [seedError, setSeedError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [panning, setPanning] = useState(false);
  const viewRef = useRef<StageView>({ zoom: 1, panX: 0, panY: 0 });
  const contentRef = useRef({ x: 0, y: 0, w: 1, h: 1, vw: 640, vh: 360 });
  const dragRef = useRef<{ pointerId: number; x: number; y: number; panX: number; panY: number } | null>(null);
  const [content, setContent] = useState({ x: 0, y: 0, w: 1, h: 1, vw: 640, vh: 360 });
  contentRef.current = content;
  const [fontReady, setFontReady] = useState(false);
  const [objectsOpen, setObjectsOpen] = useState(true);
  const [preview, setPreview] = useState<number | null>(null);
  const previewRef = useRef<number | null>(null);
  previewRef.current = preview;
  const commitView = useCallback((next: StageView) => {
    viewRef.current = next;
    setZoom(next.zoom);
    setPan({ x: next.panX, y: next.panY });
  }, []);

  function pictureSize(frameWidth: number, frameHeight: number) {
    const picture = contentRef.current;
    return {
      width: picture.w > 1 ? picture.w : frameWidth,
      height: picture.h > 1 ? picture.h : frameHeight,
    };
  }

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const seek = () => {
      const target = useInspector.getState().time;
      if (Number.isFinite(target) && Math.abs(video.currentTime - target) > 0.08) video.currentTime = target;
    };
    seek();
    video.addEventListener("loadedmetadata", seek);
    return () => video.removeEventListener("loadedmetadata", seek);
  }, [time]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.playbackRate = speed;
    if (playing) void video.play().catch(() => setPlaying(false));
    else video.pause();
  }, [playing, speed, setPlaying]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (previewRef.current != null) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.code === "Space") {
        event.preventDefault();
        setPlaying(!useInspector.getState().playing);
      } else if (event.key === "ArrowRight") {
        step(1);
      } else if (event.key === "ArrowLeft") {
        step(-1);
      } else if (event.key === "f") {
        void boxRef.current?.requestFullscreen();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPlaying, bundle.frames]);

  function step(direction: number) {
    const frames = bundle.frames;
    const now = useInspector.getState().time;
    const ordered = [...frames].sort((a, b) => a.timestamp - b.timestamp);
    const next = direction > 0
      ? ordered.find((frame) => frame.timestamp > now + 0.01)
      : [...ordered].reverse().find((frame) => frame.timestamp < now - 0.01);
    setTime(next?.timestamp ?? Math.max(0, now + direction / 10));
  }

  useEffect(() => {
    let cancel = false;
    document.fonts.load(LABEL_FONT).then(() => {
      if (!cancel) setFontReady(true);
    });
    return () => {
      cancel = true;
    };
  }, []);

  const samBoxes = overlay ? overlayObservations(bundle.observations, time, "sam") : [];
  const trackedIds = new Set(samBoxes.map((item) => item.object_local_id));
  const vlmBoxes = overlay ? overlayObservations(bundle.observations, time, "vlm").filter((item) => !trackedIds.has(item.object_local_id)) : [];
  const inferredBoxes = overlay ? overlayObservations(bundle.observations, time, "inferred") : [];
  const conflictBoxes = overlay && layers.conflicts ? overlayObservations(bundle.observations, time, "conflict") : [];
  const stills = frameObjectStills(bundle, time);
  const allPoints = bundle.tracks?.points ?? [];
  const trackHits = tracksAt(allPoints, time);
  const showTracks = layers.tracks || layers.trajectory || layers.uncertain;
  const selectedCoverage = selectedObject ? coverageAt(bundle.observations, selectedObject, time) : "";
  const src = bundle.video ? bundle.video.playback_url : "";

  useEffect(() => {
    const box = boxRef.current;
    const video = videoRef.current;
    if (!box) return;
    const apply = () => {
      const frame = boxRef.current;
      const node = videoRef.current;
      if (!frame || !node || !node.videoWidth) return;
      const rect = frame.getBoundingClientRect();
      const scale = Math.min(rect.width / node.videoWidth, rect.height / node.videoHeight);
      const w = node.videoWidth * scale;
      const h = node.videoHeight * scale;
      setContent({ x: (rect.width - w) / 2, y: (rect.height - h) / 2, w, h, vw: node.videoWidth, vh: node.videoHeight });
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(box);
    video?.addEventListener("loadedmetadata", apply);
    return () => {
      observer.disconnect();
      video?.removeEventListener("loadedmetadata", apply);
    };
  }, [src]);

  useEffect(() => {
    const frame = boxRef.current;
    if (!frame) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = frame.getBoundingClientRect();
      const picture = pictureSize(rect.width, rect.height);
      commitView(stageZoomByWheel(
        viewRef.current,
        event.clientX - rect.left,
        event.clientY - rect.top,
        rect.width,
        rect.height,
        event.deltaY,
        event.deltaMode,
        picture.width,
        picture.height,
      ));
    };
    const observer = new ResizeObserver(() => {
      const rect = frame.getBoundingClientRect();
      const picture = pictureSize(rect.width, rect.height);
      const next = clampStageView(
        viewRef.current,
        rect.width,
        rect.height,
        picture.width,
        picture.height,
      );
      const current = viewRef.current;
      if (next.zoom !== current.zoom || next.panX !== current.panX || next.panY !== current.panY) commitView(next);
    });
    frame.addEventListener("wheel", onWheel, { passive: false });
    observer.observe(frame);
    return () => {
      frame.removeEventListener("wheel", onWheel);
      observer.disconnect();
    };
  }, [commitView]);

  useEffect(() => {
    const frame = boxRef.current;
    if (!frame || content.w <= 1 || content.h <= 1) return;
    const rect = frame.getBoundingClientRect();
    const next = clampStageView(viewRef.current, rect.width, rect.height, content.w, content.h);
    const current = viewRef.current;
    if (next.zoom !== current.zoom || next.panX !== current.panX || next.panY !== current.panY) commitView(next);
  }, [content.w, content.h, commitView]);

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || viewRef.current.zoom <= 1) return;
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      panX: viewRef.current.panX,
      panY: viewRef.current.panY,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setPanning(true);
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const picture = pictureSize(rect.width, rect.height);
    commitView(panStage(
      viewRef.current,
      drag.panX + event.clientX - drag.x,
      drag.panY + event.clientY - drag.y,
      rect.width,
      rect.height,
      picture.width,
      picture.height,
    ));
  }

  function endPan(event: PointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setPanning(false);
  }

  function stepZoom(direction: number) {
    const frame = boxRef.current;
    if (!frame) return;
    const rect = frame.getBoundingClientRect();
    const picture = pictureSize(rect.width, rect.height);
    const nextZoom = Math.min(MAX_STAGE_ZOOM, Math.max(MIN_STAGE_ZOOM, viewRef.current.zoom + direction * STAGE_ZOOM_STEP));
    commitView(stageViewAt(
      viewRef.current,
      rect.width / 2,
      rect.height / 2,
      rect.width,
      rect.height,
      nextZoom,
      picture.width,
      picture.height,
    ));
  }

  const frameTransform = { transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: "center" };

  return (
    <div className="stage-slot flex min-h-0 w-full flex-col gap-2">
      <div
        ref={boxRef}
        className={`stage-frame rounded-lg bg-black${zoom > 1 ? " is-zoomed" : ""}${panning ? " is-panning" : ""}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPan}
        onPointerCancel={endPan}
      >
        <video
          ref={videoRef}
          src={src}
          style={frameTransform}
          onTimeUpdate={(event) => {
            const current = event.currentTarget.currentTime;
            if (loop && current > loop.end) {
              event.currentTarget.currentTime = loop.start;
              setTime(loop.start);
              return;
            }
            setTime(current);
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onClick={(event) => void seedAt(event)}
        />
        {(vlmBoxes.length > 0 || inferredBoxes.length > 0 || samBoxes.length > 0 || conflictBoxes.length > 0) && (
          <svg
            className="pointer-events-none absolute"
            style={{ left: content.x, top: content.y, width: content.w, height: content.h, ...frameTransform }}
            viewBox={`0 0 ${content.vw} ${content.vh}`}
          >
            {vlmBoxes.map((obs) => (
              <ObservationBox key={obs.id} obs={obs} bundle={bundle} content={content} fontReady={fontReady} showConfidence={layers.confidence} showId />
            ))}
            {inferredBoxes.map((obs) => (
              <ObservationBox key={obs.id} obs={obs} bundle={bundle} content={content} fontReady={fontReady} showConfidence={layers.confidence} showId dashed />
            ))}
            {samBoxes.map((obs) => (
              <ObservationBox key={obs.id} obs={obs} bundle={bundle} content={content} fontReady={fontReady} showConfidence={layers.confidence} showId dashed={false} />
            ))}
            {conflictBoxes.map((obs) => (
              <ObservationBox key={obs.id} obs={obs} bundle={bundle} content={content} fontReady={fontReady} showConfidence={layers.confidence} showId dashed conflict />
            ))}
          </svg>
        )}
        {stills.length > 0 && (
          <div
            className="frame-objects"
            onPointerDown={(event) => event.stopPropagation()}
            onWheel={(event) => event.stopPropagation()}
          >
            <div className="frame-objects-head">
              <div className="frame-objects-title">Objects in frame</div>
              <button
                type="button"
                className="frame-objects-toggle"
                aria-expanded={objectsOpen}
                aria-label={objectsOpen ? "Hide objects in frame" : "Show objects in frame"}
                onClick={() => setObjectsOpen((open) => !open)}
              >
                {objectsOpen ? <Eye size={13} strokeWidth={1.75} /> : <EyeOff size={13} strokeWidth={1.75} />}
              </button>
            </div>
            {objectsOpen && (
              <div className="frame-objects-row">
                {stills.map((still, index) => (
                  <button
                    key={still.id}
                    type="button"
                    className="frame-object"
                    title={still.label}
                    aria-label={`View ${still.label}`}
                    onClick={() => setPreview(index)}
                  >
                    <img src={still.url} alt={still.label} style={{ borderColor: still.color }} />
                    <span>{still.label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        <div
          className="stage-zoom"
          onPointerDown={(event) => event.stopPropagation()}
          onWheel={(event) => event.stopPropagation()}
        >
          <ZoomStepper
            canZoomOut={zoom > MIN_STAGE_ZOOM}
            canZoomIn={zoom < MAX_STAGE_ZOOM}
            onZoomOut={() => stepZoom(-1)}
            onZoomIn={() => stepZoom(1)}
          />
        </div>
        {showTracks && trackHits.length > 0 && (
          <svg
            className="pointer-events-none absolute"
            style={{ left: content.x, top: content.y, width: content.w, height: content.h, ...frameTransform }}
            viewBox={`0 0 ${content.vw} ${content.vh}`}
          >
            {trackHits.map((point) => (
              <TrackMark key={`${point.detector_id}-${point.track_ref}-${point.timestamp}`} point={point} points={allPoints} time={time} content={content} layers={layers} />
            ))}
          </svg>
        )}
      </div>
      <div className="flex flex-col gap-2 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn" onClick={() => step(-1)}>Prev frame</button>
          <button className="btn" onClick={() => step(1)}>Next frame</button>
          <button className="btn" onClick={() => useInspector.getState().setOverlay(!overlay)}>{overlay ? "Hide boxes" : "Show boxes"}</button>
          <button className="btn" aria-pressed={layers.conflicts} onClick={() => useInspector.getState().setLayer("conflicts", !layers.conflicts)}>
            {layers.conflicts ? "Hide conflicts" : "Show conflicts"}
          </button>
          <button className="btn" aria-pressed={layers.confidence} onClick={() => useInspector.getState().setLayer("confidence", !layers.confidence)}>
            {layers.confidence ? "Hide confidence" : "Show confidence"}
          </button>
          <button className="btn" aria-pressed={layers.tracks} onClick={() => useInspector.getState().setLayer("tracks", !layers.tracks)}>
            {layers.tracks ? "Hide tracks" : "Show tracks"}
          </button>
          {([
            ["trajectory", "trajectory"],
            ["uncertain", "uncertain tracks"],
          ] as const).map(([key, label]) => (
            <button key={key} className="btn" aria-pressed={layers[key]} onClick={() => useInspector.getState().setLayer(key, !layers[key])}>
              {layers[key] ? `Hide ${label}` : `Show ${label}`}
            </button>
          ))}
          <button
            className="btn"
            aria-pressed={seedMode}
            disabled={zoom !== 1 || pan.x !== 0 || pan.y !== 0}
            title="Reset zoom first. The click is mapped through the letterboxed frame."
            onClick={() => useInspector.getState().setSeedMode(!seedMode)}
          >
            {seedMode ? "Click the object" : "Seed a track"}
          </button>
          <button className="btn" onClick={() => commitView({ zoom: 1, panX: 0, panY: 0 })}>Reset zoom</button>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn" onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
          <label className="inline-flex items-center gap-2 text-sm text-[var(--muted)]">
            <span>Speed</span>
            <select className="field w-auto text-[var(--text)]" value={speed} onChange={(event) => useInspector.getState().setSpeed(Number(event.target.value))}>
              {[0.25, 0.5, 1, 1.5, 2].map((value) => <option key={value} value={value}>{value}×</option>)}
            </select>
          </label>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="mono text-[var(--muted)]">{time.toFixed(2)}s</span>
          <span className="text-xs text-[var(--muted)]">Space play · arrows step · f fullscreen · scroll over a point to zoom · drag to pan</span>
          {selectedObject && (
            <span className="text-xs text-[var(--muted)]">
              {humanize(selectedObject)} at this time: {coverageLabel(selectedCoverage)}. A drawn trail uses stored points and is not a filled-in location.
            </span>
          )}
        </div>
      </div>
      {preview != null && stills[preview] && (
        <AssetPreview
          key={stills[preview].id}
          images={stills.map((still) => ({ src: still.url, filename: still.label, confidence: still.confidence }))}
          startIndex={preview}
          onClose={() => setPreview(null)}
        />
      )}
      {seedError && <p className="text-xs text-[var(--rose)]">{seedError}</p>}
      {bundle.tracks?.truncated && <p className="text-xs text-[var(--muted)]">Showing the first {bundle.tracks.points.length} of {bundle.tracks.total} track points.</p>}
    </div>
  );

  function seedAt(event: MouseEvent<HTMLVideoElement>) {
    if (!seedMode || zoom !== 1 || pan.x !== 0 || pan.y !== 0) return;
    const rect = boxRef.current?.getBoundingClientRect();
    if (!rect || content.w <= 0 || content.h <= 0) return;
    const x = event.clientX - rect.left - content.x;
    const y = event.clientY - rect.top - content.y;
    if (x < 0 || y < 0 || x > content.w || y > content.h) return;
    const size = 0.12;
    const nx = Math.min(Math.max(0, x / content.w - size / 2), 1 - size);
    const ny = Math.min(Math.max(0, y / content.h - size / 2), 1 - size);
    setSeedError("");
    void api.seedTrack(bundle.run.id, { timestamp: time, x: nx, y: ny, w: size, h: size })
      .then(() => client.invalidateQueries({ queryKey: ["run", bundle.run.id] }))
      .then(() => useInspector.getState().setSeedMode(false))
      .catch((err: unknown) => setSeedError(err instanceof Error ? err.message : "The seed was not saved."));
  }
}

function coverageLabel(state: string) {
  if (state === "observed") return "directly observed";
  if (state === "inferred") return "inferred between observations";
  if (state === "absent") return "explicitly absent";
  if (state === "conflict") return "model and SAM disagree";
  if (state === "unassigned") return "SAM track was not linked to this object";
  return "unobserved";
}

function ObservationBox({
  obs,
  bundle,
  content,
  fontReady,
  showConfidence,
  showId,
  dashed = false,
  conflict = false,
}: {
  obs: Observation;
  bundle: Bundle;
  content: { vw: number; vh: number };
  fontReady: boolean;
  showConfidence: boolean;
  showId: boolean;
  dashed?: boolean;
  conflict?: boolean;
}) {
  if (!obs.bbox) return null;
  const object = bundle.objects.find((item) => item.local_id === obs.object_local_id);
  const color = conflict ? "#e07070" : object?.color ?? "#7EB6FF";
  const x = obs.bbox.x * content.vw;
  const y = obs.bbox.y * content.vh;
  const w = obs.bbox.w * content.vw;
  const h = obs.bbox.h * content.vh;
  const label = showId && object ? labeledId(object.local_id, object.label) : "";
  const lines = label ? wrapLines(label, w, labelWidth) : [];
  const score = showConfidence && obs.confidence != null ? formatConfidence(obs.confidence) : "";
  const uncertain = obs.association_status === "ambiguous" || obs.continuity === "uncertain";
  return (
    <g key={`${obs.id}-${fontReady ? "font" : "fallback"}`}>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        fill={color}
        fillOpacity={dashed ? 0.04 : 0.12}
        stroke={color}
        strokeWidth={conflict ? 2.5 : 2}
        strokeDasharray={dashed || uncertain ? "6 4" : undefined}
      />
      {lines.length > 0 && (
        <text x={x} y={Math.max(LABEL_SIZE, y - 4)} fill={color} fontSize={LABEL_SIZE} fontFamily="IBM Plex Sans, sans-serif">
          {lines.map((line, index) => (
            <tspan key={index} x={x} dy={index === 0 ? 0 : LABEL_LINE}>{line}</tspan>
          ))}
        </text>
      )}
      {score && (
        <text x={x + CONFIDENCE_INSET} y={y + 14} fill={color} fontSize={11} fontFamily="IBM Plex Sans, sans-serif">{score}</text>
      )}
    </g>
  );
}

function TrackMark({
  point,
  points,
  time,
  content,
  layers,
}: {
  point: TrackPointSample;
  points: TrackPointSample[];
  time: number;
  content: { vw: number; vh: number };
  layers: { tracks: boolean; trajectory: boolean; uncertain: boolean };
}) {
  if (!point.bbox) return null;
  const uncertain = layers.uncertain && (point.continuity === "uncertain" || point.status === "ambiguous" || point.continuity === "reassociated");
  const color = uncertain ? "#d2a24c" : "#7dcea0";
  const x = point.bbox.x * content.vw;
  const y = point.bbox.y * content.vh;
  const w = point.bbox.w * content.vw;
  const h = point.bbox.h * content.vh;
  const trail = layers.trajectory
    ? points.filter((item) => item.track_ref === point.track_ref && item.bbox && item.timestamp <= time && time - item.timestamp < 1.5).slice(-12)
    : [];
  return (
    <g>
      {trail.length > 1 && (
        <polyline
          fill="none"
          stroke={color}
          strokeWidth={2}
          points={trail.map((item) => {
            const box = item.bbox;
            if (!box) return "";
            return `${(box.x + box.w / 2) * content.vw},${(box.y + box.h / 2) * content.vh}`;
          }).join(" ")}
        />
      )}
      {layers.tracks && (
        <>
          <rect x={x} y={y} width={w} height={h} fill={color} fillOpacity={0.08} stroke={color} strokeWidth={2} strokeDasharray={uncertain ? "6 4" : undefined} />
          <text x={x} y={Math.max(14, y - 4)} fill={color} fontSize={14} fontFamily="IBM Plex Sans, sans-serif">{humanize(point.track_ref)}</text>
          <text x={x} y={y + h + 14} fill={color} fontSize={12} fontFamily="IBM Plex Sans, sans-serif">{point.status || point.continuity}</text>
        </>
      )}
    </g>
  );
}

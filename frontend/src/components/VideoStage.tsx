import { useEffect, useRef, useState } from "react";
import type { Bundle } from "../lib/types";
import { observationsAt } from "../lib/analysis";
import { labeledId } from "../lib/humanize";
import { labelSpan, wrapLines } from "../lib/wrapText";
import { useInspector } from "../stores/ui";

const LABEL_SIZE = 14;
const LABEL_LINE = LABEL_SIZE * 1.2;
const LABEL_FONT = `${LABEL_SIZE}px "IBM Plex Sans", sans-serif`;

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
  const { time, setTime, playing, setPlaying, speed, overlay, loop } = useInspector();
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [content, setContent] = useState({ x: 0, y: 0, w: 1, h: 1, vw: 640, vh: 360 });
  const [fontReady, setFontReady] = useState(false);

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

  const visible = overlay ? observationsAt(bundle, time) : [];
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

  return (
    <div className="stage-slot flex min-h-0 w-full flex-col gap-2">
      <div
        ref={boxRef}
        className="stage-frame rounded-lg bg-black"
        onWheel={(event) => {
          event.preventDefault();
          setZoom((value) => Math.min(4, Math.max(1, value + (event.deltaY < 0 ? 0.15 : -0.15))));
        }}
      >
        <video
          ref={videoRef}
          src={src}
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
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
        />
        {overlay && (
          <svg
            className="pointer-events-none absolute"
            style={{ left: content.x, top: content.y, width: content.w, height: content.h, transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: "center" }}
            viewBox={`0 0 ${content.vw} ${content.vh}`}
          >
            {visible.map((obs) => {
              if (!obs.bbox) return null;
              const object = bundle.objects.find((item) => item.local_id === obs.object_local_id);
              const color = object?.color ?? "#7EB6FF";
              const x = obs.bbox.x * content.vw;
              const y = obs.bbox.y * content.vh;
              const w = obs.bbox.w * content.vw;
              const h = obs.bbox.h * content.vh;
              const others = visible.flatMap((item) => (
                item.bbox && item.id !== obs.id ? [{ x: item.bbox.x * content.vw, width: item.bbox.w * content.vw }] : []
              ));
              const span = labelSpan(x, w, others, LABEL_SIZE);
              const label = object ? labeledId(object.local_id, object.label) : "";
              const lines = label ? wrapLines(label, span.width, labelWidth) : [];
              const anchor = Math.max(LABEL_SIZE, y - 4);
              const firstBaseline = Math.max(LABEL_SIZE, anchor - Math.max(0, lines.length - 1) * LABEL_LINE);
              return (
                <g key={`${obs.id}-${fontReady ? "font" : "fallback"}`}>
                  <rect
                    x={x}
                    y={y}
                    width={w}
                    height={h}
                    fill={color}
                    fillOpacity={0.12}
                    stroke={color}
                    strokeWidth={2}
                  />
                  {lines.length > 0 && (
                    <text x={span.x} y={firstBaseline} fill={color} fontSize={LABEL_SIZE} fontFamily="IBM Plex Sans, sans-serif">
                      {lines.map((line, index) => (
                        <tspan key={index} x={span.x} dy={index === 0 ? 0 : LABEL_LINE}>{line}</tspan>
                      ))}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <button className="btn" onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
        <button className="btn" onClick={() => step(-1)}>Prev frame</button>
        <button className="btn" onClick={() => step(1)}>Next frame</button>
        <label className="inline-flex items-center gap-2 text-sm text-[var(--muted)]">
          <span>Speed</span>
          <select className="field w-auto text-[var(--text)]" value={speed} onChange={(event) => useInspector.getState().setSpeed(Number(event.target.value))}>
            {[0.25, 0.5, 1, 1.5, 2].map((value) => <option key={value} value={value}>{value}×</option>)}
          </select>
        </label>
        <button className="btn" onClick={() => useInspector.getState().setOverlay(!overlay)}>{overlay ? "Hide boxes" : "Show boxes"}</button>
        <button className="btn" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>Reset zoom</button>
        <button className="btn" onClick={() => { setPan((p) => ({ ...p, x: p.x + 20 })); }}>Pan</button>
        <span className="mono text-[var(--muted)]">{time.toFixed(2)}s</span>
        <span className="text-xs text-[var(--muted)]">Space play · arrows step · f fullscreen · scroll zoom</span>
      </div>
    </div>
  );
}

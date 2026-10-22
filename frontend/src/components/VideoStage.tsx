import { useEffect, useRef, useState } from "react";
import type { Bundle } from "../lib/types";
import { observationsAt } from "../lib/analysis";
import { useInspector } from "../stores/ui";

export function VideoStage({ bundle }: { bundle: Bundle }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const { time, setTime, playing, setPlaying, speed, overlay, loop } = useInspector();
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [content, setContent] = useState({ x: 0, y: 0, w: 1, h: 1, vw: 640, vh: 360 });

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (Math.abs(video.currentTime - time) > 0.08) video.currentTime = time;
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

  function measure() {
    const video = videoRef.current;
    const box = boxRef.current;
    if (!video || !box || !video.videoWidth) return;
    const rect = box.getBoundingClientRect();
    const scale = Math.min(rect.width / video.videoWidth, rect.height / video.videoHeight);
    const w = video.videoWidth * scale;
    const h = video.videoHeight * scale;
    setContent({ x: (rect.width - w) / 2, y: (rect.height - h) / 2, w, h, vw: video.videoWidth, vh: video.videoHeight });
  }

  const visible = overlay ? observationsAt(bundle, time) : [];
  const src = bundle.video ? bundle.video.playback_url : "";

  return (
    <div className="flex h-full min-h-[280px] flex-col gap-2">
      <div
        ref={boxRef}
        className="relative min-h-0 flex-1 overflow-hidden rounded-lg bg-black"
        onWheel={(event) => {
          event.preventDefault();
          setZoom((value) => Math.min(4, Math.max(1, value + (event.deltaY < 0 ? 0.15 : -0.15))));
        }}
      >
        <video
          ref={videoRef}
          src={src}
          className="h-full w-full object-contain"
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
          onLoadedMetadata={measure}
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
              return (
                <g key={obs.id}>
                  <rect
                    x={obs.bbox.x * content.vw}
                    y={obs.bbox.y * content.vh}
                    width={obs.bbox.w * content.vw}
                    height={obs.bbox.h * content.vh}
                    fill={color}
                    fillOpacity={0.12}
                    stroke={color}
                    strokeWidth={2}
                  />
                  <text x={obs.bbox.x * content.vw + 4} y={Math.max(14, obs.bbox.y * content.vh - 4)} fill={color} fontSize={14}>
                    {object?.local_id} {object?.label}
                  </text>
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
        <label className="text-[var(--muted)]">
          Speed
          <select className="field ml-2 w-auto" value={speed} onChange={(event) => useInspector.getState().setSpeed(Number(event.target.value))}>
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

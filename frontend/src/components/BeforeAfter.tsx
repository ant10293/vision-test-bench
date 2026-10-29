import { useLayoutEffect, useRef, useState } from "react";
import { frameNear } from "../lib/analysis";
import type { Bundle, LabAction } from "../lib/types";
import { ZoomStepper } from "./ZoomStepper";

const MIN_ZOOM = 1;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;

export function BeforeAfter({ bundle, action }: { bundle: Bundle; action: LabAction }) {
  const before = frameNear(bundle, action.start);
  const after = frameNear(bundle, action.end);
  const [zoom, setZoom] = useState(MIN_ZOOM);
  return (
    <div className="space-y-2">
      <div className="grid gap-2 md:grid-cols-2">
        <Pane title="Before" url={before?.url} note={`${before?.timestamp.toFixed(2) ?? "?"}s · sampled frame, not an interpolated state`} zoom={zoom} />
        <Pane title="After" url={after?.url} note={`${after?.timestamp.toFixed(2) ?? "?"}s`} zoom={zoom} />
      </div>
      <div className="flex justify-center">
        <ZoomStepper
          canZoomOut={zoom > MIN_ZOOM}
          canZoomIn={zoom < MAX_ZOOM}
          onZoomOut={() => setZoom((value) => Math.max(MIN_ZOOM, value - ZOOM_STEP))}
          onZoomIn={() => setZoom((value) => Math.min(MAX_ZOOM, value + ZOOM_STEP))}
        />
      </div>
      <p className="text-xs text-[var(--muted)]">Different timestamps do not by themselves prove the state changed.</p>
    </div>
  );
}

function Pane({ title, url, note, zoom }: { title: string; url?: string; note: string; zoom: number }) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [fitHeight, setFitHeight] = useState<number | null>(null);

  useLayoutEffect(() => {
    const image = imageRef.current;
    if (!image || zoom !== MIN_ZOOM) return;
    const measure = () => {
      if (image.clientHeight > 0) setFitHeight(image.clientHeight);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(image);
    return () => observer.disconnect();
  }, [zoom, url]);

  return (
    <figure className="panel overflow-hidden">
      <div
        className="overflow-auto bg-black"
        style={zoom > MIN_ZOOM && fitHeight ? { height: fitHeight } : undefined}
      >
        {url ? (
          <img
            ref={imageRef}
            src={url}
            alt={title}
            style={{ width: `${zoom * 100}%`, maxWidth: "none" }}
            className="block h-auto"
          />
        ) : (
          <div className="p-6 text-sm text-[var(--muted)]">No frame</div>
        )}
      </div>
      <figcaption className="px-2 py-1 text-xs text-[var(--muted)]">{title} · {note}</figcaption>
    </figure>
  );
}

import { useState } from "react";
import { frameNear } from "../lib/analysis";
import type { Bundle, LabAction } from "../lib/types";

export function BeforeAfter({ bundle, action }: { bundle: Bundle; action: LabAction }) {
  const before = frameNear(bundle, action.start);
  const after = frameNear(bundle, action.end);
  const [swap, setSwap] = useState(false);
  const [zoom, setZoom] = useState(1);
  const left = swap ? after : before;
  const right = swap ? before : after;
  return (
    <div className="space-y-2">
      <div className="flex gap-2 text-xs">
        <button className="btn" onClick={() => setSwap((value) => !value)}>Swap</button>
        <button className="btn" onClick={() => setZoom((value) => Math.min(3, value + 0.25))}>Zoom</button>
        <button className="btn" onClick={() => setZoom(1)}>Reset</button>
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        <Pane title={swap ? "After" : "Before"} url={left?.url} note={`${left?.timestamp.toFixed(2) ?? "?"}s · sampled frame, not an interpolated state`} zoom={zoom} />
        <Pane title={swap ? "Before" : "After"} url={right?.url} note={`${right?.timestamp.toFixed(2) ?? "?"}s`} zoom={zoom} />
      </div>
      <p className="text-xs text-[var(--muted)]">Different timestamps do not by themselves prove the state changed.</p>
    </div>
  );
}

function Pane({ title, url, note, zoom }: { title: string; url?: string; note: string; zoom: number }) {
  return (
    <figure className="panel overflow-hidden">
      <div className="overflow-auto bg-black">
        {url ? <img src={url} alt={title} style={{ transform: `scale(${zoom})`, transformOrigin: "top left" }} className="w-full" /> : <div className="p-6 text-sm text-[var(--muted)]">No frame</div>}
      </div>
      <figcaption className="px-2 py-1 text-xs text-[var(--muted)]">{title} · {note}</figcaption>
    </figure>
  );
}

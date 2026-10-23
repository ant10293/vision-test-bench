import type { ReactNode } from "react";
import { formatTime } from "../lib/api";
import { Bone } from "./Skeleton";
import { humanize } from "../lib/humanize";
import type { Bundle } from "../lib/types";
import { useInspector } from "../stores/ui";

const TRACKS = ["frames", "objects", "actions", "states", "ambiguities", "steps"] as const;

export function Timeline({ bundle = null }: { bundle?: Bundle | null }) {
  const duration = Math.max(bundle?.video?.duration_sec ?? 1, 0.1);
  const { time, setTime, selectAction, selectStep, selectObject } = useInspector();

  return (
    <div className="space-y-1">
      <div className="relative h-4 overflow-hidden">
        {bundle ? (
          <input
            aria-label="Seek"
            className="w-full"
            type="range"
            min={0}
            max={duration}
            step={0.01}
            value={Math.min(time, duration)}
            onChange={(event) => setTime(Number(event.target.value))}
          />
        ) : (
          <Bone className="bone-fill" />
        )}
      </div>
      {TRACKS.map((track) =>
        bundle ? (
          <Track key={track} name={track} duration={duration} bundle={bundle} onSeek={setTime} onAction={selectAction} onStep={selectStep} onObject={selectObject} />
        ) : (
          <TrackLane key={track} name={track}>
            <Bone className="bone-fill" />
          </TrackLane>
        ),
      )}
    </div>
  );
}

function TrackLane({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[88px_1fr] items-center gap-2">
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{name}</div>
      <div className="relative h-6 overflow-hidden rounded bg-[#0d1016]">{children}</div>
    </div>
  );
}

function Track({
  name,
  duration,
  bundle,
  onSeek,
  onAction,
  onStep,
  onObject,
}: {
  name: (typeof TRACKS)[number];
  duration: number;
  bundle: Bundle;
  onSeek: (time: number) => void;
  onAction: (id: string) => void;
  onStep: (id: string) => void;
  onObject: (id: string) => void;
}) {
  return (
    <TrackLane name={name}>
      {segments(name, bundle).map((item) => (
          <button
            key={item.key}
            title={`${item.label} · ${formatTime(item.start)}–${formatTime(item.end)}`}
            className="absolute top-1 h-4 rounded-sm text-left"
            style={{
              left: `${(item.start / duration) * 100}%`,
              width: `${Math.max(((item.end - item.start) / duration) * 100, 0.8)}%`,
              background: item.color,
            }}
            onClick={() => {
              onSeek(item.start);
              if (item.actionId) onAction(item.actionId);
              if (item.stepId) onStep(item.stepId);
              if (item.objectId) onObject(item.objectId);
            }}
            onMouseEnter={(event) => {
              const frame = bundle.frames.find((candidate) => Math.abs(candidate.timestamp - item.start) < 0.6);
              if (!frame) return;
              event.currentTarget.title = `${item.label}\n${formatTime(item.start)}`;
            }}
          />
        ))}
    </TrackLane>
  );
}

interface Segment {
  key: string;
  start: number;
  end: number;
  label: string;
  color: string;
  actionId?: string;
  stepId?: string;
  objectId?: string;
}

function segments(name: (typeof TRACKS)[number], bundle: Bundle): Segment[] {
  if (name === "frames") {
    return bundle.frames.map((frame) => ({
      key: frame.id,
      start: frame.timestamp,
      end: frame.timestamp + 0.08,
      label: humanize(frame.reason),
      color: frame.scene_change ? "#e06c75" : "#465066",
    }));
  }
  if (name === "actions") {
    return bundle.actions.map((action) => ({
      key: action.id,
      start: action.start,
      end: Math.max(action.end, action.start + 0.05),
      label: `${humanize(action.action_type)} · ${humanize(action.outcome)}`,
      color: action.outcome === "completed" ? "#1f6b4a" : action.outcome === "attempted" ? "#8a6a22" : "#5c3a55",
      actionId: action.local_id,
    }));
  }
  if (name === "objects") {
    return bundle.objects.flatMap((object) => {
      const times = bundle.observations.filter((item) => item.object_local_id === object.local_id).map((item) => item.timestamp);
      if (!times.length) return [];
      return [{
        key: object.id,
        start: Math.min(...times),
        end: Math.max(...times),
        label: object.label,
        color: object.color,
        objectId: object.local_id,
      }];
    });
  }
  if (name === "states") {
    return bundle.transitions.map((item) => ({
      key: item.id,
      start: item.timestamp,
      end: item.timestamp + 0.12,
      label: `${humanize(item.from_state)} → ${humanize(item.to_state)}`,
      color: "#31597a",
      objectId: item.object_local_id,
    }));
  }
  if (name === "ambiguities") {
    return bundle.ambiguities.map((item) => ({
      key: item.id,
      start: item.time_start ?? 0,
      end: item.time_end ?? (item.time_start ?? 0) + 0.2,
      label: item.description,
      color: "#6a3036",
    }));
  }
  return bundle.steps.map((step) => ({
    key: step.id,
    start: step.time_start ?? 0,
    end: step.time_end ?? (step.time_start ?? 0) + 0.2,
    label: step.name,
    color: "#2c4a3d",
    stepId: step.local_id,
  }));
}

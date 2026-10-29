import { humanize } from "./humanize";
import type { Bundle, LabObject, Observation, TrackPointSample } from "./types";

export type FrameObjectStill = {
  id: string;
  url: string;
  label: string;
  color: string;
  confidence: number | null;
};

/** Crops of the labeled objects visible at the playhead, one still per object. */
export function frameObjectStills(bundle: Bundle, time: number): FrameObjectStill[] {
  return observationsAt(bundle, time).flatMap((observation) => {
    const object = bundle.objects.find((item) => item.local_id === observation.object_local_id);
    const url = stillUrl(object, observation);
    if (!url) return [];
    const name = object?.label?.trim() ? humanize(object.label) : humanize(observation.object_local_id);
    return [{
      id: observation.id,
      url,
      label: name,
      color: object?.color ?? "#7EB6FF",
      confidence: observation.confidence ?? null,
    }];
  });
}

function stillUrl(object: LabObject | undefined, observation: Observation): string {
  if (observation.frame_id) return `/api/media/observations/${observation.id}/crop`;
  const images = object?.images ?? [];
  const stills = images.filter((image) => image.url && image.kind !== "boxed" && !image.kind.endsWith("_boxed"));
  const nearest = stills.reduce<(typeof stills)[number] | undefined>((best, image) => {
    if (!best) return image;
    return Math.abs(image.timestamp - observation.timestamp) < Math.abs(best.timestamp - observation.timestamp) ? image : best;
  }, undefined);
  return nearest?.url || object?.primary_image_url || "";
}

export function observationsAt(bundle: Bundle, time: number, window = 0.45): Observation[] {
  const chosen: Observation[] = [];
  for (const object of bundle.objects) {
    const samples = bundle.observations.filter((item) => item.object_local_id === object.local_id && item.bbox);
    if (!samples.length) continue;
    const nearest = samples.reduce((best, item) =>
      Math.abs(item.timestamp - time) < Math.abs(best.timestamp - time) ? item : best,
    );
    if (Math.abs(nearest.timestamp - time) <= window) chosen.push(nearest);
  }
  return chosen;
}

export function tracksAt(points: TrackPointSample[], time: number, window = 0.2): TrackPointSample[] {
  const nearest = new Map<string, TrackPointSample>();
  for (const point of points) {
    if (!point.bbox) continue;
    const gap = Math.abs(point.timestamp - time);
    if (gap > window) continue;
    const current = nearest.get(point.track_ref);
    if (!current || gap < Math.abs(current.timestamp - time)) nearest.set(point.track_ref, point);
  }
  return [...nearest.values()];
}

export type ObjectTimelineCell = {
  frameId: string;
  timestamp: number;
  observationId: string | null;
  url: string | null;
  confidence: number | null;
};

/** One square per sampled frame. A crop when this object is in the frame, otherwise an empty cell. */
export function objectFrameTimeline(
  frames: { id: string; timestamp: number }[] | null | undefined,
  localId: string,
  observations: Observation[],
): ObjectTimelineCell[] {
  const ordered = [...(frames ?? [])].sort((a, b) => a.timestamp - b.timestamp || a.id.localeCompare(b.id));
  if (!ordered.length) return [];
  const byId = new Map(ordered.map((frame) => [frame.id, frame]));
  const present = observations.filter(
    (item) => item.object_local_id === localId && item.bbox != null && item.visibility !== "absent",
  );
  const chosen = new Map<string, Observation>();
  const claimed = new Set<string>();

  for (const observation of present) {
    const frame = observation.frame_id ? byId.get(observation.frame_id) : undefined;
    if (!frame) continue;
    claimed.add(observation.id);
    const current = chosen.get(frame.id);
    if (current && !closerObservation(observation, current, frame.timestamp)) continue;
    chosen.set(frame.id, observation);
  }

  const loose = present.filter((item) => !claimed.has(item.id) && item.frame_id);
  const options: { observation: Observation; frameId: string; gap: number }[] = [];
  for (const observation of loose) {
    ordered.forEach((frame, index) => {
      const gap = Math.abs(frame.timestamp - observation.timestamp);
      if (gap > frameSlack(ordered, index)) return;
      options.push({ observation, frameId: frame.id, gap });
    });
  }
  options.sort((a, b) => a.gap - b.gap || a.observation.id.localeCompare(b.observation.id));
  const used = new Set<string>();
  for (const option of options) {
    if (used.has(option.observation.id) || chosen.has(option.frameId)) continue;
    chosen.set(option.frameId, option.observation);
    used.add(option.observation.id);
  }

  return ordered.map((frame) => {
    const observation = chosen.get(frame.id);
    const url = observation?.frame_id ? `/api/media/observations/${observation.id}/crop` : null;
    return {
      frameId: frame.id,
      timestamp: frame.timestamp,
      observationId: url ? observation?.id ?? null : null,
      url,
      confidence: url ? observation?.confidence ?? null : null,
    };
  });
}

/** Half the gap to the nearer neighbor, so a detection lands on one frame. */
function frameSlack(frames: { timestamp: number }[], index: number): number {
  const time = frames[index]?.timestamp ?? 0;
  const gaps: number[] = [];
  if (index > 0) gaps.push(time - frames[index - 1].timestamp);
  if (index < frames.length - 1) gaps.push(frames[index + 1].timestamp - time);
  if (!gaps.length) return 0.5;
  return Math.max(Math.min(...gaps) / 2, 0.001);
}

function closerObservation(next: Observation, current: Observation, frameTime: number): boolean {
  const nextGap = Math.abs(next.timestamp - frameTime);
  const currentGap = Math.abs(current.timestamp - frameTime);
  if (nextGap !== currentGap) return nextGap < currentGap;
  const nextScore = next.confidence ?? -1;
  const currentScore = current.confidence ?? -1;
  if (nextScore !== currentScore) return nextScore > currentScore;
  return next.id.localeCompare(current.id) < 0;
}

export function frameNear(bundle: Bundle, time: number) {
  if (!bundle.frames.length) return undefined;
  return bundle.frames.reduce((best, frame) =>
    Math.abs(frame.timestamp - time) < Math.abs(best.timestamp - time) ? frame : best,
  );
}

export function objectColor(bundle: Bundle, localId: string): string {
  return bundle.objects.find((item) => item.local_id === localId)?.color ?? "#7EB6FF";
}

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

export function frameNear(bundle: Bundle, time: number) {
  if (!bundle.frames.length) return undefined;
  return bundle.frames.reduce((best, frame) =>
    Math.abs(frame.timestamp - time) < Math.abs(best.timestamp - time) ? frame : best,
  );
}

export function objectColor(bundle: Bundle, localId: string): string {
  return bundle.objects.find((item) => item.local_id === localId)?.color ?? "#7EB6FF";
}

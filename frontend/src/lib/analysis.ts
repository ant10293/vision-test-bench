import type { Bundle, Observation, TrackPointSample } from "./types";

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

import type { BBox, Bundle, TrackPointSample } from "./types";

/** How far a depth order may sit from the box's own timestamp. */
const SAMPLE_WINDOW = 0.25;

export interface GeometryBox {
  id: string;
  object_local_id: string;
  timestamp: number;
  bbox: BBox | null;
}

export interface BoxGeometry {
  /** "1.3× · +24°", or only the part that was measured. Empty when nothing was measured. */
  text: string;
  /** A clear depth order puts this box in front of another visible box. */
  front: boolean;
}

export function formatScale(value: number): string {
  return `${value.toFixed(1)}×`;
}

export function formatTurn(value: number): string {
  const rounded = Math.round(value);
  return rounded > 0 ? `+${rounded}°` : `${rounded}°`;
}

function label(scale: number | null | undefined, angle: number | null | undefined): string {
  const parts: string[] = [];
  if (scale != null) parts.push(formatScale(scale));
  if (angle != null) parts.push(formatTurn(angle));
  return parts.join(" · ");
}

function boxKey(localId: string, timestamp: number): string {
  return `${localId}|${timestamp.toFixed(3)}`;
}

/** Scale, turn, and front badge for each visible object box, keyed by box id.
 *  Values are measured on the object boxes themselves by the server. */
export function boxGeometries(bundle: Bundle, boxes: GeometryBox[]): Map<string, BoxGeometry> {
  const measured = new Map(
    (bundle.object_geometry?.boxes ?? []).map((item) => [boxKey(item.object_local_id, item.timestamp), item]),
  );
  const result = new Map<string, BoxGeometry>();
  for (const box of boxes) {
    const sample = measured.get(boxKey(box.object_local_id, box.timestamp));
    result.set(box.id, { text: label(sample?.relative_scale, sample?.angle_deg), front: inFront(bundle, box, boxes) });
  }
  return result;
}

function inFront(bundle: Bundle, box: GeometryBox, boxes: GeometryBox[]): boolean {
  const others = new Set(boxes.filter((item) => item.id !== box.id && item.bbox).map((item) => item.object_local_id));
  others.delete(box.object_local_id);
  const near = (start: number, end: number) => box.timestamp >= start - SAMPLE_WINDOW && box.timestamp <= end + SAMPLE_WINDOW;
  const fromBoxes = (bundle.object_geometry?.orders ?? []).some((order) =>
    order.nearer === box.object_local_id && others.has(order.farther) && near(order.time_start, order.time_end),
  );
  return fromBoxes || (bundle.relationships ?? []).some((item) =>
    item.kind === "in_front_of"
    && item.subject_local_id === box.object_local_id
    && item.other_local_id != null
    && others.has(item.other_local_id)
    && near(item.time_start, item.time_end),
  );
}

/** The label for a tracker box itself. Front needs a depth order against another track in view. */
export function trackGeometry(bundle: Bundle, point: TrackPointSample, visibleRefs: Set<string>): BoxGeometry {
  const front = (bundle.track_depth_orders ?? []).some((order) =>
    order.nearer === point.track_ref
    && order.farther !== point.track_ref
    && visibleRefs.has(order.farther)
    && point.timestamp >= order.time_start - SAMPLE_WINDOW
    && point.timestamp <= order.time_end + SAMPLE_WINDOW,
  );
  return { text: label(point.relative_scale, point.angle_deg), front };
}

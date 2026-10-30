"""Scale, turn, and depth order measured on a run's own object boxes.

The object boxes come from a model or a person, not from the tracker, and often
do not line up with any track. They are measured here with the same gates as
the tracks and stored beside the run for the overlay. They are not written back
into the analysis document, so they never become a verification record.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from app.perception.depth import load_depth
from app.perception.geometry import _order_intervals, annotate_geometry
from app.perception.presets import config_from_mapping
from app.perception.types import DepthUnavailable, TrackPoint
from app.services.storage import data_path

log = logging.getLogger(__name__)

CACHE_NAME = "object_geometry.json"
# Bumped when the measurement changes, so stored results are recomputed.
VERSION = 2
# An observation and a frame closer than this are the same sampled instant.
FRAME_MATCH = 0.05
# Two object boxes that overlap are usually both marked partial, since one covers the
# other. The shared area is left out of each median, so partial boxes still enter depth
# order. A hand over the uncovered part can still make a box read nearer.
DEPTH_VISIBILITY = ("visible", "partial")


@dataclass
class ObjectBox:
    object_local_id: str
    timestamp: float
    bbox: dict
    visibility: str


def object_geometry(run_id: str, boxes: list[ObjectBox], frames: list[tuple[float, str]], viewpoint: str, config: dict | None) -> dict:
    """Stored result when the boxes are unchanged, otherwise a fresh measurement."""
    viewpoint = viewpoint if viewpoint in {"fixed", "head_mounted"} else "head_mounted"
    depth_id = config_from_mapping((config or {}).get("perception")).depth if viewpoint == "fixed" else None
    signature = _signature(boxes, frames, viewpoint, depth_id)
    path = data_path("runs", run_id, CACHE_NAME)
    if path.exists():
        try:
            stored = json.loads(path.read_text())
        except json.JSONDecodeError:
            stored = {}
        if stored.get("signature") == signature:
            return stored
    try:
        result = measure_boxes(boxes, frames, viewpoint, depth_id)
    except Exception as exc:  # display-only data must not take the run page down
        log.exception("object geometry failed for run %s", run_id)
        return {"boxes": [], "orders": [], "status": "failed", "detail": str(exc)}
    result["signature"] = signature
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps(result))
    partial.replace(path)
    return result


def measure_boxes(boxes: list[ObjectBox], frames: list[tuple[float, str]], viewpoint: str, depth_id: str | None, depth=None) -> dict:
    ordered = sorted(frames)
    points: list[TrackPoint] = []
    source: dict[int, ObjectBox] = {}
    taken: set[tuple[str, float]] = set()
    for box in boxes:
        frame_time = _frame_time(ordered, box.timestamp)
        bbox = box.bbox or {}
        if frame_time is None or not {"x", "y", "w", "h"} <= bbox.keys():
            continue
        if (box.object_local_id, frame_time) in taken:
            continue
        taken.add((box.object_local_id, frame_time))
        point = TrackPoint(
            track_ref=box.object_local_id,
            timestamp=frame_time,
            x=float(bbox["x"]),
            y=float(bbox["y"]),
            w=float(bbox["w"]),
            h=float(bbox["h"]),
            motion_score=0.0,
            continuity="continuous",
            camera_compensated=False,
            scene_chunk=0,
            visibility=box.visibility or "visible",
        )
        points.append(point)
        source[id(point)] = box
    status, detail = "not_requested", ""
    if depth is None and depth_id:
        try:
            depth = load_depth(depth_id)
        except DepthUnavailable as exc:
            status, detail = "unavailable", str(exc)
    elif viewpoint != "fixed":
        status, detail = "skipped", "Depth order is only estimated for a fixed camera."
    report = annotate_geometry(
        points,
        [data_path(path) for _, path in ordered],
        [time for time, _ in ordered],
        viewpoint,
        depth=depth,
        depth_status=status,
        depth_detail=detail,
        depth_visibility=DEPTH_VISIBILITY,
    )
    return {
        "version": VERSION,
        "viewpoint": viewpoint,
        "coordinate_frame": "image",
        "boxes": [
            {
                "object_local_id": point.track_ref,
                "timestamp": source[id(point)].timestamp,
                "relative_scale": point.relative_scale,
                "angle_deg": point.angle_deg,
            }
            for point in points
            if point.relative_scale is not None or point.angle_deg is not None
        ],
        "orders": [
            {"nearer": nearer, "farther": farther, "time_start": start, "time_end": end}
            for nearer, farther, start, end, _ in _order_intervals(report.orders)
        ],
        "status": "measured",
        "depth_status": report.depth_status,
        "depth_detail": report.depth_detail,
    }


def _frame_time(ordered: list[tuple[float, str]], timestamp: float) -> float | None:
    best = min(ordered, key=lambda frame: abs(frame[0] - timestamp), default=None)
    if best is None or abs(best[0] - timestamp) > FRAME_MATCH:
        return None
    return best[0]


def _signature(boxes: list[ObjectBox], frames: list[tuple[float, str]], viewpoint: str, depth_id: str | None) -> str:
    payload = json.dumps(
        {
            "version": VERSION,
            "viewpoint": viewpoint,
            "depth": depth_id,
            "frames": sorted(frames),
            "boxes": sorted(
                json.dumps([box.object_local_id, round(box.timestamp, 3), box.bbox, box.visibility], sort_keys=True)
                for box in boxes
            ),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(payload.encode()).hexdigest()

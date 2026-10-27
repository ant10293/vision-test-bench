"""Build a kinematic scene from the regions tracked in one video.

Each shape is a solid in front of the camera. Its center stays on the ray
through the tracked box. Distance is chosen so the solids are not coplanar.
That distance is not a measurement from the footage.
"""

from __future__ import annotations

import numpy as np

from app.simulation.math3d import intrinsics
from app.simulation.schema import (
    CameraKey,
    CameraRig,
    Environment,
    Expectation,
    Light,
    Material,
    Operation,
    Piece,
    Scene,
    SceneObject,
)

PLANE_DEPTH_M = 2.0
DEPTH_NEAR = 0.75
DEPTH_FAR = 1.25
THICKNESS = 0.5
FOV_X_DEG = 48.0
EYE = (0.0, -PLANE_DEPTH_M, 0.0)
TARGET = (0.0, 0.0, 0.0)
_COLORS = (
    (0.85, 0.35, 0.28),
    (0.25, 0.55, 0.85),
    (0.35, 0.72, 0.42),
    (0.90, 0.72, 0.25),
    (0.62, 0.40, 0.78),
    (0.90, 0.50, 0.20),
    (0.30, 0.75, 0.75),
    (0.85, 0.45, 0.65),
)


def records_from_document(document) -> list[dict]:
    """Boxes for the analysis objects. Motion-track ids are not objects."""
    labels = {
        obj.local_id: (obj.label or obj.local_id)
        for obj in document.objects
        if getattr(obj, "accepted", True)
    }
    rows = []
    for obs in document.observations:
        if not getattr(obs, "accepted", True) or obs.object_local_id not in labels or obs.bbox is None:
            continue
        visibility = getattr(obs.visibility, "value", obs.visibility)
        if str(visibility) == "absent":
            continue
        bbox = obs.bbox.model_dump() if hasattr(obs.bbox, "model_dump") else dict(obs.bbox)
        rows.append(
            {
                "track_ref": obs.object_local_id,
                "label": labels[obs.object_local_id],
                "timestamp": float(obs.timestamp),
                "bbox": bbox,
            }
        )
    return rows


def scene_from_tracks(
    tracks: list[dict],
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    scene_id: str,
    name: str = "",
) -> Scene:
    """tracks items have track_ref, timestamp, and bbox {x, y, w, h} in 0..1."""
    frame = _fit_frame(width, height)
    span = _span(duration, tracks)
    rate = _rate(fps)
    grouped = _group(tracks, span)
    if not grouped:
        raise ValueError("The analysis has no object boxes, so there is nothing to place in the Blender scene.")
    names = instance_ids_for(tracks, duration)
    depths = _depths(grouped)
    objects = []
    operations = []
    for index, (track_ref, rows) in enumerate(grouped.items()):
        instance_id = names[track_ref]
        depth = depths[track_ref]
        poses = [_place(row["bbox"], frame[0], frame[1], depth) for row in rows]
        size = _median_size(poses)
        objects.append(
            SceneObject(
                instance_id=instance_id,
                label=str(rows[0].get("label") or track_ref or instance_id),
                description="Placed on this object's boxes in the selected video.",
                pieces=[Piece(name="region", kind="box", size=size)],
                material=Material(color=_COLORS[index % len(_COLORS)], roughness=0.55),
                position=poses[0][0],
            )
        )
        previous = float(rows[0]["timestamp"])
        for step, (pose, _size) in enumerate(poses[1:], start=1):
            end = min(span, float(rows[step]["timestamp"]))
            if end <= previous:
                continue
            operations.append(
                Operation(
                    id=f"{instance_id}-{step}",
                    object_id=instance_id,
                    kind="translate",
                    start=previous,
                    end=end,
                    position=pose,
                )
            )
            previous = end
    return Scene(
        scene_id=scene_id,
        name=name or "Tracked video",
        description=(
            "Shapes follow the objects found in the selected video. "
            "Each one is a solid. Larger boxes sit closer to the camera. That distance is not a measurement."
        ),
        seed=1,
        width=frame[0],
        height=frame[1],
        fps=rate,
        duration=span,
        environment=Environment(),
        objects=objects,
        constraints=[],
        operations=operations,
        camera=CameraRig(
            mode="fixed",
            fov_x_deg=FOV_X_DEG,
            keys=[CameraKey(t=0.0, eye=EYE, target=TARGET)],
        ),
        lights=[
            Light(id="key", position=(0.4, -1.2, 1.2), energy=180.0),
            Light(id="fill", position=(-0.6, -1.0, 0.8), energy=60.0, color=(0.85, 0.9, 1.0)),
        ],
        procedure=[],
        variant="tracked",
        camera_mode="fixed",
        expectation=Expectation(
            final_constraints={},
            summary="The shapes follow the objects found in the selected video.",
        ),
    )


def _place(
    bbox: dict,
    width: int,
    height: int,
    depth: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    x = float(bbox["x"])
    y = float(bbox["y"])
    w = float(bbox["w"])
    h = float(bbox["h"])
    camera = intrinsics(width, height, FOV_X_DEG)
    u = (x + w / 2.0) * width
    v = (y + h / 2.0) * height
    x_cam = (u - camera["cx"]) * depth / camera["fx"]
    y_cam = (camera["cy"] - v) * depth / camera["fy"]
    width_m = max(w * width * depth / camera["fx"], 0.02)
    height_m = max(h * height * depth / camera["fy"], 0.02)
    # Camera sits on -Y and looks toward +Y. Optical depth is world Y plus that offset.
    return (x_cam, depth - PLANE_DEPTH_M, y_cam), (width_m, _thickness(width_m, height_m), height_m)


def _thickness(width_m: float, height_m: float) -> float:
    return max(min(width_m, height_m) * THICKNESS, 0.02)


def _median_size(poses: list[tuple[tuple[float, float, float], tuple[float, float, float]]]) -> tuple[float, float, float]:
    widths = sorted(item[1][0] for item in poses)
    heights = sorted(item[1][2] for item in poses)
    mid = len(poses) // 2
    width_m = widths[mid]
    height_m = heights[mid]
    return (width_m, _thickness(width_m, height_m), height_m)


def _depths(grouped: dict[str, list[dict]]) -> dict[str, float]:
    """Larger boxes are closer. One object stays on the reference depth."""
    areas = {ref: _median_area(rows) for ref, rows in grouped.items()}
    ordered = sorted(areas, key=lambda ref: areas[ref], reverse=True)
    if len(ordered) < 2:
        return {ref: PLANE_DEPTH_M for ref in ordered}
    span = DEPTH_FAR - DEPTH_NEAR
    return {
        ref: PLANE_DEPTH_M * (DEPTH_NEAR + span * (index / (len(ordered) - 1)))
        for index, ref in enumerate(ordered)
    }


def _median_area(rows: list[dict]) -> float:
    areas = sorted(float(row["bbox"]["w"]) * float(row["bbox"]["h"]) for row in rows)
    return areas[len(areas) // 2]


def _group(tracks: list[dict], span: float) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in tracks:
        bbox = item.get("bbox") or {}
        try:
            width = float(bbox.get("w", 0))
            height = float(bbox.get("h", 0))
            timestamp = float(item.get("timestamp", 0))
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0 or not np.isfinite([width, height, timestamp]).all():
            continue
        if timestamp < -1e-6 or timestamp > span + 1e-6:
            continue
        ref = str(item.get("track_ref") or "")
        if not ref:
            continue
        grouped.setdefault(ref, []).append(
            {
                "timestamp": min(span, max(0.0, timestamp)),
                "bbox": bbox,
                "label": str(item.get("label") or ref),
            }
        )
    for rows in grouped.values():
        rows.sort(key=lambda row: row["timestamp"])
        kept = []
        for row in rows:
            if kept and abs(kept[-1]["timestamp"] - row["timestamp"]) < 1e-4:
                kept[-1] = row
                continue
            kept.append(row)
        rows[:] = kept
    return {ref: rows for ref, rows in grouped.items() if rows}


def instance_ids_for(tracks: list[dict], duration: float) -> dict[str, str]:
    """Stable object ids for the same records the scene is built from."""
    grouped = _group(tracks, _span(duration, tracks))
    used: set[str] = set()
    return {ref: _instance_id(ref, index, used) for index, ref in enumerate(grouped)}


def _instance_id(track_ref: str, index: int, used: set[str]) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in track_ref).strip("-")
    candidate = cleaned or f"region-{index + 1}"
    if candidate in used:
        candidate = f"{candidate}-{index + 1}"
    used.add(candidate)
    return candidate


def _fit_frame(width: int, height: int) -> tuple[int, int]:
    if width < 2 or height < 2:
        return 320, 240
    scale = min(1280 / width, 720 / height)
    fitted_w = max(1, int(round(width * scale)))
    fitted_h = max(1, int(round(height * scale)))
    if fitted_w < 160 or fitted_h < 120:
        grow = max(160 / fitted_w, 120 / fitted_h)
        fitted_w = int(round(fitted_w * grow))
        fitted_h = int(round(fitted_h * grow))
    return _even(fitted_w, 160, 1280), _even(fitted_h, 120, 720)


def _even(value: int, low: int, high: int) -> int:
    """libx264 with yuv420p rejects an odd width or height."""
    value = min(high, max(low, value))
    if value % 2 == 0:
        return value
    if value < high:
        return value + 1
    return value - 1


def _span(duration: float, tracks: list[dict]) -> float:
    observed = [float(item["timestamp"]) for item in tracks if _finite_time(item.get("timestamp"))]
    span = float(duration) if duration and duration > 0 else (max(observed) if observed else 1.0)
    if span <= 0:
        span = 1.0
    return min(span, 12.0)


def _finite_time(value) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _rate(fps: float) -> int:
    if not fps or fps < 2:
        return 8
    return min(24, max(2, int(round(fps))))

"""Build a kinematic scene from the objects found in one video.

The objects lie on one horizontal surface, and one face points at that surface.
A fixed camera looks straight down. A head-mounted camera looks along the
surface, from the near side or from a corner. A rotated box turns the object
in the surface when the frames provide one. None of this is a measured pose.
"""

from __future__ import annotations

import numpy as np

from app.simulation.math3d import camera_matrix, intrinsics
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
THICKNESS = 0.5
FOV_X_DEG = 48.0
_FLOOR_TILT_DEG = -90.0
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
    """Boxes for the analysis objects. Motion-track ids are not objects.

    ``accepted`` is not a filter. The model sets it false on predictions the
    overlay still draws, and those are the boxes this scene places.
    """
    labels = {obj.local_id: (obj.label or obj.local_id) for obj in document.objects}
    rows = []
    for obs in document.observations:
        if obs.object_local_id not in labels or obs.bbox is None:
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
    research_mode: str = "fixed_camera",
) -> Scene:
    """tracks items have track_ref, timestamp, and bbox {x, y, w, h} in 0..1.

    An optional oriented box is {cx, cy, w, h, angle_deg}. w and h are fractions
    of the frame. angle_deg turns the width edge in the floor.
    """
    frame = _fit_frame(width, height)
    span = _span(duration, tracks)
    rate = _rate(fps)
    grouped = _group(tracks, span)
    if not grouped:
        raise ValueError("The analysis has no object boxes, so there is nothing to place in the Blender scene.")
    names = instance_ids_for(tracks, duration)
    camera_mode, rig = _rig(research_mode)
    eye = rig.keys[0].eye
    target = rig.keys[0].target
    objects = []
    operations = []
    for index, (track_ref, rows) in enumerate(grouped.items()):
        instance_id = names[track_ref]
        poses = [_place(row, frame[0], frame[1], eye, target) for row in rows]
        size = poses[0][1]
        objects.append(
            SceneObject(
                instance_id=instance_id,
                label=str(rows[0].get("label") or track_ref or instance_id),
                description="Placed on this object's boxes in the selected video.",
                pieces=[Piece(name="region", kind="box", size=size)],
                material=Material(color=_COLORS[index % len(_COLORS)], roughness=0.55),
                position=poses[0][0],
                rotation_deg=poses[0][2],
            )
        )
        previous = float(rows[0]["timestamp"])
        for step, (pose, _size, rotation) in enumerate(poses[1:], start=1):
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
                    rotation_deg=rotation,
                )
            )
            previous = end
    return Scene(
        scene_id=scene_id,
        name=name or "Tracked video",
        description=(
            "Shapes follow the objects found in the selected video. "
            "They lie on one horizontal surface, and one face points at that surface. "
            "A rotated box can turn an object in the surface. That is not a measured pose."
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
        camera=rig,
        lights=[
            Light(id="key", position=(0.4, -1.2, 1.2), energy=180.0),
            Light(id="fill", position=(-0.6, -1.0, 0.8), energy=60.0, color=(0.85, 0.9, 1.0)),
        ],
        procedure=[],
        variant="tracked",
        camera_mode=camera_mode,
        expectation=Expectation(
            final_constraints={},
            summary="The shapes follow the objects found in the selected video.",
        ),
    )


def _rig(research_mode: str) -> tuple[str, CameraRig]:
    """The camera changes with the recording. The floor stays z = 0."""
    if research_mode == "constrained_head_mounted":
        mode = "constrained_head"
        eye = (0.0, -1.55, 1.15)
        target = (0.0, 0.25, 0.0)
    elif research_mode == "unconstrained_head_mounted":
        mode = "unconstrained_head"
        eye = (1.15, -1.15, 1.05)
        target = (0.0, 0.15, 0.0)
    else:
        mode = "fixed"
        eye = (0.0, 0.0, PLANE_DEPTH_M)
        target = (0.0, 0.0, 0.0)
    rig = CameraRig(mode=mode, fov_x_deg=FOV_X_DEG, keys=[CameraKey(t=0.0, eye=eye, target=target)])
    return mode, rig


def _place(
    row: dict,
    width: int,
    height: int,
    eye: tuple[float, float, float],
    target: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    oriented = row.get("oriented") if isinstance(row.get("oriented"), dict) else None
    bbox = row["bbox"]
    if oriented:
        cx = float(oriented["cx"])
        cy = float(oriented["cy"])
        box_w = float(oriented["w"])
        box_h = float(oriented["h"])
        yaw = float(oriented.get("angle_deg") or 0.0)
    else:
        cx = float(bbox["x"]) + float(bbox["w"]) / 2.0
        cy = float(bbox["y"]) + float(bbox["h"]) / 2.0
        box_w = float(bbox["w"])
        box_h = float(bbox["h"])
        yaw = 0.0
    point, depth = _on_floor(cx * width, cy * height, width, height, eye, target)
    camera = intrinsics(width, height, FOV_X_DEG)
    width_m = max(box_w * width * depth / camera["fx"], 0.02)
    height_m = max(box_h * height * depth / camera["fy"], 0.02)
    size = (width_m, _thickness(width_m, height_m), height_m)
    return point, size, (_FLOOR_TILT_DEG, 0.0, yaw)


def _on_floor(
    u: float,
    v: float,
    width: int,
    height: int,
    eye: tuple[float, float, float],
    target: tuple[float, float, float],
) -> tuple[tuple[float, float, float], float]:
    """Where the pixel's ray meets the floor. The returned depth is along the ray."""
    cam = camera_matrix(np.asarray(eye, dtype=float), np.asarray(target, dtype=float))
    pixels = intrinsics(width, height, FOV_X_DEG)
    dx = (u - pixels["cx"]) / pixels["fx"]
    dy = (pixels["cy"] - v) / pixels["fy"]
    direction = dx * cam[:3, 0] + dy * cam[:3, 1] - cam[:3, 2]
    dir_z = float(direction[2])
    depth = -float(eye[2]) / dir_z if abs(dir_z) > 1e-8 else PLANE_DEPTH_M
    if depth <= 0.05:
        depth = PLANE_DEPTH_M
    point = np.asarray(eye, dtype=float) + depth * direction
    return (float(point[0]), float(point[1]), 0.0), float(depth)


def _thickness(width_m: float, height_m: float) -> float:
    return max(min(width_m, height_m) * THICKNESS, 0.02)


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
        kept_row = {
            "timestamp": min(span, max(0.0, timestamp)),
            "bbox": bbox,
            "label": str(item.get("label") or ref),
        }
        if isinstance(item.get("oriented"), dict):
            kept_row["oriented"] = item["oriented"]
        grouped.setdefault(ref, []).append(kept_row)
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

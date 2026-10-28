"""Crop the first view of each object so Blender can show that picture on the shape."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import cv2
import numpy as np

from app.simulation.from_video import instance_ids_for
from app.video.frames import extract_frame, read_bgr


def crop_normalized(image: np.ndarray, bbox: dict) -> np.ndarray:
    height, width = image.shape[:2]
    x0 = max(0, min(width - 1, int(round(float(bbox["x"]) * width))))
    y0 = max(0, min(height - 1, int(round(float(bbox["y"]) * height))))
    x1 = max(x0 + 1, min(width, int(round((float(bbox["x"]) + float(bbox["w"])) * width))))
    y1 = max(y0 + 1, min(height, int(round((float(bbox["y"]) + float(bbox["h"])) * height))))
    return image[y0:y1, x0:x1]


def subject_rgba(crop: np.ndarray) -> np.ndarray:
    """Keep the object in the crop. The surrounding photo is transparent."""
    bgr = crop[:, :, :3]
    alpha = _subject_alpha(bgr)
    bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = alpha
    return bgra


def _subject_alpha(bgr: np.ndarray) -> np.ndarray:
    height, width = bgr.shape[:2]
    opaque = np.full((height, width), 255, np.uint8)
    inset_x = max(1, min(int(round(width * 0.06)), width // 4))
    inset_y = max(1, min(int(round(height * 0.06)), height // 4))
    if height - 2 * inset_y < 4 or width - 2 * inset_x < 4:
        return opaque
    mask = np.full((height, width), cv2.GC_PR_BGD, np.uint8)
    mask[inset_y : height - inset_y, inset_x : width - inset_x] = cv2.GC_PR_FGD
    mask[0, :] = cv2.GC_BGD
    mask[-1, :] = cv2.GC_BGD
    mask[:, 0] = cv2.GC_BGD
    mask[:, -1] = cv2.GC_BGD
    background = np.zeros((1, 65), np.float64)
    foreground = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(bgr, mask, None, background, foreground, 5, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return opaque
    keep = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(keep, connectivity=8)
    if count <= 1:
        return opaque
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if int(stats[largest, cv2.CC_STAT_AREA]) < 20:
        return opaque
    keep = np.where(labels == largest, 255, 0).astype(np.uint8)
    # Drop the one-pixel fringe where the table color is still mixed into the edge.
    eroded = cv2.erode(keep, np.ones((3, 3), np.uint8), iterations=1)
    if int(eroded.sum()) >= 20 * 255:
        keep = eroded
    return keep


def rotated_subject_box(image: np.ndarray, bbox: dict) -> dict:
    """A rectangle turned to the subject. Sides are fractions of the frame.

    angle_deg is the width edge's rotation in the floor, matching the scene.
    The original box is the fallback when the subject cannot be separated.
    """
    height, width = image.shape[:2]
    x0 = max(0, min(width - 1, int(round(float(bbox["x"]) * width))))
    y0 = max(0, min(height - 1, int(round(float(bbox["y"]) * height))))
    alpha = _subject_alpha(crop_normalized(image, bbox))
    return box_from_alpha(alpha, x0, y0, width, height, bbox)


def box_from_alpha(
    alpha: np.ndarray,
    x0: int,
    y0: int,
    width: int,
    height: int,
    bbox: dict,
) -> dict:
    """Turn a subject mask into the rotated box. Coordinates are full-frame pixels."""
    fallback = {
        "cx": float(bbox["x"]) + float(bbox["w"]) / 2.0,
        "cy": float(bbox["y"]) + float(bbox["h"]) / 2.0,
        "w": float(bbox["w"]),
        "h": float(bbox["h"]),
        "angle_deg": 0.0,
    }
    ys, xs = np.nonzero(alpha >= 128)
    if len(xs) < 24:
        return fallback
    points = np.stack([xs.astype(np.float32) + x0, ys.astype(np.float32) + y0], axis=1)
    corners = cv2.boxPoints(cv2.minAreaRect(points))
    edges = [corners[(index + 1) % 4] - corners[index] for index in range(2)]
    lengths = [float(np.linalg.norm(edge)) for edge in edges]
    if min(lengths) < 1.0:
        return fallback
    # The long side stays the width, so a turn between frames does not swap the solid's axes.
    long_edge = edges[0] if lengths[0] >= lengths[1] else edges[1]
    width_px = max(lengths)
    height_px = min(lengths)
    direction = long_edge / width_px
    # Prefer the sign that puts the short side toward the top of the picture.
    flipped = -direction
    width_dir = direction if (float(direction[0]), -float(direction[1])) >= (float(flipped[0]), -float(flipped[1])) else flipped
    center = corners.mean(axis=0)
    yaw = math.degrees(math.atan2(-float(width_dir[1]), float(width_dir[0])))
    return {
        "cx": float(center[0]) / width,
        "cy": float(center[1]) / height,
        "w": max(width_px, 1.0) / width,
        "h": max(height_px, 1.0) / height,
        "angle_deg": yaw,
    }


def orient_tracks(source: Path, tracks: list[dict], duration: float | None = None) -> None:
    """Attach a rotated box to each observation the scene will use. The axis-aligned box stays."""
    limit = 12.0 if not duration or duration <= 0 else min(float(duration), 12.0)
    stamps = sorted(
        {
            round(float(row["timestamp"]), 3)
            for row in tracks
            if _stamp(row.get("timestamp")) and float(row["timestamp"]) <= limit + 1e-6
        }
    )
    frames: dict[float, np.ndarray] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for stamp in stamps:
            path = root / f"{stamp:.3f}.png"
            try:
                extract_frame(source, stamp, path, accurate=False)
                frames[stamp] = read_bgr(path)
            except (OSError, RuntimeError):
                continue
    for row in tracks:
        frame = frames.get(round(float(row.get("timestamp", -1)), 3))
        bbox = row.get("bbox")
        if frame is None or not isinstance(bbox, dict):
            continue
        try:
            row["oriented"] = rotated_subject_box(frame, bbox)
        except cv2.error:
            continue


def _stamp(value) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _warp_box(image: np.ndarray, oriented: dict) -> np.ndarray:
    """Pull the rotated rectangle out so its width runs left to right and its top is image-up."""
    height, width = image.shape[:2]
    angle = math.radians(float(oriented["angle_deg"]))
    du = math.cos(angle)
    dv = -math.sin(angle)
    px_w = max(2, int(round(float(oriented["w"]) * width)))
    px_h = max(2, int(round(float(oriented["h"]) * height)))
    center = np.array([float(oriented["cx"]) * width, float(oriented["cy"]) * height], dtype=float)
    half_w = np.array([du, dv], dtype=float) * (px_w / 2.0)
    half_h = np.array([dv, -du], dtype=float) * (px_h / 2.0)
    src = np.float32([
        center - half_w - half_h,
        center + half_w - half_h,
        center + half_w + half_h,
        center - half_w + half_h,
    ])
    dst = np.float32([[0, px_h - 1], [px_w - 1, px_h - 1], [px_w - 1, 0], [0, 0]])
    transform = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, transform, (px_w, px_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def write_initial_textures(source: Path, tracks: list[dict], dest: Path, duration: float) -> dict[str, Path]:
    """One PNG per object, taken from its earliest box. Keys match the scene instance ids."""
    names = instance_ids_for(tracks, duration)
    earliest: dict[str, dict] = {}
    for row in tracks:
        ref = str(row.get("track_ref") or "")
        if ref not in names:
            continue
        if ref not in earliest or float(row["timestamp"]) < float(earliest[ref]["timestamp"]):
            earliest[ref] = row
    dest.mkdir(parents=True, exist_ok=True)
    frames: dict[float, np.ndarray] = {}
    written: dict[str, Path] = {}
    for ref, row in earliest.items():
        stamp = round(float(row["timestamp"]), 3)
        if stamp not in frames:
            frame_path = dest / f"_frame_{stamp:.3f}.png"
            extract_frame(source, stamp, frame_path)
            frames[stamp] = read_bgr(frame_path)
            frame_path.unlink(missing_ok=True)
        oriented = row.get("oriented") if isinstance(row.get("oriented"), dict) else None
        crop = _warp_box(frames[stamp], oriented) if oriented else crop_normalized(frames[stamp], row["bbox"])
        if crop.size == 0:
            continue
        path = dest / f"{names[ref]}.png"
        if not cv2.imwrite(str(path), subject_rgba(crop)):
            continue
        written[names[ref]] = path
    return written

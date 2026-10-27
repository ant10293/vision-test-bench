"""Crop the first view of each object so Blender can show that picture on the shape."""

from __future__ import annotations

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
        crop = crop_normalized(frames[stamp], row["bbox"])
        if crop.size == 0:
            continue
        path = dest / f"{names[ref]}.png"
        if not cv2.imwrite(str(path), subject_rgba(crop)):
            continue
        written[names[ref]] = path
    return written

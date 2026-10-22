"""Pluggable spatial strategies.

These baselines estimate camera motion and track residual blobs. They never
emit world coordinates. Identity is not forced across weak appearance matches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.video.frames import read_bgr
from app.video.sampling import MotionSignal


@dataclass
class TrackPoint:
    track_ref: str
    timestamp: float
    x: float
    y: float
    w: float
    h: float
    motion_score: float
    continuity: str
    camera_compensated: bool
    scene_chunk: int
    note: str = ""


@dataclass
class SpatialReport:
    viewpoint: str
    task_constraint: str
    research_mode: str
    global_motion: float
    scene_change_count: int
    camera_motion_compensated: bool
    compensation_model: str
    coordinate_frame: str
    world_coordinates_supported: bool
    notes: str
    tracks: list[TrackPoint] = field(default_factory=list)
    chunks: list[dict] = field(default_factory=list)


def characterize(signals: list[MotionSignal], declared_mode: str) -> dict:
    globals_ = [item.global_motion for item in signals[1:]] or [0.0]
    median_global = float(np.median(globals_))
    scene_changes = sum(1 for item in signals if item.scene_change)
    if declared_mode == "fixed_camera":
        mode, view, constraint = "fixed_camera", "fixed", "constrained"
        source = "user"
    elif declared_mode == "constrained_head_mounted":
        mode, view, constraint = "constrained_head_mounted", "head_mounted", "constrained"
        source = "user"
    elif declared_mode == "unconstrained_head_mounted":
        mode, view, constraint = "unconstrained_head_mounted", "head_mounted", "unconstrained"
        source = "user"
    else:
        source = "auto"
        if scene_changes >= 2:
            mode, view, constraint = "unconstrained_head_mounted", "head_mounted", "unconstrained"
        elif median_global < 0.35:
            mode, view, constraint = "fixed_camera", "fixed", "unknown"
        else:
            mode, view, constraint = "constrained_head_mounted", "head_mounted", "constrained"
    return {
        "research_mode": mode,
        "viewpoint": view,
        "task_constraint": constraint,
        "global_motion": median_global,
        "scene_change_count": scene_changes,
        "source": source,
    }


def _blobs(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < 80:
            continue
        boxes.append((x, y, w, h))
    boxes.sort(key=lambda item: item[2] * item[3], reverse=True)
    return boxes[:8]


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _color_sig(image: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = box
    crop = image[y : y + h, x : x + w]
    if crop.size == 0:
        return np.zeros(24, dtype=np.float32)
    hist = cv2.calcHist([crop], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def _compensate(prev: np.ndarray, curr: np.ndarray) -> tuple[np.ndarray, bool]:
    prev_g = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    curr_g = cv2.cvtColor(curr, cv2.COLOR_BGR2GRAY)
    features = cv2.goodFeaturesToTrack(prev_g, maxCorners=80, qualityLevel=0.01, minDistance=7)
    if features is None or len(features) < 6:
        return curr, False
    nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev_g, curr_g, features, None)
    if nxt is None or status is None:
        return curr, False
    good_prev = features[status.flatten() == 1]
    good_next = nxt[status.flatten() == 1]
    if len(good_prev) < 6:
        return curr, False
    matrix, inliers = cv2.estimateAffinePartial2D(good_prev, good_next, method=cv2.RANSAC)
    if matrix is None:
        return curr, False
    if inliers is not None and int(inliers.sum()) < 6:
        return curr, False
    height, width = curr.shape[:2]
    warped = cv2.warpAffine(prev, matrix, (width, height))
    return warped, True


def analyze_sequence(
    paths: list[Path],
    timestamps: list[float],
    signals: list[MotionSignal],
    declared_mode: str,
) -> SpatialReport:
    profile = characterize(signals, declared_mode)
    compensate = profile["viewpoint"] == "head_mounted"
    images = [read_bgr(path) for path in paths]
    if not images:
        return SpatialReport(
            viewpoint=profile["viewpoint"],
            task_constraint=profile["task_constraint"],
            research_mode=profile["research_mode"],
            global_motion=profile["global_motion"],
            scene_change_count=profile["scene_change_count"],
            camera_motion_compensated=False,
            compensation_model="none",
            coordinate_frame="image",
            world_coordinates_supported=False,
            notes="No frames were available for spatial tracking.",
        )
    height, width = images[0].shape[:2]
    chunk = 0
    active: list[dict] = []
    points: list[TrackPoint] = []
    chunks = [{"ordinal": 0, "time_start": timestamps[0], "time_end": timestamps[0], "scene_change": False}]
    next_id = 1
    compensated_any = False
    subtractor = None if compensate else cv2.createBackgroundSubtractorMOG2(history=20, varThreshold=16, detectShadows=False)

    for index, image in enumerate(images):
        timestamp = timestamps[index]
        signal = signals[index] if index < len(signals) else None
        if signal and signal.scene_change and index > 0:
            chunk += 1
            active = []
            chunks.append(
                {
                    "ordinal": chunk,
                    "time_start": timestamp,
                    "time_end": timestamp,
                    "scene_change": True,
                    "global_motion": signal.global_motion,
                }
            )
        chunks[-1]["time_end"] = timestamp
        if index == 0:
            mask = np.zeros(image.shape[:2], dtype=np.uint8)
        elif compensate:
            reference, ok = _compensate(images[index - 1], image)
            compensated_any = compensated_any or ok
            diff = cv2.absdiff(image, reference if ok else images[index - 1])
            gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            _, mask = cv2.threshold(gray, 28, 255, cv2.THRESH_BINARY)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        else:
            assert subtractor is not None
            fg = subtractor.apply(image)
            _, mask = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

        detections = _blobs(mask)
        used = set()
        for box in detections:
            signature = _color_sig(image, box)
            best_i = -1
            best_score = 0.15
            for candidate, track in enumerate(active):
                if candidate in used or track["chunk"] != chunk:
                    continue
                score = _iou(track["box"], box)
                if score > best_score:
                    best_score = score
                    best_i = candidate
            nx, ny, nw, nh = box
            norm = (nx / width, ny / height, nw / width, nh / height)
            if best_i >= 0:
                used.add(best_i)
                track = active[best_i]
                track["box"] = box
                track["sig"] = signature
                continuity = "continuous"
                ref = track["ref"]
                note = ""
            else:
                ref = f"track_{next_id}"
                next_id += 1
                continuity = "new"
                note = "New image-space track. Semantic identity is not established."
                active.append({"ref": ref, "box": box, "sig": signature, "chunk": chunk})
            motion = signal.motion if signal else 0
            points.append(
                TrackPoint(
                    track_ref=ref,
                    timestamp=timestamp,
                    x=norm[0],
                    y=norm[1],
                    w=norm[2],
                    h=norm[3],
                    motion_score=motion,
                    continuity=continuity,
                    camera_compensated=compensate and compensated_any,
                    scene_chunk=chunk,
                    note=note,
                )
            )
        _reassociate_across_chunks(points, active, chunk, profile["task_constraint"])

    notes = (
        "Tracks are image-plane motion regions. "
        "World coordinates are not estimated because no calibration or visual-geometry solution ran. "
    )
    if compensate:
        notes += "Head-mounted baseline subtracts a partial affine camera motion before blob detection. "
    else:
        notes += "Fixed-camera baseline uses a background subtractor against a stable image frame. "
    if profile["source"] == "auto":
        notes += "Research mode was characterized automatically and can be overridden."
    return SpatialReport(
        viewpoint=profile["viewpoint"],
        task_constraint=profile["task_constraint"],
        research_mode=profile["research_mode"],
        global_motion=profile["global_motion"],
        scene_change_count=profile["scene_change_count"],
        camera_motion_compensated=bool(compensate and compensated_any),
        compensation_model="affine_partial" if compensate else "none",
        coordinate_frame="image",
        world_coordinates_supported=False,
        notes=notes.strip(),
        tracks=points,
        chunks=chunks,
    )


def _reassociate_across_chunks(
    points: list[TrackPoint],
    active: list[dict],
    chunk: int,
    constraint: str,
) -> None:
    if constraint != "unconstrained" or chunk == 0:
        return
    fresh = [track for track in active if track["chunk"] == chunk]
    prior = [track for track in active if track["chunk"] < chunk]
    for track in fresh:
        if any(point.track_ref == track["ref"] and point.continuity == "reassociated" for point in points):
            continue
        best = None
        best_corr = 0.0
        for candidate in prior:
            corr = float(
                cv2.compareHist(
                    track["sig"].astype(np.float32),
                    candidate["sig"].astype(np.float32),
                    cv2.HISTCMP_CORREL,
                )
            )
            if corr > best_corr:
                best_corr = corr
                best = candidate
        if best is None:
            continue
        for point in points:
            if point.track_ref != track["ref"] or point.scene_chunk != chunk or point.continuity != "new":
                continue
            if best_corr >= 0.92:
                point.continuity = "reassociated"
                point.note = (
                    f"Appearance similarity {best_corr:.2f} with earlier track {best['ref']}. "
                    "The intervening interval was not observed, so this is a cautious reassociation, "
                    "not proof of continuous identity."
                )
            else:
                point.continuity = "uncertain"
                point.note = (
                    "A visually similar region existed in an earlier scene, but the evidence is too "
                    "weak to treat it as the same object. Identity was not carried across the gap."
                )
            break

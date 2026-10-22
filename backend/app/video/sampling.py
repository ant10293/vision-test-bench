"""Deterministic frame sampling, including motion-aware adaptive selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.schemas.enums import PRESET_FPS, SamplingPreset
from app.video.frames import read_bgr, sharpness_score
from app.video.probe import snap_timestamps


@dataclass
class SampledFrame:
    timestamp: float
    reason: str
    motion_score: float = 0
    sharpness: float = 0
    scene_change: bool = False


@dataclass
class MotionSignal:
    timestamp: float
    motion: float
    scene_change: bool
    global_motion: float


@dataclass
class AdaptiveResult:
    frames: list[SampledFrame]
    signals: list[MotionSignal] = field(default_factory=list)
    scene_change_times: list[float] = field(default_factory=list)


def uniform_targets(duration: float, fps: float) -> list[float]:
    if duration <= 0:
        return [0.0]
    if fps <= 0:
        return [0.0]
    step = 1.0 / fps
    times: list[float] = []
    cursor = 0.0
    while cursor < duration - 1e-3:
        times.append(round(cursor, 3))
        cursor += step
    if not times:
        times.append(0.0)
    return times


def preset_fps(preset: str, custom_fps: float | None) -> float:
    if preset == SamplingPreset.CUSTOM.value:
        if not custom_fps or custom_fps <= 0:
            raise ValueError("custom sampling requires a positive fps")
        return float(custom_fps)
    if preset == SamplingPreset.ADAPTIVE.value:
        return 1.0
    return PRESET_FPS.get(preset, 1.0)


def plan_uniform(duration: float, fps: float, pts: list[float]) -> list[SampledFrame]:
    targets = uniform_targets(duration, fps)
    snapped = snap_timestamps(targets, pts)
    return [SampledFrame(timestamp=item, reason="uniform") for item in snapped]


def _hist_corr(a: np.ndarray, b: np.ndarray) -> float:
    ha = cv2.calcHist([a], [0], None, [32], [0, 256])
    hb = cv2.calcHist([b], [0], None, [32], [0, 256])
    cv2.normalize(ha, ha)
    cv2.normalize(hb, hb)
    return float(cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL))


def measure_motion(paths: list[Path], timestamps: list[float]) -> list[MotionSignal]:
    signals: list[MotionSignal] = []
    prev = None
    for path, timestamp in zip(paths, timestamps, strict=True):
        gray = cv2.cvtColor(read_bgr(path), cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 90))
        if prev is None:
            signals.append(MotionSignal(timestamp, 0.0, False, 0.0))
            prev = small
            continue
        diff = float(np.mean(cv2.absdiff(prev, small)))
        corr = _hist_corr(prev, small)
        scene = corr < 0.72
        flow = cv2.calcOpticalFlowFarneback(prev, small, None, 0.5, 2, 15, 2, 5, 1.2, 0)
        mag = np.linalg.norm(flow, axis=2)
        global_motion = float(np.median(mag))
        signals.append(
            MotionSignal(
                timestamp=timestamp,
                motion=diff,
                scene_change=scene,
                global_motion=global_motion,
            )
        )
        prev = small
    return signals


def plan_adaptive(
    duration: float,
    pts: list[float],
    coarse: list[SampledFrame],
    signals: list[MotionSignal],
    uncertain_windows: list[tuple[float, float]] | None = None,
    detail_fps: float = 4.0,
) -> AdaptiveResult:
    """Add samples around motion peaks, scene changes, and uncertain windows.

    The base grid remains in the result so coverage is explicit. Extra frames are
    tagged with the reason they were added.
    """
    by_time = {round(frame.timestamp, 3): frame for frame in coarse}
    signal_by_time = {round(item.timestamp, 3): item for item in signals}
    motions = [item.motion for item in signals[1:]] or [0.0]
    median = float(np.median(motions))
    mad = float(np.median([abs(item - median) for item in motions])) or 1.0
    threshold = median + 1.5 * mad
    scene_times = [item.timestamp for item in signals if item.scene_change]

    def add(timestamp: float, reason: str, motion: float = 0, scene: bool = False) -> None:
        snapped = snap_timestamps([timestamp], pts)[0]
        key = round(snapped, 3)
        existing = by_time.get(key)
        if existing is None:
            by_time[key] = SampledFrame(snapped, reason, motion, 0, scene)
            return
        if existing.reason == "uniform" and reason != "uniform":
            existing.reason = reason
        existing.motion_score = max(existing.motion_score, motion)
        existing.scene_change = existing.scene_change or scene

    for signal in signals:
        if signal.scene_change or signal.motion >= threshold:
            reason = "scene_change" if signal.scene_change else "motion_peak"
            half = 0.35
            extra = uniform_targets(half * 2, detail_fps)
            for offset in extra:
                add(max(0.0, signal.timestamp - half + offset), reason, signal.motion, signal.scene_change)
            add(signal.timestamp, reason, signal.motion, signal.scene_change)

    for start, end in uncertain_windows or []:
        cursor = start
        step = 1.0 / detail_fps
        while cursor <= end:
            add(cursor, "uncertainty_window")
            cursor += step

    for frame in by_time.values():
        signal = signal_by_time.get(round(frame.timestamp, 3))
        if signal:
            frame.motion_score = max(frame.motion_score, signal.motion)
            frame.scene_change = frame.scene_change or signal.scene_change

    ordered = sorted(by_time.values(), key=lambda item: item.timestamp)
    ordered = [item for item in ordered if 0 <= item.timestamp <= duration + 0.05]
    return AdaptiveResult(frames=ordered, signals=signals, scene_change_times=scene_times)


def annotate_sharpness(frames: list[SampledFrame], paths: dict[float, Path]) -> None:
    for frame in frames:
        path = paths.get(round(frame.timestamp, 3))
        if path and path.exists():
            frame.sharpness = sharpness_score(read_bgr(path))

"""Image-plane geometry for tracked instances. None of it is a world coordinate.

Apparent scale is box area against the first full views of the same id. It
changes when the part comes toward the lens, and also when the head moves, so
it is shown and never judged.

Turn is the in-plane rotation of the features inside the box since the first
full view of that id. When the camera moves, its own roll between the same two
frames is removed. A frame where that roll cannot be estimated gets no angle;
more than CAMERA_BRIDGE such frames in a row end the turn trace for every id
seen before them. The roll is an estimate: a textured moving hand that fills
most of the frame can be read as camera roll and make a still part look
turned. A held turn on a moving camera is therefore inferred, not directly
observed. A turn about the part's own axis, or a flip onto another face, is
not measured.

Depth order compares relative monocular depth inside two overlapping boxes on
a fixed camera. It says which one is nearer the camera. It is not a distance,
contact, or attachment.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.perception.assignment import iou
from app.perception.camera import camera_motion
from app.perception.types import (
    DepthOrder,
    DepthUnavailable,
    GeometryReport,
    TrackPoint,
    TurnEvent,
)
from app.schemas.analysis import Evidence, RelationshipPrediction, TransitionPrediction
from app.schemas.enums import (
    IMAGE_PLANE_INITIAL,
    IMAGE_PLANE_STATES,
    IMAGE_PLANE_TURNED,
    EvidenceClass,
    Provenance,
    RelationKind,
)
from app.video.frames import VideoError, read_bgr

INITIAL = IMAGE_PLANE_INITIAL
TURNED = IMAGE_PLANE_TURNED

# A box this close to the frame edge may be cut off, so its area and features are not used.
BORDER = 0.02
BASELINE_VIEWS = 3
MIN_INLIERS = 6
# A box-to-box affine that changes scale beyond this is a wrong match, not a turn.
MAX_SCALE_CHANGE = 1.5
PATCH_PAD = 0.15
PATCH_EDGE = 256
ANCHOR_AGREEMENT = 10.0
# Camera roll is summed frame to frame, so one estimate dominated by a moving hand
# would stay in every later angle. A frame that fails these ends the trace instead.
CAMERA_CORNERS = 300
CAMERA_REPROJECTION = 2.0
CAMERA_INLIERS = 15
CAMERA_SHARE = 0.25
# Frames in a row whose camera roll may be unknown before the trace ends.
CAMERA_BRIDGE = 4
TURN_DEG = 20.0
HOLD_DEG = 8.0
HOLD_SAMPLES = 3
FRONT_IOU = 0.05
# Share of the frame's 2nd-98th percentile depth range one box must clear.
FRONT_MARGIN = 0.15
# Seconds between two orders of the same pair that still count as one interval.
ORDER_GAP = 1.0


@dataclass
class _TrackState:
    chunk: int
    chain: int
    anchor: np.ndarray | None = None
    anchor_mask: np.ndarray | None = None
    patch: np.ndarray | None = None
    mask: np.ndarray | None = None
    camera_angle: float = 0.0
    # Image rotation of the box contents since the first view, camera rotation included.
    image_turn: float = 0.0
    broken: bool = False
    areas: list[float] = field(default_factory=list)
    pending: list[TrackPoint] = field(default_factory=list)
    baseline: float | None = None


def annotate_geometry(
    points: list[TrackPoint],
    paths: list[Path],
    timestamps: list[float],
    viewpoint: str,
    depth=None,
    depth_status: str = "not_requested",
    depth_detail: str = "",
    should_stop=None,
    depth_visibility: tuple[str, ...] = ("visible",),
) -> GeometryReport:
    """Fill relative_scale and angle_deg on each point and collect turns and depth order.

    depth_visibility lists the visibility values a box may have to enter depth order.
    """
    report = GeometryReport(
        depth_id=getattr(depth, "depth_id", None),
        depth_status="ready" if depth is not None else depth_status,
        depth_detail=depth_detail,
    )
    by_time: dict[float, list[TrackPoint]] = defaultdict(list)
    for point in points:
        by_time[round(point.timestamp, 3)].append(point)
    moving = viewpoint == "head_mounted"
    states: dict[str, _TrackState] = {}
    # The last frame whose camera roll is known. A failed frame is compared again
    # from here on the next frame, so one blurred frame does not end every trace.
    reference_gray = None
    missed = 0
    camera_angle = 0.0
    chain = 0
    active_depth = depth if viewpoint == "fixed" else None
    for index, path in enumerate(paths):
        if should_stop is not None and index % 16 == 0 and should_stop():
            break
        here = by_time.get(round(timestamps[index], 3), [])
        if not here and not moving:
            continue
        try:
            image = read_bgr(path)
        except VideoError:
            missed += 1 if moving else 0
            continue
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        camera_known = True
        if moving and reference_gray is not None:
            matrix = camera_motion(
                reference_gray,
                gray,
                max_corners=CAMERA_CORNERS,
                reprojection=CAMERA_REPROJECTION,
                min_inliers=CAMERA_INLIERS,
                min_share=CAMERA_SHARE,
            )
            if matrix is not None:
                camera_angle += _rotation(matrix)
                missed = 0
            else:
                missed += 1
                camera_known = False
        if missed > CAMERA_BRIDGE:
            chain += 1
            camera_angle = 0.0
            missed = 0
            camera_known = True
        if camera_known:
            reference_gray = gray
        for point in sorted(here, key=lambda item: item.track_ref):
            state = states.get(point.track_ref)
            if state is None:
                state = _TrackState(chunk=point.scene_chunk, chain=chain)
                states[point.track_ref] = state
            if not _full_view(point):
                continue
            _scale(state, point, report)
            if camera_known:
                _angle(state, point, gray, chain, camera_angle, report)
        if active_depth is not None:
            try:
                orders = _depth_orders(image, here, active_depth, depth_visibility)
            except DepthUnavailable as exc:
                report.depth_status = "unavailable"
                report.depth_detail = str(exc)
                active_depth = None
                orders = None
            if orders is not None:
                report.depth_frames += 1
                report.orders.extend(orders)
    report.turns = held_turns(points)
    for turn in report.turns:
        turn.camera_removed = moving
    for point in points:
        if point.angle_deg is not None and abs(point.angle_deg) >= TURN_DEG:
            sentence = (
                f"This instance has turned {point.angle_deg:+.0f} degrees in the image since its first full view, "
                "so its appearance can differ from that view."
            )
            point.note = f"{point.note} {sentence}" if point.note else sentence
    return report


def held_turns(points: list[TrackPoint]) -> list[TurnEvent]:
    """A turn holds when three samples in a row sit past the threshold within a narrow band."""
    series: dict[str, list[TrackPoint]] = defaultdict(list)
    for point in points:
        if point.angle_deg is not None:
            series[point.track_ref].append(point)
    events: list[TurnEvent] = []
    for ref, rows in series.items():
        rows.sort(key=lambda item: item.timestamp)
        current = INITIAL
        index = 0
        while index + HOLD_SAMPLES <= len(rows):
            window = rows[index : index + HOLD_SAMPLES]
            angles = [item.angle_deg for item in window]
            steady = max(angles) - min(angles) <= HOLD_DEG
            past = all(abs(angle) >= TURN_DEG for angle in angles)
            back = all(abs(angle) < TURN_DEG for angle in angles)
            if steady and ((current == INITIAL and past) or (current == TURNED and back)):
                target = TURNED if current == INITIAL else INITIAL
                events.append(
                    TurnEvent(
                        track_ref=ref,
                        timestamp=window[0].timestamp,
                        from_state=current,
                        to_state=target,
                        angle_deg=float(np.median(angles)),
                    )
                )
                current = target
                index += HOLD_SAMPLES
                continue
            index += 1
    events.sort(key=lambda item: (item.timestamp, item.track_ref))
    return events


def geometry_records(
    report: GeometryReport | None, track_to_object: dict[str, str]
) -> tuple[list[TransitionPrediction], list[RelationshipPrediction]]:
    """Held turns as transitions and depth order as in_front_of intervals, keyed by object id."""
    if report is None:
        return [], []
    transitions = []
    for turn in report.turns:
        local_id = track_to_object.get(turn.track_ref)
        if not local_id:
            continue
        if turn.to_state == TURNED:
            text = (
                f"The features inside this box turned {turn.angle_deg:+.0f} degrees in the image from its first "
                f"full view and held within {HOLD_DEG:.0f} degrees for {HOLD_SAMPLES} samples. "
            )
        else:
            text = (
                f"The features inside this box came back within {TURN_DEG:.0f} degrees of its first full view "
                f"and held there for {HOLD_SAMPLES} samples. "
            )
        if turn.camera_removed:
            text += (
                "An estimated camera roll was removed, and a large moving hand can bias that estimate, "
                "so this turn is inferred. "
            )
        text += "This is an image-plane turn, not a 3D pose."
        transitions.append(
            TransitionPrediction(
                object_local_id=local_id,
                timestamp=turn.timestamp,
                from_state=turn.from_state,
                to_state=turn.to_state,
                description=text,
                evidence=Evidence(
                    classification=EvidenceClass.INFERRED if turn.camera_removed else EvidenceClass.DIRECTLY_OBSERVED,
                    provenance=Provenance.DETERMINISTIC,
                    note=text,
                    time_start=turn.timestamp,
                    time_end=turn.timestamp,
                    frame_timestamps=[turn.timestamp],
                ),
            )
        )
    relationships = []
    for nearer, farther, start, end, frames in _order_intervals(report.orders):
        subject = track_to_object.get(nearer)
        other = track_to_object.get(farther)
        if not subject or not other or subject == other:
            continue
        text = (
            "Inside two overlapping boxes, relative monocular depth puts this one nearer the camera. "
            "It is not a distance, contact, or attachment."
        )
        relationships.append(
            RelationshipPrediction(
                subject_local_id=subject,
                other_local_id=other,
                kind=RelationKind.IN_FRONT_OF,
                time_start=start,
                time_end=end,
                description=text,
                evidence=Evidence(
                    classification=EvidenceClass.DIRECTLY_OBSERVED,
                    provenance=Provenance.CV_DETECTION,
                    note=f"{report.depth_id or 'depth model'} on a fixed camera. {text}",
                    time_start=start,
                    time_end=end,
                    frame_timestamps=frames[:12],
                ),
            )
        )
    return transitions, relationships


def is_geometry_record(item) -> bool:
    if isinstance(item, TransitionPrediction):
        return item.to_state in IMAGE_PLANE_STATES and _provenance(item) == Provenance.DETERMINISTIC.value
    if isinstance(item, RelationshipPrediction):
        kind = item.kind.value if hasattr(item.kind, "value") else str(item.kind)
        return kind == RelationKind.IN_FRONT_OF.value and _provenance(item) == Provenance.CV_DETECTION.value
    return False


def _provenance(item) -> str:
    value = item.evidence.provenance
    return value.value if hasattr(value, "value") else str(value)


def _order_intervals(orders: list[DepthOrder]):
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for order in orders:
        grouped[(order.nearer, order.farther)].append(order.timestamp)
    for (nearer, farther), times in sorted(grouped.items()):
        times.sort()
        start = previous = times[0]
        frames = [start]
        for time in times[1:]:
            if time - previous > ORDER_GAP:
                yield nearer, farther, start, previous, frames
                start, frames = time, []
            frames.append(time)
            previous = time
        yield nearer, farther, start, previous, frames


def _full_view(point: TrackPoint) -> bool:
    if (point.visibility or "visible") != "visible":
        return False
    return (
        point.x >= BORDER
        and point.y >= BORDER
        and point.x + point.w <= 1 - BORDER
        and point.y + point.h <= 1 - BORDER
    )


def _scale(state: _TrackState, point: TrackPoint, report: GeometryReport) -> None:
    area = point.w * point.h
    if area <= 0:
        return
    if state.baseline is None:
        state.areas.append(area)
        state.pending.append(point)
        if len(state.areas) < BASELINE_VIEWS:
            return
        state.baseline = float(np.median(state.areas))
        for earlier in state.pending:
            earlier.relative_scale = earlier.w * earlier.h / state.baseline
            report.scale_samples += 1
        state.pending = []
        return
    point.relative_scale = area / state.baseline
    report.scale_samples += 1


def _angle(state: _TrackState, point: TrackPoint, gray: np.ndarray, chain: int, camera_angle: float, report: GeometryReport) -> None:
    if state.broken:
        return
    if point.scene_chunk != state.chunk or chain != state.chain:
        state.broken = True
        return
    patch, mask = _patch(gray, point)
    if state.anchor is None:
        state.anchor, state.anchor_mask = patch, mask
        state.patch, state.mask = patch, mask
        state.camera_angle = camera_angle
        state.image_turn = 0.0
        point.angle_deg = 0.0
        report.angle_samples += 1
        return
    step = _refined_rotation(state.patch, state.mask, patch, 0.0)
    if step is None:
        return
    chained = state.image_turn + step
    # Chaining adds a small error at every sample. The first view corrects it, but a
    # first-view match that disagrees with the chain lost the features and is dropped.
    anchored = _refined_rotation(state.anchor, state.anchor_mask, patch, chained)
    if anchored is not None and abs(anchored - chained) <= ANCHOR_AGREEMENT:
        state.image_turn = anchored
    else:
        state.image_turn = chained
    state.patch, state.mask = patch, mask
    point.angle_deg = state.image_turn - (camera_angle - state.camera_angle)
    report.angle_samples += 1


def _patch(gray: np.ndarray, point: TrackPoint) -> tuple[np.ndarray, np.ndarray]:
    """The box with a margin, centered on the box, at a size shared by later patches."""
    height, width = gray.shape[:2]
    box_w = max(4.0, point.w * width)
    box_h = max(4.0, point.h * height)
    side = max(box_w, box_h) * (1 + 2 * PATCH_PAD)
    scale = min(1.0, PATCH_EDGE / side)
    size = max(16, int(round(side * scale)))
    center = ((point.x + point.w / 2) * width, (point.y + point.h / 2) * height)
    patch = cv2.getRectSubPix(gray, (int(round(side)), int(round(side))), center)
    if scale < 1.0:
        patch = cv2.resize(patch, (size, size), interpolation=cv2.INTER_AREA)
    else:
        size = patch.shape[0]
    mask = np.zeros((size, size), dtype=np.uint8)
    half_w = box_w * scale / 2
    half_h = box_h * scale / 2
    mid = size / 2
    cv2.rectangle(mask, (int(mid - half_w), int(mid - half_h)), (int(mid + half_w), int(mid + half_h)), 255, -1)
    return patch, mask


def _refined_rotation(previous: np.ndarray, mask: np.ndarray, current: np.ndarray, seed: float) -> float | None:
    """Rotation from previous to current, measured after undoing the seed and refined once."""
    estimate = seed
    for attempt in range(2):
        center = (current.shape[1] / 2, current.shape[0] / 2)
        undo = cv2.getRotationMatrix2D(center, estimate, 1.0)
        view = cv2.warpAffine(current, undo, (current.shape[1], current.shape[0]), borderMode=cv2.BORDER_REPLICATE)
        residual = _patch_rotation(previous, mask, view)
        if residual is None:
            return estimate if attempt else None
        estimate += residual
    return estimate


def _patch_rotation(previous: np.ndarray, mask: np.ndarray, current: np.ndarray) -> float | None:
    if previous.shape != current.shape:
        current = cv2.resize(current, (previous.shape[1], previous.shape[0]), interpolation=cv2.INTER_AREA)
    features = cv2.goodFeaturesToTrack(previous, maxCorners=40, qualityLevel=0.01, minDistance=3, mask=mask)
    if features is None or len(features) < MIN_INLIERS:
        return None
    nxt, status, _err = cv2.calcOpticalFlowPyrLK(previous, current, features, None, winSize=(21, 21), maxLevel=3)
    if nxt is None or status is None:
        return None
    keep = status.flatten() == 1
    if int(keep.sum()) < MIN_INLIERS:
        return None
    matrix, inliers = cv2.estimateAffinePartial2D(features[keep], nxt[keep], method=cv2.RANSAC)
    if matrix is None or inliers is None or int(inliers.sum()) < MIN_INLIERS:
        return None
    scale = math.hypot(matrix[0, 0], matrix[1, 0])
    if not (1 / MAX_SCALE_CHANGE <= scale <= MAX_SCALE_CHANGE):
        return None
    return _rotation(matrix)


def _rotation(matrix: np.ndarray) -> float:
    """Degrees, positive clockwise as displayed, since image y points down."""
    return math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))


def _depth_orders(
    image: np.ndarray, here: list[TrackPoint], depth, depth_visibility: tuple[str, ...] = ("visible",)
) -> list[DepthOrder] | None:
    """Orders for overlapping pairs, or None when no pair overlapped and no depth map was made."""
    visible = [point for point in here if (point.visibility or "visible") in depth_visibility]
    pairs = []
    for index, left in enumerate(visible):
        for right in visible[index + 1 :]:
            if left.track_ref == right.track_ref:
                continue
            if iou(_box(left), _box(right)) >= FRONT_IOU:
                pairs.append((left, right))
    if not pairs:
        return None
    raw = np.asarray(depth.predict(image), dtype=np.float32)
    height, width = image.shape[:2]
    if raw.shape[:2] != (height, width):
        raw = cv2.resize(raw, (width, height), interpolation=cv2.INTER_LINEAR)
    low, high = np.percentile(raw, [2, 98])
    if not np.isfinite(low) or not np.isfinite(high) or high - low < 1e-6:
        return []
    normalized = (raw - low) / (high - low)
    orders = []
    for left, right in pairs:
        left_value = _exclusive_median(normalized, left, right)
        right_value = _exclusive_median(normalized, right, left)
        if left_value is None or right_value is None:
            continue
        margin = left_value - right_value
        if abs(margin) < FRONT_MARGIN:
            continue
        nearer, farther = (left, right) if margin > 0 else (right, left)
        orders.append(
            DepthOrder(
                timestamp=left.timestamp,
                nearer=nearer.track_ref,
                farther=farther.track_ref,
                margin=float(abs(margin)),
            )
        )
    return orders


def _exclusive_median(values: np.ndarray, point: TrackPoint, other: TrackPoint) -> float | None:
    """Median depth inside one box, leaving out the part the other box also covers."""
    height, width = values.shape[:2]
    x0, y0, x1, y1 = _pixels(point, width, height)
    if x1 <= x0 or y1 <= y0:
        return None
    keep = np.ones((y1 - y0, x1 - x0), dtype=bool)
    ox0, oy0, ox1, oy1 = _pixels(other, width, height)
    ix0, iy0 = max(x0, ox0), max(y0, oy0)
    ix1, iy1 = min(x1, ox1), min(y1, oy1)
    if ix1 > ix0 and iy1 > iy0:
        keep[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0] = False
    region = values[y0:y1, x0:x1][keep]
    if region.size < 16:
        return None
    return float(np.median(region))


def _pixels(point: TrackPoint, width: int, height: int) -> tuple[int, int, int, int]:
    x0 = max(0, int(point.x * width))
    y0 = max(0, int(point.y * height))
    x1 = min(width, int((point.x + point.w) * width))
    y1 = min(height, int((point.y + point.h) * height))
    return x0, y0, x1, y1


def _box(point: TrackPoint) -> tuple[float, float, float, float]:
    return (point.x, point.y, point.w, point.h)

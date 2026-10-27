"""Choose the frames where a known part has to be looked for again.

Scene analysis boxes every part it can name in one pass. A part that rotates
and is partly covered often gets no box, or gets the neighbor's box, which
extent matching then removes. The follow-up look is a different question:
here is this part, and here are the frames where its box is missing. Answer
only for that part. The returned box still has to fit the part's size.
"""

from __future__ import annotations

import numpy as np

from app.perception.continuity import GROWTH, SHRINK
from app.schemas.analysis import AnalysisDocument, BBox, Evidence, ObservationPrediction
from app.schemas.enums import Continuity, EvidenceClass, Provenance, Visibility

# A follow-up box may be rotated. It may not be a different surface, such as skin.
SATURATION_GAP = 40
CHROMA_GAP = 30

TAIL_FRAMES = 3


def choose_locate_times(kept: list[float], all_times: list[float], limit: int = 4) -> list[float]:
    """Holes between sightings, then the first frames after the part vanishes.

    A hole is the usual place a rotated, partly covered part is still visible.
    The first frames of a trailing gap are where an insertion is still on screen.
    Frames long after the part has gone stay out of the request.
    """
    if not kept or limit <= 0:
        return []
    kept_set = {round(timestamp, 3) for timestamp in kept}
    first, last = min(kept_set), max(kept_set)
    holes = [timestamp for timestamp in all_times if first < timestamp < last and round(timestamp, 3) not in kept_set]
    holes.sort(key=lambda timestamp: min(abs(timestamp - item) for item in kept_set))
    tail = [timestamp for timestamp in all_times if timestamp > last]
    chosen = holes[:limit]
    chosen.extend(tail[: max(0, min(TAIL_FRAMES, limit - len(chosen)))])
    unique: list[float] = []
    for timestamp in chosen:
        if timestamp not in unique:
            unique.append(timestamp)
    return unique


def reference_time(observations: list[ObservationPrediction], object_id: str) -> ObservationPrediction | None:
    """The in-size view used as the picture of the part."""
    rows = [item for item in observations if item.object_local_id == object_id and item.bbox is not None]
    if len(rows) < 3:
        return None
    areas = sorted(item.bbox.w * item.bbox.h for item in rows if item.bbox is not None)
    reference = areas[len(areas) // 2]
    ranked = []
    for item in rows:
        assert item.bbox is not None
        area = item.bbox.w * item.bbox.h
        ratio = max(area, reference) / min(area, reference)
        if ratio > GROWTH:
            continue
        visible = 0 if item.visibility == Visibility.VISIBLE else 1
        ranked.append((visible, ratio, item.timestamp, item))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][3]


def same_surface(reference: np.ndarray, candidate: np.ndarray) -> bool:
    """The center of the follow-up box has to be the same material as the reference.

    Area alone accepts a skin-colored box the size of a small part. Saturation and
    the spread between color channels stay put when the part rotates, and they
    change when the box has landed on a different surface.
    """
    left = _surface(reference)
    right = _surface(candidate)
    if left is None or right is None:
        return False
    return abs(left[0] - right[0]) <= SATURATION_GAP and abs(left[1] - right[1]) <= CHROMA_GAP


def _surface(image: np.ndarray) -> tuple[float, float] | None:
    if image is None or getattr(image, "size", 0) == 0 or getattr(image, "ndim", 0) != 3 or image.shape[2] < 3:
        return None
    height, width = image.shape[:2]
    if height < 2 or width < 2:
        return None
    y0, y1 = height // 4, max(height // 4 + 1, (3 * height) // 4)
    x0, x1 = width // 4, max(width // 4 + 1, (3 * width) // 4)
    pixels = image[y0:y1, x0:x1].reshape(-1, image.shape[2])[:, :3].astype(np.float32)
    median = np.median(pixels, axis=0)
    blue, green, red = (float(median[0]), float(median[1]), float(median[2]))
    peak = max(red, green, blue)
    floor = min(red, green, blue)
    saturation = 0.0 if peak <= 0 else (peak - floor) / peak * 255.0
    return saturation, peak - floor


def box_fits(area: float, reference: float) -> bool:
    """A located box may be the visible portion. It may not be a different, larger part."""
    if area <= 0 or reference <= 0:
        return False
    if area > reference * GROWTH:
        return False
    if area * SHRINK < reference:
        return False
    return True


def apply_located(
    document: AnalysisDocument,
    object_id: str,
    found: list[tuple],
) -> AnalysisDocument:
    """Add boxes the follow-up look returned, when they fit this part."""
    reference = reference_time(document.observations, object_id)
    if reference is None or reference.bbox is None:
        return document
    reference_area = reference.bbox.w * reference.bbox.h
    occupied = {
        (round(item.timestamp, 3), item.object_local_id)
        for item in document.observations
        if item.bbox is not None
    }
    others: dict[float, list[BBox]] = {}
    for item in document.observations:
        if item.object_local_id == object_id or item.bbox is None:
            continue
        others.setdefault(round(item.timestamp, 3), []).append(item.bbox)
    added: list[ObservationPrediction] = []
    for timestamp, bbox, visibility, confidence in (_with_confidence(item) for item in found):
        key = round(timestamp, 3)
        if bbox is None or visibility == "absent":
            continue
        if (key, object_id) in occupied:
            continue
        area = bbox.w * bbox.h
        if not box_fits(area, reference_area):
            continue
        if any(_iou(bbox, other) >= 0.55 for other in others.get(key, [])):
            continue
        added.append(
            ObservationPrediction(
                object_local_id=object_id,
                timestamp=timestamp,
                bbox=bbox,
                visibility=Visibility.PARTIAL if visibility == "partial" else Visibility.VISIBLE,
                description=(
                    "Visible portion of this part. A changed angle or a partial cover still counts, "
                    "and the box is only the portion that matches the part's own size."
                ),
                evidence=Evidence(
                    classification=EvidenceClass.DIRECTLY_OBSERVED,
                    provenance=Provenance.AI_PREDICTION,
                    note="Located from a reference crop of this part, on the frame where the scene pass had no fitting box.",
                    frame_timestamps=[timestamp],
                ),
                continuity=Continuity.REASSOCIATED,
                confidence=confidence,
            )
        )
    if not added:
        return document
    observations = [*document.observations, *added]
    observations.sort(key=lambda item: (item.timestamp, item.object_local_id))
    return document.model_copy(update={"observations": observations})


def _with_confidence(item: tuple) -> tuple[float, BBox | None, str, float | None]:
    """Older callers pass timestamp, box, and visibility. A score is optional and does not decide acceptance."""
    timestamp, bbox, visibility, *rest = item
    return timestamp, bbox, visibility, rest[0] if rest else None


def _iou(a: BBox, b: BBox) -> float:
    from app.perception.assignment import iou

    return iou((a.x, a.y, a.w, a.h), (b.x, b.y, b.w, b.h))

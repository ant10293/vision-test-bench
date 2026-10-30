"""Replace video-file boxes that were measured on a square canvas.

A video model can return width and height as fractions of the same side.
On a 2160×3840 frame a circle then becomes a tall rectangle and sits off the
part. The extracted frames are the picture the player shows, so a follow-up
call on those frames is what puts the boxes back on the parts. A rescale of
the original numbers does not.
"""

from __future__ import annotations

from app.schemas.analysis import AnalysisDocument, ObservationPrediction

# A phone frame is about 1.78. Anything under this can honestly have square boxes.
_MIN_ASPECT = 1.35
_MIN_BOXES = 6
_NEAR_ONE = 0.5


def square_normalized(observations: list[ObservationPrediction], width: int, height: int) -> bool:
    """True when most boxes are square in normalized units on a non-square frame."""
    if width <= 0 or height <= 0:
        return False
    aspect = max(width, height) / min(width, height)
    if aspect < _MIN_ASPECT:
        return False
    ratios = [item.bbox.w / item.bbox.h for item in observations if item.bbox is not None and item.bbox.h > 0]
    if len(ratios) < _MIN_BOXES:
        return False
    ratios.sort()
    median = ratios[len(ratios) // 2]
    near = sum(1 for ratio in ratios if 0.9 <= ratio <= 1.1) / len(ratios)
    return 0.85 <= median <= 1.15 and near >= _NEAR_ONE


def frame_boxes_usable(observations: list[ObservationPrediction], width: int, height: int) -> bool:
    """A follow-up answer is usable when its boxes are not still on a square scale."""
    ratios = [item.bbox.w / item.bbox.h for item in observations if item.bbox is not None and item.bbox.h > 0]
    if not ratios or square_normalized(observations, width, height):
        return False
    if width <= 0 or height <= 0:
        return True
    if max(width, height) / min(width, height) < _MIN_ASPECT:
        return True
    ratios.sort()
    median = ratios[len(ratios) // 2]
    return not 0.85 <= median <= 1.15


def frame_box_prompt(width: int, height: int) -> str:
    ratio = height / width if width else 1
    return (
        "You are placing boxes on still frames from a recording. "
        f"Each image is {width} pixels wide and {height} pixels tall. "
        "x and w are fractions of that width. y and h are fractions of that height. "
        "The origin is the top-left pixel of the image, and the bottom-right pixel is x=1, y=1. "
        "Do not inset the box, and do not leave a margin for a border that is not in the image. "
        f"A circle on this image has w/h = {ratio:.3f}. "
        "Do not give w and h the same fraction unless the part has that shape in the image. "
        "The box touches the outermost visible pixels of that one part. "
        "Keep the given object ids. On every image, include one observation for each object "
        "that is even partly visible, and omit an object that is fully hidden. "
        "Return JSON with an observations list. Each box is an object named bbox with x, y, w, and h. "
        "Do not rename the objects."
    )


def merge_frame_boxes(document: AnalysisDocument, fresh: AnalysisDocument) -> AnalysisDocument:
    """Copy boxes from a frame answer onto the matching object and timestamp."""
    incoming: dict[tuple[str, float], ObservationPrediction] = {}
    for item in fresh.observations:
        if item.bbox is None:
            continue
        incoming[(item.object_local_id, round(item.timestamp, 3))] = item
    if not incoming:
        return document
    updated = []
    for item in document.observations:
        match = incoming.get((item.object_local_id, round(item.timestamp, 3)))
        if match is None:
            updated.append(item)
            continue
        updated.append(
            item.model_copy(
                update={
                    "bbox": match.bbox,
                    "visibility": match.visibility,
                    "confidence": match.confidence if match.confidence is not None else item.confidence,
                }
            )
        )
    return document.model_copy(update={"observations": updated})

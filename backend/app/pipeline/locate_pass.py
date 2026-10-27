"""A second look for one part, using a picture of that part.

The scene pass names every object at once. It drops a part whose pose changed,
or it draws that part's id on a neighbor. Extent matching removes the neighbor's
box. This pass asks only where the missing part is, and only on the frames
where its own box is absent. A box that comes back larger than the part is rejected.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.media import FrameAsset
from app.models.runs import AnalysisRun, ModelRequestRow
from app.perception.locate import apply_located, choose_locate_times, reference_time, same_surface
from app.pipeline.parsing import ParseFailure, extract_json_object
from app.pipeline.prompts import LOCATE_PART
from app.providers.registry import get_provider, needs_secret, run_transport
from app.providers.types import FrameInput, GenerationRequest
from app.schemas.analysis import AnalysisDocument, BBox
from app.services.costs import fail_provider_call, finish_provider_call, open_provider_call
from app.services.credentials import load_secret
from app.services.storage import data_path

logger = logging.getLogger(__name__)

MAX_OBJECTS = 8
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "timestamp": {"type": "number"},
                    "visibility": {"type": "string"},
                    "bbox": {
                        "anyOf": [
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "x": {"type": "number"},
                                    "y": {"type": "number"},
                                    "w": {"type": "number"},
                                    "h": {"type": "number"},
                                },
                                "required": ["x", "y", "w", "h"],
                            },
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["timestamp", "visibility", "bbox"],
            },
        }
    },
    "required": ["observations"],
}


async def locate_missing(
    session: Session,
    run: AnalysisRun,
    document: AnalysisDocument,
    frames: list[FrameAsset],
) -> tuple[AnalysisDocument, dict]:
    """Fill missing boxes for parts that already have a stable extent. Failures stay in the report."""
    if run.provider_id in {"fixture", "deterministic", "manual_json"} or not frames:
        return document, {"skipped": "no vision provider"}
    transport = run_transport(run.provider_id, run.config_json)
    secret = ""
    if needs_secret(run.provider_id, transport):
        secret, _source = load_secret(session, run.provider_id)
        if not secret:
            return document, {"skipped": "no API key"}
    config = run.config_json or {}
    sent = session.query(ModelRequestRow).filter(ModelRequestRow.run_id == run.id).count()
    budget = int(config.get("max_requests") or 12)
    targets = _targets(document)[:MAX_OBJECTS]
    located: list[str] = []
    errors: list[str] = []
    for object_id, times in targets:
        if sent >= budget:
            errors.append(f"{object_id}: request budget reached")
            break
        try:
            document = await _one(session, run, document, frames, object_id, times, secret, transport)
            located.append(object_id)
            sent += 1
        except Exception as exc:
            logger.warning("locate failed for %s: %s", object_id, exc)
            errors.append(f"{object_id}: {exc}")
    return document, {"located": located, "errors": errors}


def _targets(document: AnalysisDocument) -> list[tuple[str, list[float]]]:
    all_times = sorted({round(item.timestamp, 3) for item in document.observations})
    ranked: list[tuple[tuple, str, list[float]]] = []
    labels = {obj.local_id: obj for obj in document.objects}
    for object_id, obj in labels.items():
        kept = [
            round(item.timestamp, 3)
            for item in document.observations
            if item.object_local_id == object_id and item.bbox is not None
        ]
        chosen = choose_locate_times(kept, all_times)
        if reference_time(document.observations, object_id) is None or not chosen:
            continue
        tail = sum(1 for timestamp in all_times if kept and timestamp > max(kept))
        priority = (0 if obj.manipulated else 1, -tail, object_id)
        ranked.append((priority, object_id, chosen))
    ranked.sort(key=lambda item: item[0])
    return [(object_id, times) for _priority, object_id, times in ranked]


async def _one(session, run, document, frames, object_id, times, secret, transport: str) -> AnalysisDocument:
    reference = reference_time(document.observations, object_id)
    if reference is None or reference.bbox is None:
        return document
    source = _frame_at(frames, reference.timestamp)
    if source is None:
        raise RuntimeError(f"no stored frame near {reference.timestamp:.3f}s")
    image = cv2.imread(str(_stored(source)))
    if image is None:
        raise RuntimeError("reference frame could not be read")
    crop = _crop(image, reference.bbox)
    if crop.size == 0:
        raise RuntimeError("reference crop was empty")
    dest = data_path("runs", run.id, "locate", f"{object_id}.jpg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dest), crop):
        raise RuntimeError("reference crop could not be stored")
    selected: list[FrameInput] = [FrameInput(reference.timestamp, dest)]
    search_times: list[float] = []
    for timestamp in times:
        frame = _frame_at(frames, timestamp)
        if frame is None:
            continue
        selected.append(FrameInput(frame.timestamp, _stored(frame)))
        search_times.append(frame.timestamp)
    if len(selected) < 2:
        return document
    label = next((obj.label for obj in document.objects if obj.local_id == object_id), object_id)
    listed = ", ".join(f"{timestamp:.3f}s" for timestamp in search_times)
    user = (
        f"The part is {object_id} ({label}). "
        f"The first image is the reference crop from {reference.timestamp:.0f}s, not a frame to annotate. "
        f"Return a tight box for this part on the full frames at {listed}, or absent when it is fully hidden."
    )
    provider = get_provider(run.provider_id, session)
    config = run.config_json or {}
    request = GenerationRequest(
        model_id=run.model_id,
        system_prompt=LOCATE_PART,
        user_prompt=user,
        frames=selected,
        modality="sampled_frames",
        json_schema=_SCHEMA,
        temperature=config.get("temperature"),
        reasoning_effort=config.get("reasoning_effort"),
        image_detail=config.get("image_detail") or "high",
        timeout_s=float(config.get("timeout_s") or 180),
        metadata={"transport": transport} if run.provider_id in {"anthropic", "cursor"} else {},
    )
    row = open_provider_call(
        session,
        run,
        stage="locate",
        provider_id=run.provider_id,
        model_id=run.model_id,
        modality="sampled_frames",
        prompt_version_id=run.prompt_version_id,
        parameters={"object_id": object_id, "reference_timestamp": reference.timestamp},
        frame_timestamps=search_times,
    )
    session.commit()
    try:
        response = await provider.generate(secret, request)
    except Exception as exc:
        fail_provider_call(row, exc)
        session.commit()
        raise
    finish_provider_call(session, run, row, response)
    session.commit()
    sizes = {
        round(float(frame.timestamp), 3): (int(frame.width), int(frame.height))
        for frame in frames
        if frame.width and frame.height
    }
    found = _parse(response.text, set(search_times), sizes)
    return apply_located(document, object_id, _on_the_same_surface(crop, frames, found))


def _on_the_same_surface(reference, frames: list[FrameAsset], found: list[tuple[float, BBox | None, str]]):
    """Drop a follow-up box whose pixels are a different surface from the reference crop."""
    kept = []
    for timestamp, bbox, visibility in found:
        if bbox is None:
            kept.append((timestamp, bbox, visibility))
            continue
        frame = _frame_at(frames, timestamp)
        image = cv2.imread(str(_stored(frame))) if frame is not None else None
        candidate = _crop(image, bbox, pad=0.0) if image is not None else None
        if candidate is not None and candidate.size and same_surface(reference, candidate):
            kept.append((timestamp, bbox, visibility))
        else:
            kept.append((timestamp, None, visibility))
    return kept


def _fraction_box(raw: dict, width: int, height: int) -> BBox | None:
    """Accept 0–1 fractions or pixel coordinates from the frame that was sent."""
    try:
        x = float(raw["x"])
        y = float(raw["y"])
        w = float(raw["w"])
        h = float(raw["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if max(x, y, w, h) > 1.0 and width > 0 and height > 0:
        x, w = x / width, w / width
        y, h = y / height, h / height
    if w <= 0 or h <= 0:
        return None
    x = min(max(x, 0.0), 0.999)
    y = min(max(y, 0.0), 0.999)
    w = min(w, 1.0 - x)
    h = min(h, 1.0 - y)
    if w <= 0 or h <= 0:
        return None
    try:
        return BBox(x=x, y=y, w=w, h=h)
    except ValidationError:
        return None


def _parse(
    text: str, allowed: set[float], sizes: dict[float, tuple[int, int]] | None = None
) -> list[tuple[float, BBox | None, str]]:
    try:
        payload = extract_json_object(text)
    except ParseFailure:
        return []
    rows = payload.get("observations") or []
    if not isinstance(rows, list):
        return []
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            timestamp = float(row.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if not any(abs(timestamp - item) < 0.05 for item in allowed):
            continue
        raw = row.get("bbox")
        bbox = None
        if isinstance(raw, dict):
            width, height = _frame_size(sizes or {}, timestamp)
            bbox = _fraction_box(raw, width, height)
        visibility = str(row.get("visibility") or "visible")
        found.append((timestamp, bbox, visibility))
    return found


def _frame_size(sizes: dict[float, tuple[int, int]], timestamp: float) -> tuple[int, int]:
    if not sizes:
        return (0, 0)
    nearest = min(sizes, key=lambda item: abs(item - timestamp))
    if abs(nearest - timestamp) > 0.05:
        return (0, 0)
    return sizes[nearest]


def _frame_at(frames: list[FrameAsset], timestamp: float) -> FrameAsset | None:
    best = None
    best_dt = 0.2
    for frame in frames:
        delta = abs(float(frame.timestamp) - timestamp)
        if delta < best_dt:
            best = frame
            best_dt = delta
    return best


def _stored(frame: FrameAsset) -> Path:
    return data_path(*Path(frame.path).parts)


def _crop(image, bbox: BBox, pad: float = 0.03):
    height, width = image.shape[:2]
    x0 = max(0, int((bbox.x - pad) * width))
    y0 = max(0, int((bbox.y - pad) * height))
    x1 = min(width, int((bbox.x + bbox.w + pad) * width))
    y1 = min(height, int((bbox.y + bbox.h + pad) * height))
    return image[y0:y1, x0:x1]

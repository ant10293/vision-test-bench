"""Persist an analysis document as normalized rows and extract real frame crops."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.domain import (
    ActionRow,
    AmbiguityRow,
    DetectedObject,
    ObjectImage,
    ObjectObservation,
    ObjectRelationship,
    ObjectStateRow,
    ObjectStateTransition,
    SegmentationMask,
    WorkflowDependencyRow,
    WorkflowRow,
    WorkflowStepRow,
)
from app.models.media import FrameAsset
from app.models.util import new_id
from app.schemas.analysis import AnalysisDocument, BBox
from app.schemas.enums import OBJECT_COLORS
from app.services.storage import data_path, relative
from app.video.frames import read_bgr, sharpness_score


def materialize(
    session: Session,
    run_id: str,
    revision_id: str,
    document: AnalysisDocument,
    frames: list[FrameAsset],
) -> None:
    _clear(session, revision_id)
    frame_index = sorted(frames, key=lambda item: item.timestamp)
    objects: dict[str, DetectedObject] = {}
    for index, item in enumerate(document.objects):
        row = DetectedObject(
            id=new_id(),
            revision_id=revision_id,
            run_id=run_id,
            local_id=item.local_id,
            label=item.label,
            description=item.description,
            visual_characteristics=item.visual_characteristics,
            initial_state=item.initial_state,
            final_state=item.final_state,
            uncertain_identity=item.uncertain_identity,
            manipulated=item.manipulated,
            confidence=item.confidence,
            color=OBJECT_COLORS[index % len(OBJECT_COLORS)],
            accepted=item.accepted,
        )
        session.add(row)
        objects[item.local_id] = row
    session.flush()

    observations: dict[str, list[ObjectObservation]] = {key: [] for key in objects}
    for item in document.observations:
        obj = objects.get(item.object_local_id)
        if obj is None:
            continue
        frame = _nearest_frame(frame_index, item.timestamp)
        obs = ObjectObservation(
            object_id=obj.id,
            revision_id=revision_id,
            timestamp=item.timestamp,
            bbox_json=item.bbox.model_dump() if item.bbox else None,
            visibility=item.visibility.value,
            description=item.description,
            evidence_class=item.evidence.classification.value,
            provenance=item.evidence.provenance.value,
            evidence_note=item.evidence.note,
            frame_id=frame.id if frame else None,
            sharpness=item.sharpness,
            occlusion=item.occlusion,
            continuity=item.continuity.value if hasattr(item.continuity, "value") else str(item.continuity),
            coordinate_frame=item.coordinate_frame,
            camera_compensated=item.camera_compensated,
            track_ref=item.track_ref or "",
            accepted=item.accepted,
        )
        session.add(obs)
        observations[item.object_local_id].append(obs)
    for obj in objects.values():
        obj.observation_count = len(observations[obj.local_id])

    for item in document.states:
        obj = objects.get(item.object_local_id)
        if obj is None:
            continue
        session.add(
            ObjectStateRow(
                object_id=obj.id,
                revision_id=revision_id,
                time_start=item.time_start,
                time_end=item.time_end,
                state_label=item.state_label,
                description=item.description,
                evidence_class=item.evidence.classification.value,
                provenance=item.evidence.provenance.value,
                accepted=item.accepted,
            )
        )
    for item in document.transitions:
        obj = objects.get(item.object_local_id)
        if obj is None:
            continue
        session.add(
            ObjectStateTransition(
                object_id=obj.id,
                revision_id=revision_id,
                timestamp=item.timestamp,
                from_state=item.from_state,
                to_state=item.to_state,
                description=item.description,
                evidence_class=item.evidence.classification.value,
                provenance=item.evidence.provenance.value,
                certainty=item.certainty,
                accepted=item.accepted,
            )
        )
    for item in document.relationships:
        subject = objects.get(item.subject_local_id)
        if subject is None:
            continue
        other = objects.get(item.other_local_id) if item.other_local_id else None
        session.add(
            ObjectRelationship(
                revision_id=revision_id,
                subject_id=subject.id,
                other_id=other.id if other else None,
                kind=item.kind.value,
                time_start=item.time_start,
                time_end=item.time_end,
                description=item.description,
                evidence_class=item.evidence.classification.value,
                provenance=item.evidence.provenance.value,
                accepted=item.accepted,
            )
        )
    for item in document.actions:
        session.add(
            ActionRow(
                revision_id=revision_id,
                run_id=run_id,
                local_id=item.local_id,
                action_type=item.action_type,
                description=item.description,
                start=item.start,
                end=item.end,
                actor=item.actor or "",
                object_local_ids=item.object_local_ids,
                initial_state=item.initial_state,
                final_state=item.final_state,
                outcome=item.outcome.value,
                uncertainty=item.uncertainty,
                evidence_class=item.evidence.classification.value,
                provenance=item.evidence.provenance.value,
                evidence_note=item.evidence.note,
                evidence_timestamps=item.evidence.frame_timestamps,
                accepted=item.accepted,
            )
        )
    workflow = WorkflowRow(
        revision_id=revision_id,
        run_id=run_id,
        name="Reconstructed procedure",
        summary=document.summary,
    )
    session.add(workflow)
    session.flush()
    for index, item in enumerate(document.workflow_steps, start=1):
        session.add(
            WorkflowStepRow(
                workflow_id=workflow.id,
                revision_id=revision_id,
                local_id=item.local_id,
                number=index,
                name=item.name,
                required_objects=item.required_objects,
                preconditions=item.preconditions,
                expected_action=item.expected_action,
                completion_condition=item.completion_condition,
                observed_result=item.observed_result,
                action_local_ids=item.action_local_ids,
                time_start=item.time_start,
                time_end=item.time_end,
                uncertainty=item.uncertainty,
                evidence_class=item.evidence.classification.value,
                provenance=item.evidence.provenance.value,
                parallel_group=item.parallel_group or "",
                accepted=item.accepted,
            )
        )
    for item in document.dependencies:
        session.add(
            WorkflowDependencyRow(
                workflow_id=workflow.id,
                revision_id=revision_id,
                from_step=item.from_step,
                to_step=item.to_step,
                category=item.category.value,
                rationale=item.rationale,
                evidence_class=item.evidence.classification.value,
                provenance=item.evidence.provenance.value,
                accepted=item.accepted,
            )
        )
    for item in document.ambiguities:
        session.add(
            AmbiguityRow(
                revision_id=revision_id,
                run_id=run_id,
                local_id=item.local_id,
                kind=item.kind.value,
                description=item.description,
                time_start=item.time_start,
                time_end=item.time_end,
                object_local_ids=item.object_local_ids,
                action_local_ids=item.action_local_ids,
                alternative=item.alternative,
                evidence_class=item.evidence.classification.value,
                provenance=item.evidence.provenance.value,
                accepted=item.accepted,
            )
        )
    session.flush()
    for local_id, obj in objects.items():
        _extract_images(session, run_id, revision_id, obj, observations[local_id], frame_index)
    session.flush()


def _clear(session: Session, revision_id: str) -> None:
    object_ids = list(
        session.scalars(select(DetectedObject.id).where(DetectedObject.revision_id == revision_id))
    )
    if object_ids:
        session.execute(delete(DetectedObject).where(DetectedObject.revision_id == revision_id))
    session.execute(delete(ActionRow).where(ActionRow.revision_id == revision_id))
    session.execute(delete(AmbiguityRow).where(AmbiguityRow.revision_id == revision_id))
    session.execute(delete(SegmentationMask).where(SegmentationMask.revision_id == revision_id))
    workflow_ids = list(
        session.scalars(select(WorkflowRow.id).where(WorkflowRow.revision_id == revision_id))
    )
    if workflow_ids:
        session.execute(delete(WorkflowRow).where(WorkflowRow.revision_id == revision_id))
    session.flush()


def _nearest_frame(frames: list[FrameAsset], timestamp: float) -> FrameAsset | None:
    if not frames:
        return None
    return min(frames, key=lambda frame: abs(frame.timestamp - timestamp))


def _extract_images(session, run_id, revision_id, obj: DetectedObject, observations, frames) -> None:
    by_id = {frame.id: frame for frame in frames}
    scored: list[tuple[float, ObjectObservation, np.ndarray, np.ndarray]] = []
    for obs in observations:
        if not obs.bbox_json or not obs.frame_id or obs.visibility == "absent":
            continue
        frame = by_id.get(obs.frame_id)
        if frame is None:
            continue
        path = data_path(*Path(frame.path).parts)
        if not path.exists():
            continue
        image = read_bgr(path)
        bbox = BBox.model_validate(obs.bbox_json)
        crop = _crop(image, bbox)
        if crop.size == 0:
            continue
        sharp = sharpness_score(crop)
        area = bbox.w * bbox.h
        vis = {"visible": 1.0, "partial": 0.55, "occluded": 0.2}.get(obs.visibility, 0.4)
        occ = obs.occlusion or 0
        score = sharp * (0.15 + area) * vis * (1 - min(occ, 0.9))
        obs.sharpness = sharp
        scored.append((score, obs, image, crop))
    if not scored:
        return
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_obs, best_image, best_crop = scored[0]
    first = min(scored, key=lambda item: item[1].timestamp)
    last = max(scored, key=lambda item: item[1].timestamp)
    kinds = {
        "primary": (best_score, best_obs, best_image, best_crop),
        "unobstructed": (best_score, best_obs, best_image, best_crop),
        "initial": first,
        "final": last,
    }
    if len(scored) > 1:
        kinds["alternate"] = scored[1]
    folder = data_path("runs", run_id, "objects", obj.local_id)
    folder.mkdir(parents=True, exist_ok=True)
    primary_id = None
    for kind, (score, obs, image, crop) in kinds.items():
        crop_path = folder / f"{kind}_{obs.id[:8]}.png"
        boxed_path = folder / f"{kind}_{obs.id[:8]}_boxed.jpg"
        cv2.imwrite(str(crop_path), crop)
        boxed = image.copy()
        _draw_box(boxed, BBox.model_validate(obs.bbox_json), obj.color)
        cv2.imwrite(str(boxed_path), boxed)
        crop_row = ObjectImage(
            object_id=obj.id,
            revision_id=revision_id,
            kind="crop" if kind == "primary" else kind,
            path=relative(crop_path),
            frame_id=obs.frame_id,
            timestamp=obs.timestamp,
            width=crop.shape[1],
            height=crop.shape[0],
            score=score,
        )
        session.add(crop_row)
        session.flush()
        if kind == "primary":
            primary_id = crop_row.id
            obj.primary_image_id = primary_id
        session.add(
            ObjectImage(
                object_id=obj.id,
                revision_id=revision_id,
                kind="boxed" if kind == "primary" else f"{kind}_boxed",
                path=relative(boxed_path),
                frame_id=obs.frame_id,
                timestamp=obs.timestamp,
                width=boxed.shape[1],
                height=boxed.shape[0],
                score=score,
            )
        )


def _crop(image: np.ndarray, bbox: BBox, pad: float = 0.02) -> np.ndarray:
    height, width = image.shape[:2]
    x0 = max(0, int((bbox.x - pad) * width))
    y0 = max(0, int((bbox.y - pad) * height))
    x1 = min(width, int((bbox.x + bbox.w + pad) * width))
    y1 = min(height, int((bbox.y + bbox.h + pad) * height))
    return image[y0:y1, x0:x1]


def _draw_box(image: np.ndarray, bbox: BBox, color: str) -> None:
    height, width = image.shape[:2]
    x0, y0 = int(bbox.x * width), int(bbox.y * height)
    x1, y1 = int((bbox.x + bbox.w) * width), int((bbox.y + bbox.h) * height)
    bgr = _hex_bgr(color)
    cv2.rectangle(image, (x0, y0), (x1, y1), bgr, 2)


def _hex_bgr(color: str) -> tuple[int, int, int]:
    value = color.removeprefix("#")
    if len(value) != 6:
        return (255, 255, 255)
    red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return blue, green, red


def grabcut_cutout(image: np.ndarray, bbox: BBox) -> np.ndarray | None:
    height, width = image.shape[:2]
    x = max(0, int(bbox.x * width))
    y = max(0, int(bbox.y * height))
    w = max(2, min(width - x, int(bbox.w * width)))
    h = max(2, min(height - y, int(bbox.h * height)))
    if w < 4 or h < 4:
        return None
    mask = np.zeros((height, width), np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(image, mask, (x, y, w, h), bgd, fgd, 3, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    keep = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    if int(keep.sum()) < 10:
        return None
    bgra = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = keep
    return bgra[y : y + h, x : x + w]

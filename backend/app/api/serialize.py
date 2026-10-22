"""Read models into API dictionaries. Secrets are never included."""

from __future__ import annotations

from sqlalchemy import func, select
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
    WorkflowDependencyRow,
    WorkflowStepRow,
)
from app.models.media import FrameAsset, VideoAsset
from app.models.runs import AnalysisRevision, AnalysisRun, PipelineStageRun


def video_dict(video: VideoAsset, frame_count: int | None = None) -> dict:
    return {
        "id": video.id,
        "project_id": video.project_id,
        "filename": video.original_filename,
        "checksum": video.checksum_sha256,
        "duration_sec": video.duration_sec,
        "width": video.display_width,
        "height": video.display_height,
        "fps": video.fps,
        "avg_fps": video.avg_fps,
        "variable_frame_rate": video.variable_frame_rate,
        "codec": video.codec,
        "container": video.container,
        "rotation_deg": video.rotation_deg,
        "frame_count": video.frame_count,
        "size_bytes": video.size_bytes,
        "preview_frames": frame_count,
        "thumbnail_url": f"/api/media/videos/{video.id}/thumbnail" if video.thumbnail_path else "",
        "contact_sheet_url": f"/api/media/videos/{video.id}/contact-sheet" if video.contact_sheet_path else "",
        "playback_url": f"/api/media/videos/{video.id}/playback",
        "created_at": video.created_at.isoformat(),
    }


def run_summary(session: Session, run: AnalysisRun) -> dict:
    video = session.get(VideoAsset, run.video_id)
    revision_id = run.active_revision_id
    objects = actions = 0
    if revision_id:
        objects = session.scalar(
            select(func.count()).select_from(DetectedObject).where(DetectedObject.revision_id == revision_id)
        ) or 0
        actions = session.scalar(
            select(func.count()).select_from(ActionRow).where(ActionRow.revision_id == revision_id)
        ) or 0
    duration = None
    if run.started_at and run.finished_at:
        duration = (run.finished_at - run.started_at).total_seconds()
    return {
        "id": run.id,
        "project_id": run.project_id,
        "video_id": run.video_id,
        "name": run.name,
        "status": run.status,
        "research_mode": run.research_mode,
        "viewpoint": run.viewpoint,
        "task_constraint": run.task_constraint,
        "provider_id": run.provider_id,
        "model_id": run.model_id,
        "modality": run.modality,
        "sampling_preset": run.sampling_preset,
        "analysis_pass": run.analysis_pass,
        "segmentation_mode": run.segmentation_mode,
        "fixture": run.fixture,
        "fixture_label": run.fixture_label,
        "cost_usd": run.cost_usd,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "error": run.error,
        "object_count": objects,
        "action_count": actions,
        "duration_sec": duration,
        "created_at": run.created_at.isoformat(),
        "thumbnail_url": f"/api/media/videos/{run.video_id}/thumbnail" if video and video.thumbnail_path else "",
        "video_filename": video.original_filename if video else "",
    }


def bundle(session: Session, run: AnalysisRun) -> dict:
    video = session.get(VideoAsset, run.video_id)
    revision = session.get(AnalysisRevision, run.active_revision_id) if run.active_revision_id else None
    revision_id = revision.id if revision else ""
    objects = []
    if revision_id:
        rows = session.scalars(select(DetectedObject).where(DetectedObject.revision_id == revision_id)).all()
        for obj in rows:
            images = session.scalars(select(ObjectImage).where(ObjectImage.object_id == obj.id)).all()
            objects.append(_object(obj, images))
    frames = session.scalars(
        select(FrameAsset).where(FrameAsset.run_id == run.id).order_by(FrameAsset.timestamp)
    ).all()
    if not frames and video:
        frames = session.scalars(
            select(FrameAsset)
            .where(FrameAsset.video_id == video.id, FrameAsset.run_id.is_(None))
            .order_by(FrameAsset.timestamp)
        ).all()
    stages = session.scalars(select(PipelineStageRun).where(PipelineStageRun.run_id == run.id)).all()
    stage_order = {name: index for index, name in enumerate(_order())}
    return {
        "run": run_summary(session, run),
        "video": video_dict(video) if video else None,
        "revision": {
            "id": revision.id,
            "layer": revision.layer,
            "version": revision.version,
            "note": revision.note,
        }
        if revision
        else None,
        "coverage": run.coverage_json,
        "config": _public_config(run.config_json or {}),
        "stages": [
            _stage(stage)
            for stage in sorted(stages, key=lambda item: stage_order.get(item.stage, 99))
        ],
        "frames": [_frame(frame) for frame in frames],
        "objects": objects,
        "observations": _observations(session, revision_id),
        "states": _states(session, revision_id),
        "transitions": _transitions(session, revision_id),
        "relationships": _relationships(session, revision_id),
        "actions": _actions(session, revision_id),
        "steps": _steps(session, revision_id),
        "dependencies": _deps(session, revision_id),
        "ambiguities": _ambiguities(session, revision_id),
        "validation": (revision.document_json or {}).get("validation_issues", []) if revision else [],
        "summary": (revision.document_json or {}).get("summary", "") if revision else "",
        "camera": (revision.document_json or {}).get("camera") if revision else None,
    }


def _public_config(config: dict) -> dict:
    hidden = {"credentials", "api_key"}
    return {key: value for key, value in config.items() if key not in hidden and "key" not in key.lower()}


def _order() -> list[str]:
    from app.schemas.enums import PIPELINE_STAGES

    return PIPELINE_STAGES


def _stage(stage: PipelineStageRun) -> dict:
    return {
        "stage": stage.stage,
        "status": stage.status,
        "progress_current": stage.progress_current,
        "progress_total": stage.progress_total,
        "attempt": stage.attempt,
        "error": stage.error,
        "checkpoint": stage.checkpoint_json,
        "started_at": stage.started_at.isoformat() if stage.started_at else None,
        "finished_at": stage.finished_at.isoformat() if stage.finished_at else None,
    }


def _frame(frame: FrameAsset) -> dict:
    return {
        "id": frame.id,
        "timestamp": frame.timestamp,
        "reason": frame.reason,
        "motion_score": frame.motion_score,
        "sharpness": frame.sharpness,
        "scene_change": frame.scene_change,
        "url": f"/api/media/frames/{frame.id}",
    }


def _object(obj: DetectedObject, images: list[ObjectImage]) -> dict:
    primary = next((image for image in images if image.id == obj.primary_image_id), None)
    if primary is None:
        primary = next((image for image in images if image.kind in {"crop", "primary"}), None)
    return {
        "id": obj.id,
        "local_id": obj.local_id,
        "label": obj.label,
        "description": obj.description,
        "visual_characteristics": obj.visual_characteristics,
        "initial_state": obj.initial_state,
        "final_state": obj.final_state,
        "uncertain_identity": obj.uncertain_identity,
        "manipulated": obj.manipulated,
        "confidence": obj.confidence,
        "color": obj.color,
        "accepted": obj.accepted,
        "annotation_status": obj.annotation_status,
        "observation_count": obj.observation_count,
        "primary_image_url": f"/api/media/object-images/{primary.id}" if primary else "",
        "images": [
            {
                "id": image.id,
                "kind": image.kind,
                "timestamp": image.timestamp,
                "width": image.width,
                "height": image.height,
                "score": image.score,
                "url": f"/api/media/object-images/{image.id}" if image.path else "",
            }
            for image in images
        ],
    }


def _observations(session, revision_id: str) -> list[dict]:
    if not revision_id:
        return []
    rows = session.scalars(select(ObjectObservation).where(ObjectObservation.revision_id == revision_id)).all()
    objects = {
        obj.id: obj.local_id
        for obj in session.scalars(select(DetectedObject).where(DetectedObject.revision_id == revision_id))
    }
    return [
        {
            "id": row.id,
            "object_local_id": objects.get(row.object_id, ""),
            "timestamp": row.timestamp,
            "bbox": row.bbox_json,
            "visibility": row.visibility,
            "description": row.description,
            "evidence_class": row.evidence_class,
            "provenance": row.provenance,
            "evidence_note": row.evidence_note,
            "frame_id": row.frame_id,
            "continuity": row.continuity,
            "coordinate_frame": row.coordinate_frame,
            "camera_compensated": row.camera_compensated,
            "track_ref": row.track_ref,
        }
        for row in rows
    ]


def _states(session, revision_id: str) -> list[dict]:
    if not revision_id:
        return []
    rows = session.scalars(select(ObjectStateRow).where(ObjectStateRow.revision_id == revision_id)).all()
    objects = {
        obj.id: obj.local_id
        for obj in session.scalars(select(DetectedObject).where(DetectedObject.revision_id == revision_id))
    }
    return [
        {
            "id": row.id,
            "object_local_id": objects.get(row.object_id, ""),
            "time_start": row.time_start,
            "time_end": row.time_end,
            "state_label": row.state_label,
            "description": row.description,
            "evidence_class": row.evidence_class,
            "provenance": row.provenance,
        }
        for row in rows
    ]


def _transitions(session, revision_id: str) -> list[dict]:
    if not revision_id:
        return []
    rows = session.scalars(
        select(ObjectStateTransition).where(ObjectStateTransition.revision_id == revision_id)
    ).all()
    objects = {
        obj.id: obj.local_id
        for obj in session.scalars(select(DetectedObject).where(DetectedObject.revision_id == revision_id))
    }
    return [
        {
            "id": row.id,
            "object_local_id": objects.get(row.object_id, ""),
            "timestamp": row.timestamp,
            "from_state": row.from_state,
            "to_state": row.to_state,
            "description": row.description,
            "evidence_class": row.evidence_class,
            "provenance": row.provenance,
            "certainty": row.certainty,
        }
        for row in rows
    ]


def _relationships(session, revision_id: str) -> list[dict]:
    if not revision_id:
        return []
    rows = session.scalars(select(ObjectRelationship).where(ObjectRelationship.revision_id == revision_id)).all()
    objects = {
        obj.id: obj.local_id
        for obj in session.scalars(select(DetectedObject).where(DetectedObject.revision_id == revision_id))
    }
    return [
        {
            "id": row.id,
            "subject_local_id": objects.get(row.subject_id, ""),
            "other_local_id": objects.get(row.other_id, "") if row.other_id else None,
            "kind": row.kind,
            "time_start": row.time_start,
            "time_end": row.time_end,
            "description": row.description,
            "evidence_class": row.evidence_class,
            "provenance": row.provenance,
        }
        for row in rows
    ]


def _actions(session, revision_id: str) -> list[dict]:
    if not revision_id:
        return []
    rows = session.scalars(select(ActionRow).where(ActionRow.revision_id == revision_id)).all()
    return [
        {
            "id": row.id,
            "local_id": row.local_id,
            "action_type": row.action_type,
            "description": row.description,
            "start": row.start,
            "end": row.end,
            "actor": row.actor,
            "object_local_ids": row.object_local_ids,
            "initial_state": row.initial_state,
            "final_state": row.final_state,
            "outcome": row.outcome,
            "uncertainty": row.uncertainty,
            "evidence_class": row.evidence_class,
            "provenance": row.provenance,
            "evidence_note": row.evidence_note,
            "evidence_timestamps": row.evidence_timestamps,
            "accepted": row.accepted,
        }
        for row in sorted(rows, key=lambda item: item.start)
    ]


def _steps(session, revision_id: str) -> list[dict]:
    if not revision_id:
        return []
    rows = session.scalars(select(WorkflowStepRow).where(WorkflowStepRow.revision_id == revision_id)).all()
    return [
        {
            "id": row.id,
            "local_id": row.local_id,
            "number": row.number,
            "name": row.name,
            "required_objects": row.required_objects,
            "preconditions": row.preconditions,
            "expected_action": row.expected_action,
            "completion_condition": row.completion_condition,
            "observed_result": row.observed_result,
            "action_local_ids": row.action_local_ids,
            "time_start": row.time_start,
            "time_end": row.time_end,
            "uncertainty": row.uncertainty,
            "evidence_class": row.evidence_class,
            "provenance": row.provenance,
            "parallel_group": row.parallel_group,
        }
        for row in sorted(rows, key=lambda item: item.number)
    ]


def _deps(session, revision_id: str) -> list[dict]:
    if not revision_id:
        return []
    rows = session.scalars(
        select(WorkflowDependencyRow).where(WorkflowDependencyRow.revision_id == revision_id)
    ).all()
    return [
        {
            "id": row.id,
            "from_step": row.from_step,
            "to_step": row.to_step,
            "category": row.category,
            "rationale": row.rationale,
            "evidence_class": row.evidence_class,
            "provenance": row.provenance,
        }
        for row in rows
    ]


def _ambiguities(session, revision_id: str) -> list[dict]:
    if not revision_id:
        return []
    rows = session.scalars(select(AmbiguityRow).where(AmbiguityRow.revision_id == revision_id)).all()
    return [
        {
            "id": row.id,
            "local_id": row.local_id,
            "kind": row.kind,
            "description": row.description,
            "time_start": row.time_start,
            "time_end": row.time_end,
            "object_local_ids": row.object_local_ids,
            "action_local_ids": row.action_local_ids,
            "alternative": row.alternative,
            "evidence_class": row.evidence_class,
            "provenance": row.provenance,
        }
        for row in rows
    ]

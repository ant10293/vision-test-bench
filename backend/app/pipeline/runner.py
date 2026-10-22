"""Durable stage runner. Completed stages are skipped. Failures retry from the failed stage."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, SceneChunk, SegmentationMask, SpatialTrackPoint
from app.models.media import FrameAsset, VideoAsset
from app.models.runs import AnalysisRevision, AnalysisRun, Job, PipelineStageRun
from app.models.util import utcnow
from app.pipeline.deterministic_doc import document_from_spatial
from app.pipeline.infer import (
    BudgetExceeded,
    StageSkipped,
    load_working,
    produce_document,
    save_working,
)
from app.pipeline.prompts import compose
from app.pipeline.validate import validate_document
from app.providers.types import UnsupportedMode
from app.schemas.analysis import AnalysisDocument, Coverage
from app.schemas.enums import PIPELINE_STAGES
from app.services.ingest import playback_path, sample_run_frames
from app.services.materialize import grabcut_cutout, materialize
from app.services.storage import data_path, relative
from app.video.frames import contact_sheet, extract_frame, read_bgr
from app.video.probe import presentation_timestamps
from app.video.sampling import (
    annotate_sharpness,
    measure_motion,
    plan_adaptive,
    plan_uniform,
    preset_fps,
)
from app.video.spatial import analyze_sequence


async def execute_job(session: Session, job: Job) -> None:
    run = session.get(AnalysisRun, job.run_id)
    if run is None or run.status == "canceled":
        job.status = "canceled"
        job.finished_at = utcnow()
        session.commit()
        return
    video = session.get(VideoAsset, run.video_id)
    if video is None:
        _fail_job(session, job, run, "video missing")
        return
    run.status = "running"
    run.started_at = run.started_at or utcnow()
    run.error = ""
    session.commit()
    started = job.from_stage == ""
    try:
        for name in PIPELINE_STAGES:
            session.refresh(run)
            if run.status == "canceled":
                _cancel_remaining(session, run, name)
                job.status = "canceled"
                job.finished_at = utcnow()
                session.commit()
                return
            if not started:
                if name != job.from_stage:
                    continue
                started = True
            stage = _stage(session, run.id, name)
            if stage.status == "completed":
                continue
            stage.status = "running"
            stage.attempt += 1
            stage.started_at = utcnow()
            stage.error = ""
            stage.progress_current = 0
            stage.progress_total = 1
            job.heartbeat_at = utcnow()
            session.commit()
            try:
                await _dispatch(session, run, video, stage)
            except StageSkipped as skipped:
                stage.status = "skipped"
                stage.checkpoint_json = {"reason": skipped.reason, **(stage.checkpoint_json or {})}
                stage.finished_at = utcnow()
                session.commit()
                continue
            except Exception as exc:
                stage.status = "failed"
                stage.error = str(exc)[:2000]
                stage.finished_at = utcnow()
                if name in {"segmentation", "reinspection"}:
                    session.commit()
                    continue
                raise
            stage.status = "completed"
            stage.progress_current = max(stage.progress_total, 1)
            stage.finished_at = utcnow()
            session.commit()
        run.status = "completed"
        run.finished_at = utcnow()
        job.status = "completed"
        job.finished_at = utcnow()
        session.commit()
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)[:2000]
        run.finished_at = utcnow()
        if job.attempt < job.max_attempts and not isinstance(exc, (UnsupportedMode, BudgetExceeded)):
            job.status = "pending"
            job.error = str(exc)[:2000]
            job.worker_id = ""
        else:
            job.status = "failed"
            job.error = str(exc)[:2000]
            job.finished_at = utcnow()
        session.commit()


def _fail_job(session, job, run, message: str) -> None:
    job.status = "failed"
    job.error = message
    job.finished_at = utcnow()
    run.status = "failed"
    run.error = message
    session.commit()


def _cancel_remaining(session, run, current: str) -> None:
    seen = False
    for name in PIPELINE_STAGES:
        if name == current:
            seen = True
        if not seen:
            continue
        stage = _stage(session, run.id, name)
        if stage.status in {"pending", "running"}:
            stage.status = "canceled"
            stage.finished_at = utcnow()


def _stage(session, run_id: str, name: str) -> PipelineStageRun:
    stage = session.scalar(
        select(PipelineStageRun).where(PipelineStageRun.run_id == run_id, PipelineStageRun.stage == name)
    )
    if stage is None:
        stage = PipelineStageRun(run_id=run_id, stage=name, status="pending", checkpoint_json={})
        session.add(stage)
        session.flush()
    return stage


async def _dispatch(session, run: AnalysisRun, video: VideoAsset, stage: PipelineStageRun) -> None:
    name = stage.stage
    if name == "metadata":
        stage.checkpoint_json = {
            "duration_sec": video.duration_sec,
            "fps": video.fps,
            "avg_fps": video.avg_fps,
            "variable_frame_rate": video.variable_frame_rate,
            "width": video.display_width,
            "height": video.display_height,
            "checksum": video.checksum_sha256,
            "rotation_deg": video.rotation_deg,
        }
        stage.progress_total = 1
        stage.progress_current = 1
        return
    if name == "preprocess":
        path = playback_path(video)
        stage.checkpoint_json = {"playback": relative(path), "proxy": bool(video.proxy_path)}
        stage.progress_total = 1
        stage.progress_current = 1
        return
    if name == "sample_frames":
        _sample(session, run, video, stage)
        return
    if name == "spatial_tracking":
        _spatial(session, run, video, stage)
        return
    if name in {"scene_analysis", "action_analysis", "state_tracking", "workflow", "ambiguity"}:
        await _model_stage(session, run, video, stage)
        return
    if name == "object_extraction":
        _publish_current(session, run)
        stage.checkpoint_json = {"note": "Crops were taken from stored frames during materialization."}
        return
    if name == "segmentation":
        await _segment(session, run, video, stage)
        return
    if name == "validation":
        _validate(session, run, video, stage)
        return
    if name == "reinspection":
        await _reinspect(session, run, video, stage)
        return
    if name == "artifacts":
        _artifacts(session, run, stage)
        return
    stage.status = "skipped"
    stage.checkpoint_json = {"reason": "unknown stage"}


def _sample(session, run, video, stage) -> None:
    config = run.config_json or {}
    start = float(config.get("time_start") or 0)
    end = float(config.get("time_end") or video.duration_sec)
    pts_cache = data_path("originals", f"{video.checksum_sha256[:12]}.pts.json")
    original = data_path(*Path(video.stored_path).parts)
    pts = presentation_timestamps(original, pts_cache)
    pts = [item for item in pts if start - 0.01 <= item <= end + 0.01]
    preset = run.sampling_preset
    if run.modality == "native_video":
        fps = 1.0
        samples = plan_uniform(video.duration_sec, fps, pts)
        samples = [item for item in samples if start <= item.timestamp <= end]
        note = (
            "Native video was selected for the model. Preview frames were still extracted so the "
            "inspector can show evidence, but they are not a claim that the model saw only those frames."
        )
    elif preset == "adaptive":
        coarse = plan_uniform(video.duration_sec, 4.0, pts)
        coarse = [item for item in coarse if start <= item.timestamp <= end][:80]
        coarse_rows = sample_run_frames(session, video, run.id, coarse)
        signals = measure_motion(
            [data_path(*Path(row.path).parts) for row in coarse_rows],
            [row.timestamp for row in coarse_rows],
        )
        adapted = plan_adaptive(video.duration_sec, pts, coarse, signals)
        samples = [item for item in adapted.frames if start <= item.timestamp <= end]
        note = (
            f"Adaptive sampling kept {len(samples)} frames from a {len(coarse)}-frame motion pass. "
            "This is not full-frame coverage."
        )
        stage.checkpoint_json = {"scene_changes": adapted.scene_change_times}
    else:
        fps = preset_fps(preset, config.get("custom_fps"))
        samples = plan_uniform(video.duration_sec, fps, pts)
        samples = [item for item in samples if start <= item.timestamp <= end]
        note = (
            f"Uniform {fps:g} FPS sampling snapped to presentation timestamps. "
            f"{len(samples)} frames will be available. The source has about {video.frame_count} frames."
        )
    limit = int(config.get("max_frames") or 48)
    if len(samples) > limit:
        step = len(samples) / limit
        samples = [samples[min(len(samples) - 1, int(i * step))] for i in range(limit)]
        note += f" The frame budget truncated the set to {limit}."
    rows = sample_run_frames(session, video, run.id, samples)
    paths = {round(row.timestamp, 3): data_path(*Path(row.path).parts) for row in rows}
    annotate_sharpness(samples, paths)
    for sample, row in zip(samples, rows, strict=False):
        row.sharpness = sample.sharpness
        row.motion_score = sample.motion_score
    reasons = Counter(sample.reason for sample in samples)
    coverage = Coverage(
        modality="native_video" if run.modality == "native_video" else "sampled_frames",
        sampling_preset=preset,
        nominal_fps=preset_fps(preset, config.get("custom_fps")) if preset != "adaptive" else 4.0,
        frames_extracted=len(rows),
        frames_submitted=0 if run.modality == "native_video" else len(rows),
        frame_timestamps=[row.timestamp for row in rows],
        selection_reasons=dict(reasons),
        duration_sec=video.duration_sec,
        native_video_submitted=run.modality == "native_video",
        temporal_coverage_note=note,
    )
    run.coverage_json = coverage.model_dump(mode="json")
    stage.progress_total = max(len(rows), 1)
    stage.progress_current = len(rows)
    stage.checkpoint_json = {**(stage.checkpoint_json or {}), "frames": len(rows), "reasons": dict(reasons)}
    session.commit()


def _spatial(session, run, video, stage) -> None:
    frames = list(
        session.scalars(
            select(FrameAsset)
            .where(FrameAsset.run_id == run.id)
            .order_by(FrameAsset.timestamp)
        )
    )
    if len(frames) < 2:
        frames = list(
            session.scalars(
                select(FrameAsset)
                .where(FrameAsset.video_id == video.id, FrameAsset.run_id.is_(None))
                .order_by(FrameAsset.timestamp)
            )
        )
    paths = [data_path(*Path(frame.path).parts) for frame in frames]
    timestamps = [frame.timestamp for frame in frames]
    signals = measure_motion(paths, timestamps) if len(paths) >= 2 else []
    report = analyze_sequence(paths, timestamps, signals, run.research_mode)
    if run.research_mode == "auto":
        run.viewpoint = report.viewpoint
        run.task_constraint = report.task_constraint
        run.research_mode = report.research_mode
        config = dict(run.config_json or {})
        config["auto_characterization"] = {
            "mode": report.research_mode,
            "global_motion": report.global_motion,
            "scene_changes": report.scene_change_count,
            "notes": report.notes,
        }
        run.config_json = config
    session.query(SpatialTrackPoint).filter(SpatialTrackPoint.run_id == run.id).delete()
    session.query(SceneChunk).filter(SceneChunk.run_id == run.id).delete()
    for point in report.tracks:
        session.add(
            SpatialTrackPoint(
                run_id=run.id,
                video_id=video.id,
                track_ref=point.track_ref,
                timestamp=point.timestamp,
                bbox_json={"x": point.x, "y": point.y, "w": point.w, "h": point.h},
                motion_score=point.motion_score,
                continuity=point.continuity,
                coordinate_frame="image",
                camera_compensated=point.camera_compensated,
                scene_chunk=point.scene_chunk,
                identity_note=point.note,
            )
        )
    for chunk in report.chunks:
        session.add(
            SceneChunk(
                run_id=run.id,
                ordinal=chunk["ordinal"],
                time_start=chunk["time_start"],
                time_end=chunk["time_end"],
                scene_change=bool(chunk.get("scene_change")),
                global_motion=float(chunk.get("global_motion") or report.global_motion),
                notes=report.notes,
            )
        )
    coverage = Coverage.model_validate(run.coverage_json or {})
    document = document_from_spatial(report, coverage)
    spatial_path = data_path("runs", run.id, "spatial.json")
    spatial_path.parent.mkdir(parents=True, exist_ok=True)
    spatial_path.write_text(document.model_dump_json())
    stage.checkpoint_json = {
        "tracks": len({point.track_ref for point in report.tracks}),
        "points": len(report.tracks),
        "global_motion": report.global_motion,
        "scene_changes": report.scene_change_count,
        "world_coordinates_supported": False,
        "compensation_model": report.compensation_model,
        "notes": report.notes,
    }
    stage.progress_total = max(len(frames), 1)
    stage.progress_current = len(frames)


async def _model_stage(session, run, video, stage) -> None:
    config = run.config_json or {}
    single = config.get("analysis_pass", "single") == "single"
    if single and stage.stage != "scene_analysis":
        document = load_working(run.id)
        stage.checkpoint_json = {
            "source": "single_pass",
            "note": "This stage did not issue another model call. It records the slice of the single response.",
        }
        _remember(session, run, document, stage.stage)
        return
    if stage.stage != "scene_analysis" and run.provider_id in {"deterministic", "fixture"}:
        document = load_working(run.id)
        stage.checkpoint_json = {"source": run.provider_id}
        _remember(session, run, document, stage.stage)
        return
    frames = list(
        session.scalars(select(FrameAsset).where(FrameAsset.run_id == run.id).order_by(FrameAsset.timestamp))
    )
    coverage = Coverage.model_validate(run.coverage_json or {})
    spatial = None
    spatial_path = data_path("runs", run.id, "spatial.json")
    if spatial_path.exists() and run.provider_id == "deterministic":
        spatial = AnalysisDocument.model_validate(json.loads(spatial_path.read_text()))
    caps_window = 16
    from app.providers.catalog import lookup

    caps_window = lookup(run.provider_id, run.model_id).max_images
    config = dict(config)
    config["max_images_per_call"] = caps_window
    run.config_json = config
    document = await produce_document(session, run, video, frames, stage.stage, coverage, spatial)
    if run.provider_id == "fixture":
        run.fixture = True
        run.fixture_label = document.fixture_label
    if document.camera and run.research_mode == "auto":
        run.viewpoint = document.camera.viewpoint
        run.task_constraint = document.camera.task_constraint
    run.coverage_json = (document.coverage or coverage).model_dump(mode="json")
    save_working(run.id, document)
    _remember(session, run, document, stage.stage)
    stage.checkpoint_json = {
        "provider": run.provider_id,
        "model": run.model_id,
        "objects": len(document.objects),
        "actions": len(document.actions),
        "modality": run.modality,
    }


def _remember(session, run, document: AnalysisDocument, note: str) -> None:
    revision = session.scalar(
        select(AnalysisRevision).where(
            AnalysisRevision.run_id == run.id,
            AnalysisRevision.layer == "parsed",
        )
    )
    payload = document.model_dump(mode="json")
    if revision is None:
        revision = AnalysisRevision(
            run_id=run.id,
            layer="parsed",
            version=1,
            document_json=payload,
            note=note,
        )
        session.add(revision)
        session.flush()
    else:
        revision.document_json = payload
        revision.note = note
    frames = list(session.scalars(select(FrameAsset).where(FrameAsset.run_id == run.id)))
    if not frames:
        frames = list(
            session.scalars(select(FrameAsset).where(FrameAsset.video_id == run.video_id, FrameAsset.run_id.is_(None)))
        )
    materialize(session, run.id, revision.id, document, frames)
    if not _human_active(session, run):
        run.active_revision_id = revision.id
    session.commit()


def _publish_current(session, run) -> None:
    document = load_working(run.id)
    if document.objects or document.actions:
        _remember(session, run, document, "object_extraction")


async def _segment(session, run, video, stage) -> None:
    mode = run.segmentation_mode
    if mode in {"disabled", "boxes_only"}:
        raise StageSkipped(
            "Segmentation is off. Object images use boxes and rectangular crops from the analysis."
        )
    if not run.active_revision_id:
        raise StageSkipped("No analysis revision is available to segment.")
    if mode == "sam":
        if not _has_meta_key(session):
            raise UnsupportedMode(
                "SAM 3.1 was selected, but no Meta Model API key is configured. "
                "Boxes and crops were kept. Add MODEL_API_KEY or switch segmentation to boxes or GrabCut."
            )
        await _run_sam(session, run, stage)
        return
    if mode == "local_grabcut":
        _run_grabcut(session, run, stage)
        return
    raise StageSkipped(f"Unknown segmentation mode {mode}.")


def _has_meta_key(session) -> bool:
    from app.services.credentials import load_secret

    secret, _source = load_secret(session, "meta")
    return bool(secret)


async def _run_sam(session, run, stage) -> None:
    """One concept prompt per object label, on that object's clearest stored frame.

    SAM track ids are matched to LearnLoop objects by box overlap. A weak overlap
    is stored as an unassigned track instead of forcing the semantic id.
    """
    from app.models.domain import DetectedObject, ObjectImage, ObjectObservation
    from app.providers.sam import SamAdapter
    from app.schemas.analysis import BBox
    from app.services.credentials import load_secret

    secret, _source = load_secret(session, "meta")
    adapter = SamAdapter(secret, timeout_s=float((run.config_json or {}).get("timeout_s") or 180))
    objects = list(
        session.scalars(select(DetectedObject).where(DetectedObject.revision_id == run.active_revision_id))
    )
    stored = 0
    for obj in objects:
        obs = session.scalar(
            select(ObjectObservation)
            .where(
                ObjectObservation.object_id == obj.id,
                ObjectObservation.bbox_json.is_not(None),
                ObjectObservation.visibility == "visible",
            )
            .order_by(ObjectObservation.sharpness.desc())
        )
        if obs is None or not obs.frame_id:
            continue
        frame = session.get(FrameAsset, obs.frame_id)
        if frame is None:
            continue
        image_path = data_path(*Path(frame.path).parts)
        _text, records, latency_ms = await adapter.segment_image(image_path, obj.label)
        target = BBox.model_validate(obs.bbox_json)
        best = None
        best_iou = 0.0
        for record in records:
            iou = _iou_norm(target, record)
            if iou > best_iou:
                best_iou = iou
                best = record
        chosen = best if best is not None and best_iou >= 0.1 else (records[0] if records else None)
        uncertain = chosen is None or best_iou < 0.3
        if chosen is None:
            continue
        session.add(
            SegmentationMask(
                object_id=None if uncertain else obj.id,
                revision_id=run.active_revision_id,
                run_id=run.id,
                frame_id=frame.id,
                timestamp=obs.timestamp,
                track_ref=chosen.track_id,
                encoding=chosen.mask_encoding,
                path="",
                rle_json={"payload": chosen.mask_payload[:20000]},
                bbox_json={
                    "x": chosen.x1 / max(chosen.width, 1),
                    "y": chosen.y1 / max(chosen.height, 1),
                    "w": max(chosen.x2 - chosen.x1, 1) / max(chosen.width, 1),
                    "h": max(chosen.y2 - chosen.y1, 1) / max(chosen.height, 1),
                },
                confidence=chosen.confidence,
                source="sam-3.1",
                width=chosen.width,
                height=chosen.height,
                identity_uncertain=uncertain,
            )
        )
        if not uncertain:
            session.add(
                ObjectImage(
                    object_id=obj.id,
                    revision_id=run.active_revision_id,
                    kind="cutout",
                    path="",
                    frame_id=frame.id,
                    timestamp=obs.timestamp,
                    width=chosen.width,
                    height=chosen.height,
                    score=best_iou,
                )
            )
        stored += 1
        stage.checkpoint_json = {"source": "sam-3.1", "latency_ms": latency_ms, "records": stored}
    stage.progress_total = max(len(objects), 1)
    stage.progress_current = stored
    stage.checkpoint_json = {
        "source": "sam-3.1",
        "masks": stored,
        "note": "SAM tracks were linked only when box overlap supported the semantic id.",
    }


def _run_grabcut(session, run, stage) -> None:
    from app.models.domain import DetectedObject, ObjectImage, ObjectObservation

    objects = list(
        session.scalars(select(DetectedObject).where(DetectedObject.revision_id == run.active_revision_id))
    )
    written = 0
    for obj in objects:
        obs = session.scalar(
            select(ObjectObservation)
            .where(
                ObjectObservation.object_id == obj.id,
                ObjectObservation.bbox_json.is_not(None),
                ObjectObservation.visibility != "absent",
            )
            .order_by(ObjectObservation.timestamp)
        )
        if obs is None or not obs.frame_id:
            continue
        frame = session.get(FrameAsset, obs.frame_id)
        if frame is None:
            continue
        image = read_bgr(data_path(*Path(frame.path).parts))
        from app.schemas.analysis import BBox

        cutout = grabcut_cutout(image, BBox.model_validate(obs.bbox_json))
        if cutout is None:
            continue
        dest = data_path("runs", run.id, "objects", obj.local_id, "grabcut.png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dest), cutout)
        session.add(
            ObjectImage(
                object_id=obj.id,
                revision_id=run.active_revision_id,
                kind="cutout",
                path=relative(dest),
                frame_id=frame.id,
                timestamp=obs.timestamp,
                width=cutout.shape[1],
                height=cutout.shape[0],
                score=0,
            )
        )
        session.add(
            SegmentationMask(
                object_id=obj.id,
                revision_id=run.active_revision_id,
                run_id=run.id,
                frame_id=frame.id,
                timestamp=obs.timestamp,
                encoding="png",
                path=relative(dest),
                bbox_json=obs.bbox_json,
                source="grabcut",
                width=cutout.shape[1],
                height=cutout.shape[0],
                identity_uncertain=True,
            )
        )
        written += 1
    stage.checkpoint_json = {
        "source": "grabcut",
        "cutouts": written,
        "note": "GrabCut was initialized from stored boxes. These masks are not SAM output.",
    }
    stage.progress_total = max(len(objects), 1)
    stage.progress_current = written


def _validate(session, run, video, stage) -> None:
    document = load_working(run.id)
    issues = validate_document(document, video.duration_sec)
    errors = [issue for issue in issues if issue.severity == "error"]
    revision = AnalysisRevision(
        run_id=run.id,
        layer="validated",
        version=_next_version(session, run.id, "validated"),
        parent_id=run.active_revision_id,
        document_json=document.model_dump(mode="json"),
        note=f"{len(errors)} validation errors, {len(issues) - len(errors)} warnings",
    )
    session.add(revision)
    session.flush()
    frames = list(session.scalars(select(FrameAsset).where(FrameAsset.run_id == run.id)))
    materialize(session, run.id, revision.id, document, frames)
    if not _human_active(session, run):
        run.active_revision_id = revision.id
    stage.checkpoint_json = {"errors": len(errors), "warnings": len(issues) - len(errors)}
    stage.progress_total = max(len(issues), 1)
    stage.progress_current = max(len(issues), 1)


async def _reinspect(session, run, video, stage) -> None:
    if (run.config_json or {}).get("analysis_pass") != "multi_reinspect":
        raise StageSkipped("Targeted reinspection is off for this experiment.")
    if run.provider_id in {"deterministic", "fixture", "manual_json"}:
        raise StageSkipped("Reinspection needs a vision-language provider.")
    document = load_working(run.id)
    notes = []
    for ambiguity in document.ambiguities[:4]:
        if ambiguity.time_start is None:
            continue
        end = ambiguity.time_end if ambiguity.time_end is not None else ambiguity.time_start
        original = data_path(*Path(video.stored_path).parts)
        extra_rows = []
        cursor = ambiguity.time_start
        while cursor <= end and len(extra_rows) < 4:
            dest = data_path("frames", video.id, run.id, f"reinspect_{cursor:.3f}.jpg")
            extract_frame(original, cursor, dest)
            row = FrameAsset(
                video_id=video.id,
                run_id=run.id,
                timestamp=cursor,
                path=relative(dest),
                width=video.display_width,
                height=video.display_height,
                reason="reinspection",
                strategy="uncertainty_window",
                ordinal=1000 + len(extra_rows),
            )
            session.add(row)
            extra_rows.append(row)
            cursor += max((end - ambiguity.time_start) / 3, 0.2)
        session.flush()
        question = (
            f"What is visible between {ambiguity.time_start:.2f}s and {end:.2f}s? "
            "Compare the state before and after. Do not infer attachment from proximity. "
            f"Existing ambiguity: {ambiguity.description}"
        )
        try:
            refined = await produce_document(
                session,
                run,
                video,
                extra_rows,
                "reinspection",
                Coverage.model_validate(run.coverage_json or {}),
                None,
                user_extra=question,
            )
            notes.append(
                {
                    "ambiguity": ambiguity.local_id,
                    "question": question,
                    "original": ambiguity.description,
                    "refined_summary": refined.summary,
                    "frames": [row.timestamp for row in extra_rows],
                }
            )
        except Exception as exc:
            notes.append({"ambiguity": ambiguity.local_id, "error": str(exc)})
    document.reinspection_notes = [json.dumps(item) for item in notes]
    save_working(run.id, document)
    stage.checkpoint_json = {"reinspections": len(notes)}
    stage.progress_total = max(len(document.ambiguities), 1)
    stage.progress_current = len(notes)


def _artifacts(session, run, stage) -> None:
    document = load_working(run.id)
    folder = data_path("runs", run.id)
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        "analysis": document.model_dump(mode="json"),
        "objects": [item.model_dump(mode="json") for item in document.objects],
        "actions": [item.model_dump(mode="json") for item in document.actions],
        "workflow": {
            "steps": [item.model_dump(mode="json") for item in document.workflow_steps],
            "dependencies": [item.model_dump(mode="json") for item in document.dependencies],
        },
    }
    for kind, payload in files.items():
        path = folder / f"{kind}.json"
        path.write_text(json.dumps(payload, indent=2))
        session.add(Artifact(run_id=run.id, kind=kind, path=relative(path), label=kind))
    frames = list(
        session.scalars(select(FrameAsset).where(FrameAsset.run_id == run.id).order_by(FrameAsset.timestamp))
    )
    if frames:
        sheet = folder / "run_contact_sheet.jpg"
        contact_sheet([data_path(*Path(frame.path).parts) for frame in frames[:30]], sheet)
        session.add(Artifact(run_id=run.id, kind="contact_sheet", path=relative(sheet), label="Run contact sheet"))
    stage.checkpoint_json = {"files": list(files)}
    stage.progress_total = len(files)
    stage.progress_current = len(files)


def _iou_norm(target, record) -> float:
    bx = record.x1 / max(record.width, 1)
    by = record.y1 / max(record.height, 1)
    bw = max(record.x2 - record.x1, 1) / max(record.width, 1)
    bh = max(record.y2 - record.y1, 1) / max(record.height, 1)
    x1, y1 = max(target.x, bx), max(target.y, by)
    x2 = min(target.x + target.w, bx + bw)
    y2 = min(target.y + target.h, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = target.w * target.h + bw * bh - inter
    return inter / union if union else 0.0


def _human_active(session, run) -> bool:
    if not run.active_revision_id:
        return False
    revision = session.get(AnalysisRevision, run.active_revision_id)
    return bool(revision and revision.layer == "human")


def _next_version(session, run_id: str, layer: str) -> int:
    versions = session.scalars(
        select(AnalysisRevision.version).where(
            AnalysisRevision.run_id == run_id,
            AnalysisRevision.layer == layer,
        )
    )
    current = list(versions)
    return (max(current) if current else 0) + 1


def ensure_prompt_snapshot(run: AnalysisRun) -> None:
    if run.config_json.get("prompt_snapshot"):
        return
    config = dict(run.config_json)
    config["prompt_snapshot"] = compose("scene", run.research_mode)
    run.config_json = config

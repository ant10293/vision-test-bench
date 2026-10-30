"""Run tracking without sending those frames to the language model."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import SceneChunk, SpatialTrackPoint
from app.models.media import FrameAsset, VideoAsset
from app.models.runs import AnalysisRun, PipelineStageRun
from app.perception.frames import extract_tracking_frames
from app.perception.presets import config_from_mapping
from app.pipeline.deterministic_doc import document_from_spatial
from app.schemas.analysis import Coverage
from app.services.storage import data_path, resolve_stored
from app.video.sampling import distinct_moments, measure_motion
from app.video.spatial import analyze_sequence


def run_spatial_stage(session: Session, run: AnalysisRun, video: VideoAsset, stage: PipelineStageRun) -> None:
    config = config_from_mapping((run.config_json or {}).get("perception"))
    paths, timestamps, frame_source, limitation = _paths(session, run, video, config)
    signals = measure_motion(paths, timestamps) if len(paths) >= 2 else []

    def should_stop() -> bool:
        session.refresh(run)
        return run.status == "canceled"

    report = analyze_sequence(
        paths,
        timestamps,
        signals,
        run.research_mode,
        config=config,
        cache_dir=data_path("runs", run.id, "embeddings"),
        should_stop=should_stop,
    )
    if run.research_mode == "auto":
        run.viewpoint = report.viewpoint
        run.task_constraint = report.task_constraint
        run.research_mode = report.research_mode
        stored = dict(run.config_json or {})
        stored["auto_characterization"] = {
            "mode": report.research_mode,
            "global_motion": report.global_motion,
            "scene_changes": report.scene_change_count,
            "notes": report.notes,
        }
        run.config_json = stored
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
                track_status=point.status,
                association_score=point.association_score,
                detector_id=point.detector_id,
                relative_scale=point.relative_scale,
                angle_deg=point.angle_deg,
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
    diagnostics = dict(report.perception.diagnostics) if report.perception else {}
    diagnostics["frame_source"] = frame_source
    if limitation:
        diagnostics["limitation"] = limitation
    stage.checkpoint_json = {
        "tracks": len({point.track_ref for point in report.tracks}),
        "points": len(report.tracks),
        "global_motion": report.global_motion,
        "scene_changes": report.scene_change_count,
        "world_coordinates_supported": False,
        "compensation_model": report.compensation_model,
        "notes": report.notes,
        "strategy": config.strategy,
        "detector": config.detector,
        "encoder": config.encoder,
        "encoder_status": report.perception.encoder_status if report.perception else "not_requested",
        "encoder_detail": report.perception.encoder_detail if report.perception else "",
        "diagnostics": diagnostics,
    }
    stage.progress_total = max(len(paths), 1)
    stage.progress_current = len(paths)


def _paths(session: Session, run: AnalysisRun, video: VideoAsset, config) -> tuple[list[Path], list[float], str, str]:
    sampled = list(
        session.scalars(select(FrameAsset).where(FrameAsset.run_id == run.id).order_by(FrameAsset.timestamp))
    )
    if len(sampled) < 2:
        sampled = list(
            session.scalars(
                select(FrameAsset)
                .where(FrameAsset.video_id == video.id, FrameAsset.run_id.is_(None))
                .order_by(FrameAsset.timestamp)
            )
        )
    sampled = distinct_moments(sampled)
    sampled_paths = [data_path(*Path(frame.path).parts) for frame in sampled]
    sampled_times = [frame.timestamp for frame in sampled]
    if not config.separate_stream:
        return sampled_paths, sampled_times, "sampled_frames", ""
    source = resolve_stored(video.stored_path)
    cache = data_path(
        "frames",
        video.checksum_sha256[:12],
        "track",
        f"{float(config.target_fps):g}fps_{int(config.maximum_frames)}",
    )
    try:
        rows = extract_tracking_frames(
            source,
            cache,
            float(config.target_fps),
            int(config.maximum_frames),
            float(video.duration_sec or 0),
        )
    except (RuntimeError, OSError) as exc:
        return sampled_paths, sampled_times, "sampled_frames", f"Tracking-frame extraction failed ({exc}). Sampled frames were used."
    if len(rows) < 2:
        return sampled_paths, sampled_times, "sampled_frames", "Tracking-frame extraction returned too few frames. Sampled frames were used."
    return [path for _timestamp, path in rows], [timestamp for timestamp, _path in rows], "tracking_stream", ""


def load_spatial_document(run_id: str):
    path = data_path("runs", run_id, "spatial.json")
    if not path.exists():
        return None
    from app.schemas.analysis import AnalysisDocument

    return AnalysisDocument.model_validate(json.loads(path.read_text()))


__all__ = ["run_spatial_stage", "load_spatial_document"]

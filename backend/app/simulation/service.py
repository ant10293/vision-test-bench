"""Create simulation datasets and run their renders off the request thread."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models.media import VideoAsset
from app.models.runs import AnalysisRun
from app.models.simulation import SimulationDataset, SimulationEvaluation, SimulationJob
from app.models.util import new_id, utcnow
from app.services.ingest import ingest_file
from app.services.storage import data_path
from app.simulation.presets import CAMERA_MODES, RESEARCH_MODES, VARIANTS, build_assembly
from app.simulation.render import RenderCancelled, render_dataset
from app.simulation.schema import Scene
from app.simulation.scoring import analysis_payload, assert_independent, score_dataset

QUALITY_SAMPLES = {"preview": 1, "standard": 8}
CAMERA_FOR_RESEARCH = {
    "fixed_camera": "fixed",
    "constrained_head_mounted": "constrained_head",
    "unconstrained_head_mounted": "unconstrained_head",
    "auto": "fixed",
}


class SimulationError(ValueError):
    pass


def dataset_dir(dataset_id: str) -> Path:
    return data_path("simulations", dataset_id)


def scene_for(dataset: SimulationDataset, scene_id: str | None = None) -> Scene:
    return build_assembly(
        variant=dataset.variant,
        camera_mode=dataset.camera_mode,
        seed=dataset.seed,
        width=dataset.width,
        height=dataset.height,
        fps=dataset.fps,
        duration=dataset.duration,
        randomize=dataset.randomize,
        scene_id=scene_id or dataset.id,
        pair_key=dataset.pair_key,
    )


def enqueue(
    session: Session,
    variant: str,
    camera_mode: str,
    seed: int = 1,
    width: int = 320,
    height: int = 240,
    fps: int = 8,
    duration: float = 6.0,
    quality: str = "preview",
    randomize: bool = False,
    pair_key: str = "",
    name: str = "",
    research_mode: str = "",
    analysis_config: dict | None = None,
) -> tuple[SimulationDataset, SimulationJob]:
    if variant not in VARIANTS:
        raise SimulationError(f"unknown variant {variant}")
    if camera_mode not in CAMERA_MODES:
        raise SimulationError(f"unknown camera mode {camera_mode}")
    if quality not in QUALITY_SAMPLES:
        raise SimulationError(f"unknown quality {quality}")
    if not pair_key:
        pair_key = f"assembly:{seed}:{camera_mode}:{width}x{height}:{fps}:{duration}:{int(randomize)}"
    dataset_id = new_id()
    dataset = SimulationDataset(
        id=dataset_id,
        name=name or f"Assembly {variant}",
        preset="assembly",
        variant=variant,
        camera_mode=camera_mode,
        research_mode=research_mode or RESEARCH_MODES[camera_mode],
        analysis_config=analysis_config,
        seed=seed,
        quality=quality,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        randomize=randomize,
        pair_key=pair_key,
        status="pending",
        relative_dir=f"simulations/{dataset_id}",
    )
    job = SimulationJob(dataset_id=dataset.id, frame_total=0)
    session.add(dataset)
    session.add(job)
    session.commit()
    session.refresh(dataset)
    session.refresh(job)
    return dataset, job


def execute_simulation(job_id: str) -> None:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        job = session.get(SimulationJob, job_id)
        if job is None:
            return
        dataset = session.get(SimulationDataset, job.dataset_id)
        if dataset is None:
            return
        dataset.status = "running"
        job.stage = "evaluate"
        session.commit()
        scene = scene_for(dataset)
        root = dataset_dir(dataset.id)

        def on_progress(stage: str, frame: int, total: int) -> None:
            _touch(job_id, stage, frame, total)

        def should_cancel() -> bool:
            check = SessionLocal()
            try:
                current = check.get(SimulationJob, job_id)
                return bool(current and current.cancel_requested)
            finally:
                check.close()

        try:
            manifest = render_dataset(
                scene,
                root,
                dataset.id,
                samples=QUALITY_SAMPLES[dataset.quality],
                timeout_sec=float(settings.simulation_timeout_sec),
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
        except RenderCancelled:
            _finish(job_id, "canceled", "cancelled")
            return
        except Exception as exc:
            _finish(job_id, "failed", str(exc)[-2000:])
            return
        _touch(job_id, "ingest", manifest["frame_count"], manifest["frame_count"])
        try:
            video = ingest_file(
                session,
                None,
                f"sim-{dataset.id}.mp4",
                root / "videos" / "execution.mp4",
                research_mode=dataset.research_mode,
            )
        except Exception as exc:
            _finish(job_id, "failed", str(exc)[-2000:])
            return
        dataset = session.get(SimulationDataset, dataset.id)
        job = session.get(SimulationJob, job_id)
        if dataset is None or job is None:
            return
        dataset.video_id = video.id
        dataset.blender_version = manifest.get("blender_version") or ""
        dataset.engine = manifest.get("engine") or ""
        dataset.frame_count = int(manifest.get("frame_count") or 0)
        follow_error = _start_saved_analysis(session, dataset, video.id)
        dataset.status = "completed"
        dataset.finished_at = utcnow()
        dataset.error = follow_error
        job.status = "completed"
        job.stage = "complete"
        job.finished_at = utcnow()
        job.error = follow_error
        session.commit()
    finally:
        session.close()


def _start_saved_analysis(session: Session, dataset: SimulationDataset, video_id: str) -> str:
    if not dataset.analysis_config:
        return ""
    from app.services.experiments import create_run

    payload = {key: value for key, value in dict(dataset.analysis_config).items() if key != "scene"}
    payload["video_id"] = video_id
    try:
        run = create_run(session, payload)
    except Exception as exc:
        return str(exc)[-2000:]
    dataset.analysis_run_id = run.id
    return ""


def _touch(job_id: str, stage: str, frame: int, total: int) -> None:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        job = session.get(SimulationJob, job_id)
        if job is None:
            return
        job.stage = stage
        job.frame_current = frame
        job.frame_total = total
        job.heartbeat_at = utcnow()
        session.commit()
    finally:
        session.close()


def _finish(job_id: str, status: str, error: str) -> None:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        job = session.get(SimulationJob, job_id)
        if job is None:
            return
        dataset = session.get(SimulationDataset, job.dataset_id)
        job.status = status
        job.error = error
        job.finished_at = utcnow()
        if dataset is not None and dataset.status != "completed":
            dataset.status = status
            dataset.error = error
            dataset.finished_at = utcnow()
        session.commit()
    finally:
        session.close()


def recover_simulation_jobs() -> int:
    from app.db import SessionLocal

    session = SessionLocal()
    recovered = 0
    try:
        jobs = session.scalars(select(SimulationJob).where(SimulationJob.status == "running")).all()
        for job in jobs:
            dataset = session.get(SimulationDataset, job.dataset_id)
            if job.attempt >= job.max_attempts:
                job.status = "failed"
                job.error = "worker stopped before the render finished"
                job.finished_at = utcnow()
                if dataset is not None and dataset.status == "running":
                    dataset.status = "failed"
                    dataset.error = job.error
            else:
                job.status = "pending"
                job.worker_id = ""
                job.error = "requeued after process restart"
                if dataset is not None and dataset.status == "running":
                    dataset.status = "pending"
            recovered += 1
        session.commit()
    finally:
        session.close()
    return recovered


def claim_simulation(worker_id: str) -> str | None:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        pending = session.scalar(select(SimulationJob).where(SimulationJob.status == "pending").order_by(SimulationJob.created_at))
        if pending is None:
            return None
        result = session.execute(
            update(SimulationJob)
            .where(SimulationJob.id == pending.id, SimulationJob.status == "pending")
            .values(
                status="running",
                worker_id=worker_id,
                started_at=utcnow(),
                heartbeat_at=utcnow(),
                attempt=SimulationJob.attempt + 1,
            )
        )
        session.commit()
        if result.rowcount != 1:
            return None
        return pending.id
    finally:
        session.close()


def delete_dataset(session: Session, dataset: SimulationDataset) -> None:
    """Remove the simulation files. The ingested video stays in the library."""
    root = dataset_dir(dataset.id)
    if root.exists():
        shutil.rmtree(root)
    session.delete(dataset)
    session.commit()


def load_scene(dataset: SimulationDataset) -> Scene:
    path = dataset_dir(dataset.id) / "scene.json"
    if dataset.status != "completed" or not path.exists():
        raise SimulationError("this dataset has no published scene")
    return Scene.model_validate_json(path.read_text())


def evaluate_run(session: Session, dataset: SimulationDataset, run: AnalysisRun) -> SimulationEvaluation:
    if dataset.status != "completed" or not dataset.video_id:
        raise SimulationError("the dataset is not ready to score")
    if run.video_id != dataset.video_id:
        raise SimulationError("that run is not an analysis of this dataset's video")
    if run.status != "completed" or not run.active_revision_id:
        raise SimulationError("the analysis has not finished")
    assert_independent(run)
    from app.models.runs import AnalysisRevision

    revision = session.get(AnalysisRevision, run.active_revision_id)
    if revision is None:
        raise SimulationError("the analysis has no revision")
    result = score_dataset(dataset_dir(dataset.id), revision.document_json)
    perception = ((run.config_json or {}).get("perception") or {}).get("preset") or ""
    row = SimulationEvaluation(
        dataset_id=dataset.id,
        run_id=run.id,
        perception_preset=str(perception),
        result_json=result,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def playback_request(dataset: SimulationDataset, perception_preset: str = "B_temporal") -> dict:
    if not dataset.video_id:
        raise SimulationError("the dataset has no video yet")
    return analysis_payload(
        dataset.video_id,
        dataset.research_mode,
        perception_preset=perception_preset,
        name=f"Simulation {dataset.id[:8]}",
    )


def summary(session: Session, dataset: SimulationDataset) -> dict:
    job = session.scalar(
        select(SimulationJob).where(SimulationJob.dataset_id == dataset.id).order_by(SimulationJob.created_at.desc())
    )
    video = session.get(VideoAsset, dataset.video_id) if dataset.video_id else None
    return {
        "id": dataset.id,
        "name": dataset.name,
        "preset": dataset.preset,
        "variant": dataset.variant,
        "camera_mode": dataset.camera_mode,
        "research_mode": dataset.research_mode,
        "seed": dataset.seed,
        "quality": dataset.quality,
        "width": dataset.width,
        "height": dataset.height,
        "fps": dataset.fps,
        "duration": dataset.duration,
        "randomize": dataset.randomize,
        "pair_key": dataset.pair_key,
        "status": dataset.status,
        "error": dataset.error,
        "video_id": dataset.video_id,
        "analysis_run_id": dataset.analysis_run_id,
        "video_filename": video.original_filename if video else "",
        "blender_version": dataset.blender_version,
        "engine": dataset.engine,
        "frame_count": dataset.frame_count,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else "",
        "finished_at": dataset.finished_at.isoformat() if dataset.finished_at else "",
        "job": None
        if job is None
        else {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "frame_current": job.frame_current,
            "frame_total": job.frame_total,
            "error": job.error,
            "cancel_requested": job.cancel_requested,
        },
    }

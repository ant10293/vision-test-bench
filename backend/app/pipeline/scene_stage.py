"""Render a scene from the objects found in the selected video."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.media import VideoAsset
from app.models.runs import AnalysisRun, PipelineStageRun
from app.pipeline.infer import StageSkipped, load_working


def render_blender_scene(session: Session, run: AnalysisRun, stage: PipelineStageRun) -> None:
    requested = (run.config_json or {}).get("scene")
    if not isinstance(requested, dict) or requested.get("skip", True):
        raise StageSkipped("Blender scene creation was skipped.")
    from app.models.simulation import SimulationDataset
    from app.models.util import new_id, utcnow
    from app.services.ingest import ingest_file, playback_path
    from app.simulation.from_video import records_from_document, scene_from_tracks
    from app.simulation.render import render_dataset
    from app.simulation.service import QUALITY_SAMPLES, dataset_dir
    from app.simulation.textures import write_initial_textures

    video = session.get(VideoAsset, run.video_id)
    if video is None:
        raise RuntimeError("The selected video is missing, so no scene was built.")
    tracks = records_from_document(load_working(run.id))
    dataset_id = new_id()
    scene = scene_from_tracks(
        tracks,
        width=video.display_width or video.width,
        height=video.display_height or video.height,
        fps=video.fps or video.avg_fps,
        duration=video.duration_sec,
        scene_id=dataset_id,
        name=video.original_filename,
    )
    dataset = SimulationDataset(
        id=dataset_id,
        name=video.original_filename,
        preset="video",
        variant="objects",
        camera_mode="fixed",
        research_mode=run.research_mode or video.research_mode or "",
        seed=1,
        quality="preview",
        width=scene.width,
        height=scene.height,
        fps=scene.fps,
        duration=scene.duration,
        randomize=False,
        pair_key="",
        status="running",
        relative_dir=f"simulations/{dataset_id}",
        analysis_run_id=run.id,
    )
    session.add(dataset)
    session.commit()

    def on_progress(phase: str, frame: int, total: int) -> None:
        stage.progress_current = frame
        stage.progress_total = total or 1
        stage.checkpoint_json = {"dataset_id": dataset.id, "phase": phase, "objects": len(scene.objects)}
        session.commit()

    root = dataset_dir(dataset.id)
    try:
        textures = write_initial_textures(
            playback_path(video),
            tracks,
            root / "textures",
            float(video.duration_sec or scene.duration),
        )
        manifest = render_dataset(
            scene,
            root,
            dataset.id,
            samples=QUALITY_SAMPLES["preview"],
            on_progress=on_progress,
            textures=textures,
        )
        rendered = ingest_file(
            session,
            run.project_id,
            f"sim-{dataset.id}.mp4",
            root / "videos" / "execution.mp4",
            research_mode=dataset.research_mode,
        )
    except Exception as exc:
        dataset.status = "failed"
        dataset.error = str(exc)[-2000:]
        dataset.finished_at = utcnow()
        session.commit()
        raise
    dataset.video_id = rendered.id
    dataset.status = "completed"
    dataset.finished_at = utcnow()
    dataset.blender_version = manifest.get("blender_version") or ""
    dataset.engine = manifest.get("engine") or ""
    dataset.frame_count = int(manifest.get("frame_count") or 0)
    dataset.error = ""
    stage.progress_total = dataset.frame_count or stage.progress_total or 1
    stage.progress_current = stage.progress_total
    stage.checkpoint_json = {
        "dataset_id": dataset.id,
        "objects": len(scene.objects),
        "textures": len(textures),
    }
    session.commit()

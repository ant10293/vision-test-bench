"""Create and control analysis runs."""

from __future__ import annotations

import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.audio.engine import WHISPER_MODELS
from app.config import REPO_ROOT
from app.models.media import VideoAsset
from app.models.runs import AnalysisRun, Job, PipelineStageRun, PromptVersion
from app.models.util import new_id
from app.perception.presets import preset
from app.pipeline.prompts import TEMPLATES, compose
from app.providers.catalog import lookup
from app.providers.claude_code import load_claude_code
from app.providers.types import UnsupportedMode
from app.schemas.enums import PIPELINE_STAGES, ResearchMode


def scene_flag(payload: dict) -> dict:
    """The only scene fact stored on an analysis run: whether to render one.

    Absent means skip, so older runs do not gain a render. A variant name is not stored.
    """
    scene = payload.get("scene")
    if not isinstance(scene, dict):
        return {"skip": True}
    return {"skip": bool(scene.get("skip", False))}


MODE_AXES = {
    "fixed_camera": ("fixed", "constrained"),
    "constrained_head_mounted": ("head_mounted", "constrained"),
    "unconstrained_head_mounted": ("head_mounted", "unconstrained"),
    "auto": ("unknown", "unknown"),
}


def create_run(session: Session, payload: dict, enqueue: bool = True) -> AnalysisRun:
    video_id = payload.get("video_id")
    if not video_id:
        raise ValueError("video not found")
    video = session.get(VideoAsset, video_id)
    if video is None:
        raise ValueError("video not found")
    mode = payload.get("research_mode") or ResearchMode.FIXED_CAMERA.value
    if mode not in MODE_AXES:
        raise ValueError(f"unknown research mode {mode}")
    provider_id = payload["provider_id"]
    model_id = payload["model_id"]
    modality = payload.get("modality") or "sampled_frames"
    caps = lookup(provider_id, model_id)
    if modality == "native_video" and not caps.native_video:
        raise UnsupportedMode(
            f"{model_id} does not support native video. Choose sampled frames. "
            "The selection was not changed for you."
        )
    if modality == "sampled_frames" and not caps.image and provider_id not in {"manual_json"}:
        raise UnsupportedMode(f"{model_id} does not accept images.")
    temperature = payload.get("temperature")
    if temperature is not None and not caps.supports_temperature:
        raise UnsupportedMode(f"{model_id} does not accept temperature. Leave it unset.")
    anthropic_transport = "api"
    if provider_id == "anthropic":
        anthropic_transport = load_claude_code()["transport"]
        if anthropic_transport == "claude_code" and temperature is not None:
            raise UnsupportedMode(
                "Claude Code does not accept temperature. Leave it unset, "
                "or switch Anthropic runs to the API key."
            )
    reasoning = payload.get("reasoning_effort") or None
    if reasoning and reasoning not in caps.reasoning_efforts:
        raise UnsupportedMode(
            f"Reasoning effort '{reasoning}' is not available on {model_id}. "
            f"Supported: {', '.join(caps.reasoning_efforts) or 'none'}."
        )
    transcribe = bool(payload.get("transcribe"))
    use_transcript = bool(payload.get("use_transcript"))
    if use_transcript and not transcribe:
        raise ValueError("Transcript-assisted analysis requires local transcription.")
    whisper_model = payload.get("whisper_model") or "base"
    if whisper_model not in WHISPER_MODELS:
        raise ValueError(f"Unknown Whisper model {whisper_model}.")
    prompt = _latest_prompt(session, payload.get("prompt_key") or "scene")
    perception_name = payload.get("perception_preset") or "B_temporal"
    perception = preset(perception_name).to_dict()
    perception["preset"] = perception_name
    viewpoint, constraint = MODE_AXES[mode]
    config = {
        "research_mode": mode,
        "provider_id": provider_id,
        "model_id": model_id,
        "modality": modality,
        "sampling_preset": payload.get("sampling_preset") or "standard",
        "custom_fps": payload.get("custom_fps"),
        "analysis_pass": payload.get("analysis_pass") or "single",
        "segmentation_mode": payload.get("segmentation_mode") or "disabled",
        "segmentation_model_id": payload.get("segmentation_model_id") or "",
        "temperature": temperature,
        "reasoning_effort": reasoning,
        "image_detail": payload.get("image_detail") or "high",
        "timeout_s": payload.get("timeout_s") or 180,
        "max_requests": payload.get("max_requests") or 12,
        "max_frames": payload.get("max_frames") or 48,
        "max_retries": payload.get("max_retries") or 2,
        "cost_ceiling_usd": payload.get("cost_ceiling_usd"),
        "time_start": payload.get("time_start"),
        "time_end": payload.get("time_end"),
        "prompt_key": prompt.key if prompt else "scene",
        "prompt_version": prompt.version if prompt else 1,
        "prompt_snapshot": compose(prompt.key if prompt else "scene", mode),
        "transcribe": transcribe,
        "use_transcript": use_transcript,
        "whisper_model": whisper_model,
        "whisper_language": (payload.get("whisper_language") or "").strip(),
        "whisper_vad": payload.get("whisper_vad", True) is not False,
        "word_timestamps": bool(payload.get("word_timestamps")),
        "align_window_sec": float(payload.get("align_window_sec") or 4),
        "transcript_text_may_be_sent": use_transcript and provider_id not in {"fixture", "deterministic", "manual_json"},
        "capabilities": caps.as_dict(),
        "app_version": __version__,
        "git_revision": _git(),
        "perception": perception,
        "scene": scene_flag(payload),
    }
    if provider_id == "anthropic":
        config["anthropic_transport"] = anthropic_transport
    run = AnalysisRun(
        id=new_id(),
        project_id=video.project_id,
        video_id=video.id,
        name=payload.get("name") or f"{video.original_filename} · {model_id}",
        status="pending",
        research_mode=mode,
        viewpoint=viewpoint,
        task_constraint=constraint,
        provider_id=provider_id,
        model_id=model_id,
        segmentation_mode=config["segmentation_mode"],
        segmentation_model_id=config["segmentation_model_id"],
        prompt_version_id=prompt.id if prompt else None,
        modality=modality,
        sampling_preset=config["sampling_preset"],
        analysis_pass=config["analysis_pass"],
        config_json=config,
        coverage_json={},
        parent_run_id=payload.get("parent_run_id"),
        app_version=__version__,
        git_revision=config["git_revision"],
    )
    session.add(run)
    session.flush()
    for name in PIPELINE_STAGES:
        session.add(PipelineStageRun(run_id=run.id, stage=name, status="pending", checkpoint_json={}))
    if enqueue:
        session.add(Job(run_id=run.id, max_attempts=int(config["max_retries"]) + 1))
    session.commit()
    session.refresh(run)
    return run


def duplicate_run(session: Session, run: AnalysisRun, name: str | None = None) -> AnalysisRun:
    payload = dict(run.config_json or {})
    payload.update(
        {
            "name": name or f"{run.name} copy",
            "video_id": run.video_id,
            "parent_run_id": run.id,
            "provider_id": run.provider_id,
            "model_id": run.model_id,
            "modality": run.modality,
            "research_mode": run.research_mode,
            "sampling_preset": run.sampling_preset,
        }
    )
    return create_run(session, payload)


def duplicate_visual_only(session: Session, run: AnalysisRun) -> AnalysisRun:
    payload = dict(run.config_json or {})
    payload.update(
        {
            "name": f"{run.name} · visual only",
            "video_id": run.video_id,
            "parent_run_id": run.id,
            "provider_id": run.provider_id,
            "model_id": run.model_id,
            "modality": run.modality,
            "research_mode": run.research_mode,
            "sampling_preset": run.sampling_preset,
            "transcribe": False,
            "use_transcript": False,
        }
    )
    return create_run(session, payload)


def retry_from(session: Session, run: AnalysisRun, stage_name: str) -> Job:
    if stage_name not in PIPELINE_STAGES:
        raise ValueError(f"unknown stage {stage_name}")
    index = PIPELINE_STAGES.index(stage_name)
    stages = session.scalars(select(PipelineStageRun).where(PipelineStageRun.run_id == run.id)).all()
    by_name = {stage.stage: stage for stage in stages}
    for name in PIPELINE_STAGES[index:]:
        stage = by_name.get(name)
        if stage is None:
            continue
        stage.status = "pending"
        stage.error = ""
        stage.finished_at = None
    run.status = "pending"
    run.error = ""
    run.finished_at = None
    job = Job(run_id=run.id, from_stage=stage_name, max_attempts=2)
    session.add(job)
    session.commit()
    return job


def seed_prompts(session: Session) -> None:
    for key, (name, body) in TEMPLATES.items():
        exists = session.scalar(
            select(PromptVersion).where(PromptVersion.key == key, PromptVersion.version == 1)
        )
        if exists is None:
            session.add(
                PromptVersion(
                    key=key,
                    name=name,
                    version=1,
                    body=body,
                    foundational=key == "foundational",
                )
            )
    session.commit()


def save_prompt(session: Session, key: str, name: str, body: str) -> PromptVersion:
    versions = list(
        session.scalars(select(PromptVersion.version).where(PromptVersion.key == key))
    )
    version = (max(versions) if versions else 0) + 1
    row = PromptVersion(key=key, name=name, version=version, body=body, foundational=False)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _latest_prompt(session: Session, key: str) -> PromptVersion | None:
    return session.scalar(
        select(PromptVersion)
        .where(PromptVersion.key == key)
        .order_by(PromptVersion.version.desc())
    )


def _git() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""

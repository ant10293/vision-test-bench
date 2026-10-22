"""HTTP routes."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.api.serialize import bundle, run_summary, video_dict
from app.db import get_session
from app.evaluation.compare import compare_documents
from app.evaluation.metrics import evaluate_documents
from app.models.domain import (
    ActionRow,
    AmbiguityRow,
    ComparisonRow,
    DetectedObject,
    EvaluationResult,
    GroundTruthDataset,
    ObjectImage,
    WorkflowRow,
)
from app.models.media import FrameAsset, Project, VideoAsset
from app.models.runs import (
    AnalysisRevision,
    AnalysisRun,
    Job,
    ManualModel,
    ModelRequestRow,
    ModelResponseRow,
    PromptVersion,
    ProviderStatus,
)
from app.models.util import utcnow
from app.providers.catalog import lookup, models_for
from app.providers.registry import get_provider, provider_ids
from app.providers.types import ProviderError, UnsupportedMode
from app.schemas.analysis import AnalysisDocument
from app.services.corrections import (
    add_action,
    add_dependency,
    add_object,
    add_step,
    merge_objects,
    remove_dependency,
    split_object,
    update_action,
    update_object,
    update_step,
)
from app.services.credentials import (
    delete_secret,
    load_secret,
    provider_status,
    record_validation,
    secret_hint,
    store_secret,
)
from app.services.exchange import export_run, import_run
from app.services.experiments import create_run, duplicate_run, retry_from, save_prompt
from app.services.ingest import ingest_file
from app.services.storage import data_path, resolve_stored

router = APIRouter(prefix="/api")


def db() -> Session:
    yield from get_session()


def _run(session: Session, run_id: str) -> AnalysisRun:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@router.get("/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "name": "LearnLoop Research Lab"}


@router.get("/dashboard")
def dashboard(session: Session = Depends(db)) -> dict:
    videos = session.scalar(select(func.count()).select_from(VideoAsset)) or 0
    runs = session.scalars(select(AnalysisRun)).all()
    completed = sum(1 for run in runs if run.status == "completed")
    active = sum(1 for run in runs if run.status in {"pending", "running"})
    revision_ids = [run.active_revision_id for run in runs if run.active_revision_id]
    objects = actions = ambiguities = workflows = 0
    if revision_ids:
        objects = session.scalar(
            select(func.count()).select_from(DetectedObject).where(DetectedObject.revision_id.in_(revision_ids))
        ) or 0
        actions = session.scalar(
            select(func.count()).select_from(ActionRow).where(ActionRow.revision_id.in_(revision_ids))
        ) or 0
        ambiguities = session.scalar(
            select(func.count()).select_from(AmbiguityRow).where(AmbiguityRow.revision_id.in_(revision_ids))
        ) or 0
        workflows = session.scalar(
            select(func.count()).select_from(WorkflowRow).where(WorkflowRow.revision_id.in_(revision_ids))
        ) or 0
    connected = []
    for provider_id in provider_ids():
        status = provider_status(session, provider_id)
        if status and status.last_ok:
            connected.append(provider_id)
    recent = [run_summary(session, run) for run in sorted(runs, key=lambda item: item.created_at, reverse=True)[:12]]
    return {
        "videos": videos,
        "runs_completed": completed,
        "runs_active": active,
        "objects": objects,
        "actions": actions,
        "workflows": workflows,
        "ambiguities": ambiguities,
        "providers_connected": connected,
        "input_tokens": sum(run.input_tokens for run in runs),
        "output_tokens": sum(run.output_tokens for run in runs),
        "cost_usd": round(sum(run.cost_usd for run in runs), 6),
        "recent_runs": recent,
    }


@router.get("/projects")
def list_projects(session: Session = Depends(db)) -> list[dict]:
    rows = session.scalars(select(Project).order_by(Project.created_at.desc())).all()
    return [{"id": row.id, "name": row.name, "description": row.description, "created_at": row.created_at.isoformat()} for row in rows]


@router.post("/projects")
def create_project(payload: dict, session: Session = Depends(db)) -> dict:
    row = Project(name=payload.get("name") or "Untitled study", description=payload.get("description") or "")
    session.add(row)
    session.commit()
    return {"id": row.id, "name": row.name, "description": row.description}


@router.post("/videos")
async def upload_video(
    project_id: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(db),
) -> dict:
    from app.config import settings
    from app.video.probe import VideoError

    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(413, "file exceeds the upload limit")
    if not raw:
        raise HTTPException(400, "empty upload")
    temp = data_path("imports", f"upload_{file.filename or 'video'}")
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_bytes(raw)
    try:
        video = ingest_file(session, project_id, file.filename or "video.mp4", temp)
    except VideoError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if temp.exists():
            temp.unlink()
    return video_dict(video)


@router.get("/videos")
def list_videos(project_id: str | None = None, session: Session = Depends(db)) -> list[dict]:
    query = select(VideoAsset).order_by(VideoAsset.created_at.desc())
    if project_id:
        query = query.where(VideoAsset.project_id == project_id)
    return [video_dict(video) for video in session.scalars(query)]


@router.get("/videos/{video_id}")
def get_video(video_id: str, session: Session = Depends(db)) -> dict:
    video = session.get(VideoAsset, video_id)
    if video is None:
        raise HTTPException(404, "video not found")
    frames = session.scalars(
        select(FrameAsset).where(FrameAsset.video_id == video.id, FrameAsset.run_id.is_(None)).order_by(FrameAsset.timestamp)
    ).all()
    payload = video_dict(video, len(frames))
    payload["frames"] = [
        {"id": frame.id, "timestamp": frame.timestamp, "reason": frame.reason, "url": f"/api/media/frames/{frame.id}"}
        for frame in frames
    ]
    return payload


@router.delete("/videos/{video_id}")
def delete_video(video_id: str, session: Session = Depends(db)) -> dict:
    video = session.get(VideoAsset, video_id)
    if video is None:
        raise HTTPException(404, "video not found")
    used = session.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.video_id == video.id))
    if used:
        raise HTTPException(409, "delete the runs that use this video first")
    for stored in (video.stored_path, video.proxy_path, video.thumbnail_path, video.contact_sheet_path):
        if stored:
            path = resolve_stored(stored)
            if path.exists() and path.is_file():
                path.unlink()
    session.delete(video)
    session.commit()
    return {"deleted": video_id}


@router.get("/media/videos/{video_id}/{kind}")
def media_video(video_id: str, kind: str, session: Session = Depends(db)) -> FileResponse:
    video = session.get(VideoAsset, video_id)
    if video is None:
        raise HTTPException(404, "video not found")
    stored = {
        "playback": video.proxy_path or video.stored_path,
        "original": video.stored_path,
        "thumbnail": video.thumbnail_path,
        "contact-sheet": video.contact_sheet_path,
    }.get(kind)
    if not stored:
        raise HTTPException(404, "media not found")
    path = resolve_stored(stored)
    if not path.exists():
        raise HTTPException(404, "file missing on disk")
    return FileResponse(path)


@router.get("/media/frames/{frame_id}")
def media_frame(frame_id: str, session: Session = Depends(db)) -> FileResponse:
    frame = session.get(FrameAsset, frame_id)
    if frame is None:
        raise HTTPException(404, "frame not found")
    path = resolve_stored(frame.path)
    return FileResponse(path)


@router.get("/media/object-images/{image_id}")
def media_object_image(image_id: str, session: Session = Depends(db)) -> FileResponse:
    image = session.get(ObjectImage, image_id)
    if image is None or not image.path:
        raise HTTPException(404, "image not found")
    return FileResponse(resolve_stored(image.path))


@router.get("/prompts")
def list_prompts(session: Session = Depends(db)) -> list[dict]:
    rows = session.scalars(select(PromptVersion).order_by(PromptVersion.key, PromptVersion.version.desc())).all()
    return [
        {
            "id": row.id,
            "key": row.key,
            "name": row.name,
            "version": row.version,
            "body": row.body,
            "foundational": row.foundational,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/prompts")
def create_prompt(payload: dict, session: Session = Depends(db)) -> dict:
    row = save_prompt(session, payload["key"], payload.get("name") or payload["key"], payload["body"])
    return {"id": row.id, "key": row.key, "version": row.version}


@router.get("/providers")
def list_providers(session: Session = Depends(db)) -> list[dict]:
    payload = []
    for provider_id in provider_ids():
        configured, source, hint = secret_hint(session, provider_id) if provider_id not in {"deterministic", "fixture", "manual_json"} else (True, "local", "")
        status = provider_status(session, provider_id)
        payload.append(
            {
                "id": provider_id,
                "configured": configured,
                "secret_source": source,
                "hint": hint,
                "validated": bool(status and status.last_ok),
                "last_error": "" if not status else status.last_error,
                "last_validated_at": status.last_validated_at.isoformat() if status and status.last_validated_at else None,
                "base_url": status.extra_base_url if status else "",
                "models": [caps.as_dict() for caps in models_for(provider_id)],
            }
        )
    return payload


@router.post("/providers/{provider_id}/credentials")
def put_credentials(provider_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    if provider_id in {"deterministic", "fixture", "manual_json"}:
        raise HTTPException(400, "this provider does not use an API key")
    secret = payload.get("api_key") or ""
    if not secret:
        raise HTTPException(400, "api_key is required")
    store_secret(session, provider_id, secret)
    if payload.get("base_url"):
        status = session.get(ProviderStatus, provider_id) or ProviderStatus(provider_id=provider_id)
        status.extra_base_url = payload["base_url"]
        session.merge(status)
        session.commit()
    return {"configured": True, "provider_id": provider_id}


@router.delete("/providers/{provider_id}/credentials")
def remove_credentials(provider_id: str, session: Session = Depends(db)) -> dict:
    delete_secret(session, provider_id)
    return {"configured": False}


@router.post("/providers/{provider_id}/validate")
async def validate_provider(provider_id: str, session: Session = Depends(db)) -> dict:
    if provider_id in {"deterministic", "fixture", "manual_json"}:
        record_validation(session, provider_id, True, "")
        return {"ok": True, "provider_id": provider_id, "detail": "local provider"}
    secret, source = load_secret(session, provider_id)
    if not secret:
        record_validation(session, provider_id, False, "no credentials configured")
        raise HTTPException(400, "no credentials configured")
    try:
        await get_provider(provider_id, session).validate(secret)
    except Exception as exc:
        record_validation(session, provider_id, False, str(exc))
        raise HTTPException(502, f"validation failed: {exc}") from exc
    record_validation(session, provider_id, True, "")
    return {"ok": True, "provider_id": provider_id, "source": source}


@router.get("/providers/{provider_id}/models")
async def discover_models(provider_id: str, session: Session = Depends(db)) -> dict:
    catalog = [caps.as_dict() for caps in models_for(provider_id)]
    manual = session.scalars(select(ManualModel).where(ManualModel.provider_id == provider_id)).all()
    for row in manual:
        catalog.append(lookup(provider_id, row.model_id).as_dict())
    discovered: list[str] = []
    error = ""
    secret, _source = load_secret(session, provider_id)
    if secret and provider_id not in {"deterministic", "fixture", "manual_json"}:
        try:
            discovered = await get_provider(provider_id, session).list_models(secret)
        except Exception as exc:
            error = str(exc)
    return {"catalog": catalog, "discovered": discovered, "error": error}


@router.post("/providers/{provider_id}/models")
def add_model(provider_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    model_id = payload.get("model_id")
    if not model_id:
        raise HTTPException(400, "model_id is required")
    row = ManualModel(provider_id=provider_id, model_id=model_id, notes=payload.get("notes") or "")
    session.add(row)
    session.commit()
    return lookup(provider_id, model_id).as_dict()


@router.post("/runs")
def post_run(payload: dict, session: Session = Depends(db)) -> dict:
    try:
        run = create_run(session, payload)
    except (UnsupportedMode, ValueError, ProviderError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return run_summary(session, run)


@router.get("/runs")
def list_runs(
    project_id: str | None = None,
    status: str | None = None,
    provider_id: str | None = None,
    session: Session = Depends(db),
) -> list[dict]:
    query = select(AnalysisRun).order_by(AnalysisRun.created_at.desc())
    if project_id:
        query = query.where(AnalysisRun.project_id == project_id)
    if status:
        query = query.where(AnalysisRun.status == status)
    if provider_id:
        query = query.where(AnalysisRun.provider_id == provider_id)
    return [run_summary(session, run) for run in session.scalars(query)]


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(db)) -> dict:
    return bundle(session, _run(session, run_id))


@router.post("/runs/{run_id}/duplicate")
def duplicate(run_id: str, payload: dict | None = None, session: Session = Depends(db)) -> dict:
    run = duplicate_run(session, _run(session, run_id), (payload or {}).get("name"))
    return run_summary(session, run)


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, session: Session = Depends(db)) -> dict:
    run = _run(session, run_id)
    run.status = "canceled"
    run.finished_at = utcnow()
    for job in session.scalars(select(Job).where(Job.run_id == run.id, Job.status.in_(["pending", "running"]))):
        job.status = "canceled"
        job.finished_at = utcnow()
    session.commit()
    return run_summary(session, run)


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, session: Session = Depends(db)) -> dict:
    run = _run(session, run_id)
    folder = data_path("runs", run.id)
    session.delete(run)
    session.commit()
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    return {"deleted": run_id}


@router.post("/runs/{run_id}/retry/{stage}")
def retry(run_id: str, stage: str, session: Session = Depends(db)) -> dict:
    try:
        retry_from(session, _run(session, run_id), stage)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return run_summary(session, _run(session, run_id))


@router.get("/runs/{run_id}/requests")
def run_requests(run_id: str, session: Session = Depends(db)) -> list[dict]:
    _run(session, run_id)
    rows = session.scalars(select(ModelRequestRow).where(ModelRequestRow.run_id == run_id)).all()
    payload = []
    for row in rows:
        response = session.scalar(select(ModelResponseRow).where(ModelResponseRow.request_id == row.id))
        payload.append(
            {
                "id": row.id,
                "stage": row.stage,
                "provider_id": row.provider_id,
                "model_id": row.model_id,
                "modality": row.modality,
                "parameters": row.parameters_json,
                "frame_timestamps": row.frame_timestamps,
                "provider_request_id": row.provider_request_id,
                "status": row.status,
                "error": row.error,
                "latency_ms": row.latency_ms,
                "response_text": response.text if response else "",
                "usage": {
                    "input_tokens": response.input_tokens if response else 0,
                    "output_tokens": response.output_tokens if response else 0,
                    "cost_usd": response.cost_usd if response else 0,
                },
            }
        )
    return payload


@router.get("/runs/{run_id}/scene")
def scene_at(run_id: str, t: float, session: Session = Depends(db)) -> dict:
    data = bundle(session, _run(session, run_id))
    states = [
        item
        for item in data["states"]
        if item["time_start"] - 1e-6 <= t <= item["time_end"] + 1e-6
    ]
    relationships = [
        item
        for item in data["relationships"]
        if item["time_start"] - 1e-6 <= t <= item["time_end"] + 1e-6
    ]
    actions = [item for item in data["actions"] if item["start"] - 1e-6 <= t <= item["end"] + 1e-6]
    ambiguities = [
        item
        for item in data["ambiguities"]
        if item["time_start"] is not None
        and item["time_start"] - 1e-6 <= t <= (item["time_end"] if item["time_end"] is not None else item["time_start"]) + 1e-6
    ]
    observations = []
    for obj in data["objects"]:
        samples = [item for item in data["observations"] if item["object_local_id"] == obj["local_id"]]
        if not samples:
            continue
        nearest = min(samples, key=lambda item: abs(item["timestamp"] - t))
        gap = abs(nearest["timestamp"] - t)
        if gap > 0.75:
            observations.append(
                {
                    "object_local_id": obj["local_id"],
                    "coverage": "gap",
                    "nearest_timestamp": nearest["timestamp"],
                    "note": "No observation covers this instant. The last box was not extrapolated.",
                }
            )
        else:
            observations.append({**nearest, "coverage": "sample" if gap < 0.05 else "nearest_sample", "gap_sec": gap})
    return {
        "t": t,
        "states": states,
        "relationships": relationships,
        "actions": actions,
        "ambiguities": ambiguities,
        "observations": observations,
    }


def _edit(session: Session, run_id: str, fn):
    try:
        fn(session, _run(session, run_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return bundle(session, _run(session, run_id))


@router.patch("/runs/{run_id}/objects/{local_id}")
def patch_object(run_id: str, local_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    return _edit(session, run_id, lambda db, run: update_object(db, run, local_id, payload))


@router.post("/runs/{run_id}/objects")
def post_object(run_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    return _edit(session, run_id, lambda db, run: add_object(db, run, payload.get("label") or "Untitled object", payload.get("description") or ""))


@router.post("/runs/{run_id}/objects/merge")
def post_merge(run_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    return _edit(session, run_id, lambda db, run: merge_objects(db, run, payload["keep_id"], payload["drop_id"]))


@router.post("/runs/{run_id}/objects/{local_id}/split")
def post_split(run_id: str, local_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    return _edit(
        session,
        run_id,
        lambda db, run: split_object(db, run, local_id, payload.get("timestamps") or [], payload.get("label") or ""),
    )


@router.patch("/runs/{run_id}/actions/{local_id}")
def patch_action(run_id: str, local_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    return _edit(session, run_id, lambda db, run: update_action(db, run, local_id, payload))


@router.post("/runs/{run_id}/actions")
def post_action(run_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    return _edit(session, run_id, lambda db, run: add_action(db, run, payload))


@router.patch("/runs/{run_id}/steps/{local_id}")
def patch_step(run_id: str, local_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    return _edit(session, run_id, lambda db, run: update_step(db, run, local_id, payload))


@router.post("/runs/{run_id}/steps")
def post_step(run_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    return _edit(session, run_id, lambda db, run: add_step(db, run, payload.get("name") or "Untitled step"))


@router.post("/runs/{run_id}/dependencies")
def post_dependency(run_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    return _edit(
        session,
        run_id,
        lambda db, run: add_dependency(
            db, run, payload["from_step"], payload["to_step"], payload.get("category") or "unknown", payload.get("rationale") or ""
        ),
    )


@router.delete("/runs/{run_id}/dependencies")
def delete_dependency(run_id: str, from_step: str, to_step: str, session: Session = Depends(db)) -> dict:
    return _edit(session, run_id, lambda db, run: remove_dependency(db, run, from_step, to_step))


@router.post("/runs/{run_id}/ground-truth")
def promote_ground_truth(run_id: str, payload: dict | None = None, session: Session = Depends(db)) -> dict:
    run = _run(session, run_id)
    revision = session.get(AnalysisRevision, run.active_revision_id) if run.active_revision_id else None
    if revision is None:
        raise HTTPException(400, "nothing to promote")
    row = GroundTruthDataset(
        run_id=run.id,
        revision_id=revision.id,
        name=(payload or {}).get("name") or f"Ground truth · {run.name}",
        annotator=(payload or {}).get("annotator") or "researcher",
        research_mode=run.research_mode,
        document_json=revision.document_json,
    )
    session.add(row)
    session.commit()
    return {"id": row.id, "name": row.name, "revision_id": revision.id}


@router.get("/runs/{run_id}/ground-truth")
def list_ground_truth(run_id: str, session: Session = Depends(db)) -> list[dict]:
    rows = session.scalars(select(GroundTruthDataset).where(GroundTruthDataset.run_id == run_id)).all()
    return [{"id": row.id, "name": row.name, "annotator": row.annotator, "created_at": row.created_at.isoformat()} for row in rows]


@router.post("/runs/{run_id}/evaluations")
def evaluate(run_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    run = _run(session, run_id)
    truth = session.get(GroundTruthDataset, payload["ground_truth_id"])
    if truth is None:
        raise HTTPException(404, "ground truth not found")
    revision = session.get(AnalysisRevision, payload.get("revision_id") or run.active_revision_id)
    if revision is None:
        raise HTTPException(400, "no predicted revision")
    metrics = evaluate_documents(revision.document_json, truth.document_json, payload.get("tolerances"))
    row = EvaluationResult(
        run_id=run.id,
        ground_truth_id=truth.id,
        predicted_revision_id=revision.id,
        tolerances_json=payload.get("tolerances") or {},
        metrics_json=metrics,
        errors_json=metrics.get("errors") or [],
    )
    session.add(row)
    session.commit()
    return {"id": row.id, "metrics": metrics}


@router.get("/runs/{run_id}/evaluations")
def list_evaluations(run_id: str, session: Session = Depends(db)) -> list[dict]:
    rows = session.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id)).all()
    return [{"id": row.id, "metrics": row.metrics_json, "created_at": row.created_at.isoformat()} for row in rows]


@router.post("/comparisons")
def create_comparison(payload: dict, session: Session = Depends(db)) -> dict:
    left = _run(session, payload["left_run_id"])
    right = _run(session, payload["right_run_id"])
    left_rev = session.get(AnalysisRevision, left.active_revision_id) if left.active_revision_id else None
    right_rev = session.get(AnalysisRevision, right.active_revision_id) if right.active_revision_id else None
    if left_rev is None or right_rev is None:
        raise HTTPException(400, "both runs need an analysis")
    report = compare_documents(
        AnalysisDocument.model_validate(left_rev.document_json),
        AnalysisDocument.model_validate(right_rev.document_json),
        payload.get("object_matches"),
    )
    row = ComparisonRow(
        name=payload.get("name") or "Comparison",
        left_run_id=left.id,
        right_run_id=right.id,
        object_matches_json=report["object_matches"],
        report_json=report,
    )
    session.add(row)
    session.commit()
    return {"id": row.id, "report": report}


@router.get("/comparisons/{comparison_id}")
def get_comparison(comparison_id: str, session: Session = Depends(db)) -> dict:
    row = session.get(ComparisonRow, comparison_id)
    if row is None:
        raise HTTPException(404, "comparison not found")
    return {
        "id": row.id,
        "name": row.name,
        "left_run_id": row.left_run_id,
        "right_run_id": row.right_run_id,
        "report": row.report_json,
    }


@router.post("/comparisons/{comparison_id}/matches")
def set_matches(comparison_id: str, payload: dict, session: Session = Depends(db)) -> dict:
    row = session.get(ComparisonRow, comparison_id)
    if row is None:
        raise HTTPException(404, "comparison not found")
    left = _run(session, row.left_run_id)
    right = _run(session, row.right_run_id)
    report = compare_documents(
        AnalysisDocument.model_validate(session.get(AnalysisRevision, left.active_revision_id).document_json),
        AnalysisDocument.model_validate(session.get(AnalysisRevision, right.active_revision_id).document_json),
        payload.get("object_matches") or [],
    )
    row.object_matches_json = report["object_matches"]
    row.report_json = report
    session.commit()
    return {"id": row.id, "report": report}


@router.get("/runs/{run_id}/export")
def download_export(run_id: str, session: Session = Depends(db)) -> FileResponse:
    run = _run(session, run_id)
    dest = data_path("exports", f"{run.id}.zip")
    export_run(session, run, dest)
    return FileResponse(dest, filename=f"learnloop-{run.id}.zip")


@router.post("/imports")
async def import_archive(
    project_id: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(db),
) -> dict:
    dest = data_path("imports", file.filename or "experiment.zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    try:
        run = import_run(session, project_id, dest)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return run_summary(session, run)


@router.get("/runs/{run_id}/analysis.json")
def analysis_json(run_id: str, session: Session = Depends(db)) -> Response:
    run = _run(session, run_id)
    revision = session.get(AnalysisRevision, run.active_revision_id) if run.active_revision_id else None
    if revision is None:
        raise HTTPException(404, "no analysis")
    return Response(content=json_dumps(revision.document_json), media_type="application/json")


def json_dumps(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2)

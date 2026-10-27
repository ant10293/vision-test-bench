"""Scene simulation API. Ground truth is not part of the analysis request."""

from __future__ import annotations

import json
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import run_summary
from app.db import get_session as db
from app.models.runs import AnalysisRun
from app.models.simulation import SimulationDataset, SimulationEvaluation, SimulationJob
from app.services.experiments import create_run
from app.services.storage import data_path
from app.simulation.evaluate import evaluate_scene
from app.simulation.presets import CAMERA_MODES, VARIANTS
from app.simulation.render import find_blender
from app.simulation.service import (
    QUALITY_SAMPLES,
    SimulationError,
    dataset_dir,
    delete_dataset,
    enqueue,
    evaluate_run,
    load_scene,
    playback_request,
    summary,
)

router = APIRouter(prefix="/api", tags=["simulation"])


class SimulationRequest(BaseModel):
    variant: str = "correct"
    camera_mode: str = "fixed"
    seed: int = 1
    width: int = Field(default=320, ge=160, le=1280)
    height: int = Field(default=240, ge=120, le=720)
    fps: int = Field(default=8, ge=2, le=24)
    duration: float = Field(default=6, gt=0, le=12)
    quality: str = "preview"
    randomize: bool = False
    pair_key: str = ""
    name: str = ""


class PairRequest(SimulationRequest):
    variants: list[str] = Field(default_factory=lambda: ["correct", "missing_washer"])


class AnalyzeRequest(BaseModel):
    perception_preset: str = "B_temporal"
    provider_id: str = "deterministic"
    model_id: str = "motion-baseline"


class EvaluateRequest(BaseModel):
    run_id: str


def _dataset(session: Session, dataset_id: str) -> SimulationDataset:
    dataset = session.get(SimulationDataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "simulation dataset not found")
    return dataset


@router.get("/simulations/presets")
def presets() -> dict:
    return {
        "presets": ["assembly"],
        "variants": list(VARIANTS),
        "camera_modes": list(CAMERA_MODES),
        "qualities": list(QUALITY_SAMPLES),
        "blender_available": find_blender() is not None,
        "kinematic": True,
        "rigid_body": False,
        "note": "Variants share a pair key when the seed and camera match. Only the named intervention changes.",
    }


@router.post("/simulations")
def create_simulation(payload: SimulationRequest, session: Session = Depends(db)) -> dict:
    try:
        dataset, _job = enqueue(session, **payload.model_dump())
    except SimulationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return summary(session, dataset)


@router.post("/simulations/pair")
def create_pair(payload: PairRequest, session: Session = Depends(db)) -> dict:
    if len(payload.variants) < 2:
        raise HTTPException(400, "a pair needs two variants")
    shared = payload.pair_key or (
        f"assembly:{payload.seed}:{payload.camera_mode}:{payload.width}x{payload.height}:"
        f"{payload.fps}:{payload.duration}:{int(payload.randomize)}"
    )
    created = []
    for variant in payload.variants:
        body = payload.model_dump()
        body.pop("variants")
        body["variant"] = variant
        body["pair_key"] = shared
        try:
            dataset, _job = enqueue(session, **body)
        except SimulationError as exc:
            raise HTTPException(400, str(exc)) from exc
        created.append(summary(session, dataset))
    return {"pair_key": shared, "datasets": created}


@router.get("/simulations")
def list_simulations(session: Session = Depends(db)) -> list[dict]:
    rows = session.scalars(select(SimulationDataset).order_by(SimulationDataset.created_at.desc())).all()
    return [summary(session, row) for row in rows]


@router.get("/simulations/{dataset_id}")
def get_simulation(dataset_id: str, session: Session = Depends(db)) -> dict:
    return summary(session, _dataset(session, dataset_id))


@router.delete("/simulations/{dataset_id}")
def remove_simulation(dataset_id: str, session: Session = Depends(db)) -> dict:
    dataset = _dataset(session, dataset_id)
    delete_dataset(session, dataset)
    return {"deleted": dataset_id, "video_kept": True}


@router.post("/simulations/{dataset_id}/generate")
def retry_simulation(dataset_id: str, session: Session = Depends(db)) -> dict:
    dataset = _dataset(session, dataset_id)
    if dataset.status not in {"failed", "canceled"}:
        raise HTTPException(400, "only a failed or cancelled dataset can be rendered again")
    job = SimulationJob(dataset_id=dataset.id)
    dataset.status = "pending"
    dataset.error = ""
    session.add(job)
    session.commit()
    return summary(session, dataset)


@router.get("/simulations/jobs/{job_id}")
def get_job(job_id: str, session: Session = Depends(db)) -> dict:
    job = session.get(SimulationJob, job_id)
    if job is None:
        raise HTTPException(404, "simulation job not found")
    dataset = session.get(SimulationDataset, job.dataset_id)
    return summary(session, dataset) if dataset is not None else {"id": job.id, "status": job.status}


@router.post("/simulations/jobs/{job_id}/cancel")
def cancel_job(job_id: str, session: Session = Depends(db)) -> dict:
    job = session.get(SimulationJob, job_id)
    if job is None:
        raise HTTPException(404, "simulation job not found")
    if job.status in {"completed", "failed", "canceled"}:
        return {"id": job.id, "status": job.status}
    job.cancel_requested = True
    cancel = dataset_dir(job.dataset_id) / "logs" / "cancel"
    cancel.parent.mkdir(parents=True, exist_ok=True)
    cancel.write_text("1")
    session.commit()
    return {"id": job.id, "status": job.status, "cancel_requested": True}


@router.get("/simulations/{dataset_id}/frames/{index}")
def frame_image(dataset_id: str, index: int, kind: str = "rgb", session: Session = Depends(db)) -> FileResponse:
    dataset = _dataset(session, dataset_id)
    if dataset.status != "completed":
        raise HTTPException(404, "frames are published when the render completes")
    if kind not in {"rgb", "instance"}:
        raise HTTPException(400, "kind is rgb or instance")
    path = dataset_dir(dataset.id) / "frames" / kind / f"{index:06d}.png"
    if not path.exists():
        raise HTTPException(404, "frame not found")
    return FileResponse(path)


@router.get("/simulations/{dataset_id}/ground-truth")
def ground_truth(dataset_id: str, session: Session = Depends(db)) -> dict:
    dataset = _dataset(session, dataset_id)
    if dataset.status != "completed":
        raise HTTPException(404, "ground truth is published when the render completes")
    root = dataset_dir(dataset.id)
    return {
        "label": "simulation ground truth",
        "scene": json.loads((root / "scene.json").read_text()),
        "objects": json.loads((root / "annotations" / "objects.json").read_text()),
        "frames": json.loads((root / "annotations" / "frames.json").read_text()),
        "events": json.loads((root / "annotations" / "events.json").read_text()),
        "relationships": json.loads((root / "annotations" / "relationships.json").read_text()),
        "workflow": json.loads((root / "annotations" / "workflow.json").read_text()),
        "observability": json.loads((root / "annotations" / "observability.json").read_text()),
        "manifest": json.loads((root / "manifest.json").read_text()),
    }


@router.get("/simulations/{dataset_id}/state")
def scene_state(dataset_id: str, timestamp: float = 0, session: Session = Depends(db)) -> dict:
    dataset = _dataset(session, dataset_id)
    try:
        scene = load_scene(dataset)
        return evaluate_scene(scene, timestamp)
    except (SimulationError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/simulations/{dataset_id}/analyze")
def analyze(dataset_id: str, payload: AnalyzeRequest, session: Session = Depends(db)) -> dict:
    dataset = _dataset(session, dataset_id)
    if payload.provider_id == "fixture":
        raise HTTPException(400, "Benchmark playback does not use the authored fixture provider.")
    try:
        request = playback_request(dataset, payload.perception_preset)
    except SimulationError as exc:
        raise HTTPException(400, str(exc)) from exc
    request["provider_id"] = payload.provider_id
    request["model_id"] = payload.model_id
    try:
        run = create_run(session, request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return run_summary(session, run)


@router.post("/simulations/{dataset_id}/evaluate")
def evaluate(dataset_id: str, payload: EvaluateRequest, session: Session = Depends(db)) -> dict:
    dataset = _dataset(session, dataset_id)
    run = session.get(AnalysisRun, payload.run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    try:
        row = evaluate_run(session, dataset, run)
    except (SimulationError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": row.id, "run_id": row.run_id, "perception_preset": row.perception_preset, "result": row.result_json}


@router.get("/simulations/{dataset_id}/evaluations")
def evaluations(dataset_id: str, session: Session = Depends(db)) -> list[dict]:
    _dataset(session, dataset_id)
    rows = session.scalars(
        select(SimulationEvaluation)
        .where(SimulationEvaluation.dataset_id == dataset_id)
        .order_by(SimulationEvaluation.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "perception_preset": row.perception_preset,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "result": row.result_json,
        }
        for row in rows
    ]


@router.get("/simulations/{dataset_id}/export")
def export_dataset(dataset_id: str, session: Session = Depends(db)) -> FileResponse:
    dataset = _dataset(session, dataset_id)
    if dataset.status != "completed":
        raise HTTPException(400, "only a completed dataset can be exported")
    root = dataset_dir(dataset.id)
    dest = data_path("exports", f"simulation-{dataset.id}.zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = session.scalars(select(SimulationEvaluation).where(SimulationEvaluation.dataset_id == dataset.id)).all()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith("logs/"):
                continue
            archive.write(path, rel)
        archive.writestr(
            "evaluation/results.json",
            json.dumps(
                [
                    {"run_id": row.run_id, "perception_preset": row.perception_preset, "result": row.result_json}
                    for row in rows
                ],
                indent=2,
            ),
        )
    return FileResponse(dest, filename=f"simulation-{dataset.id}.zip")


@router.get("/runs/{run_id}/scene-graph")
def run_scene_graph(run_id: str, session: Session = Depends(db)) -> dict:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    path = data_path("runs", run.id, "scene_graph.json")
    if not path.exists():
        raise HTTPException(404, "this run has no scene graph")
    payload = json.loads(path.read_text())
    payload["world_coordinates_supported"] = False
    return payload

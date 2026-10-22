"""End-to-end local pipeline: synthetic video, fixture provider, corrections, export."""

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("LEARNLOOP_WORKER_ENABLED", "0")
os.environ.setdefault("LEARNLOOP_AUTO_SEED", "0")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNLOOP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEARNLOOP_DATABASE_URL", f"sqlite:///{tmp_path}/lab.db")
    monkeypatch.setenv("LEARNLOOP_WORKER_ENABLED", "0")
    monkeypatch.setenv("LEARNLOOP_AUTO_SEED", "0")
    from app.config import settings
    from app.db import rebind_engine

    settings.data_dir = tmp_path
    settings.database_url = f"sqlite:///{tmp_path}/lab.db"
    settings.worker_enabled = False
    settings.auto_seed = False
    rebind_engine(settings.sqlalchemy_url)
    from app.main import create_app, init_database

    init_database()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _run_job(run_id: str) -> None:
    from app.db import SessionLocal
    from app.models.runs import Job
    from app.pipeline.runner import execute_job
    from sqlalchemy import select

    session = SessionLocal()
    job = session.scalar(select(Job).where(Job.run_id == run_id, Job.status == "pending"))
    assert job is not None
    job.status = "running"
    session.commit()
    asyncio.run(execute_job(session, job))
    session.close()


def test_fixture_pipeline_objects_actions_and_correction(client, tmp_path: Path):
    from app.demo.encode import write_clip
    from app.demo.scenarios import SCENARIOS, render_scenario
    from app.pipeline.infer import register_fixture

    project = client.post("/api/projects", json={"name": "Bench"}).json()
    scenario = SCENARIOS["fixed_camera"]()
    clip = tmp_path / "fixed.mp4"
    write_clip(render_scenario(scenario), clip)
    with clip.open("rb") as handle:
        uploaded = client.post(
            "/api/videos",
            data={"project_id": project["id"]},
            files={"file": ("fixed.mp4", handle, "video/mp4")},
        )
    assert uploaded.status_code == 200, uploaded.text
    video = uploaded.json()
    assert video["duration_sec"] > 1
    detail = client.get(f"/api/videos/{video['id']}").json()
    assert detail["frames"]
    register_fixture(video["checksum"], "fixed_camera")
    created = client.post(
        "/api/runs",
        json={
            "name": "Fixture fixed",
            "video_id": video["id"],
            "provider_id": "fixture",
            "model_id": "authored-fixture",
            "modality": "sampled_frames",
            "research_mode": "fixed_camera",
            "sampling_preset": "detailed",
            "analysis_pass": "single",
            "segmentation_mode": "boxes_only",
            "max_frames": 20,
        },
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    _run_job(run_id)
    bundle = client.get(f"/api/runs/{run_id}").json()
    assert bundle["run"]["status"] == "completed"
    assert bundle["run"]["fixture"] is True
    assert len(bundle["objects"]) >= 4
    assert len(bundle["actions"]) >= 4
    assert any(item["outcome"] == "attempted" for item in bundle["actions"])
    assert bundle["coverage"]["modality"] == "sampled_frames"
    assert bundle["coverage"]["frames_extracted"] > 0
    assert any(image["url"] for obj in bundle["objects"] for image in obj["images"])
    renamed = client.patch(
        f"/api/runs/{run_id}/objects/Object_1",
        json={"label": "Researcher label"},
    )
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["revision"]["layer"] == "human"
    labels = {obj["local_id"]: obj["label"] for obj in body["objects"]}
    assert labels["Object_1"] == "Researcher label"
    exported = client.get(f"/api/runs/{run_id}/export")
    assert exported.status_code == 200
    assert exported.content[:2] == b"PK"


def test_native_video_rejected_for_openai(client):
    project = client.post("/api/projects", json={"name": "Mode"}).json()
    from app.demo.encode import write_clip
    from app.demo.scenarios import SCENARIOS, render_scenario

    scenario = SCENARIOS["fixed_camera"]()
    path = Path(os.environ["LEARNLOOP_DATA_DIR"]) / "clip.mp4"
    write_clip(render_scenario(scenario)[:5], path)
    with path.open("rb") as handle:
        video = client.post(
            "/api/videos",
            data={"project_id": project["id"]},
            files={"file": ("clip.mp4", handle, "video/mp4")},
        ).json()
    refused = client.post(
        "/api/runs",
        json={
            "name": "should fail",
            "video_id": video["id"],
            "provider_id": "openai",
            "model_id": "gpt-4.1",
            "modality": "native_video",
            "research_mode": "fixed_camera",
        },
    )
    assert refused.status_code == 400
    assert "native video" in refused.json()["detail"].lower()


def test_job_restart_requeues_running_work(client):
    from app.db import SessionLocal
    from app.jobs.worker import recover_incomplete
    from app.models.runs import AnalysisRun, Job, PipelineStageRun
    from app.models.util import new_id

    session = SessionLocal()
    from app.models.media import Project, VideoAsset

    project = Project(name="recovery")
    session.add(project)
    session.flush()
    video = VideoAsset(
        project_id=project.id,
        original_filename="x.mp4",
        stored_path="originals/x.mp4",
        checksum_sha256="abc",
        duration_sec=1,
        width=64,
        height=64,
        display_width=64,
        display_height=64,
    )
    session.add(video)
    session.flush()
    run = AnalysisRun(
        project_id=project.id,
        video_id=video.id,
        name="stuck",
        status="running",
        research_mode="fixed_camera",
        viewpoint="fixed",
        task_constraint="constrained",
        provider_id="fixture",
        model_id="authored-fixture",
        modality="sampled_frames",
        sampling_preset="standard",
        analysis_pass="single",
        config_json={},
    )
    session.add(run)
    session.flush()
    stage = PipelineStageRun(run_id=run.id, stage="scene_analysis", status="running")
    job = Job(id=new_id(), run_id=run.id, status="running", attempt=1, max_attempts=3, worker_id="dead")
    session.add(stage)
    session.add(job)
    session.commit()
    recovered = recover_incomplete()
    assert recovered == 1
    session.expire_all()
    assert session.get(Job, job.id).status == "pending"
    assert session.get(PipelineStageRun, stage.id).status == "pending"
    session.close()

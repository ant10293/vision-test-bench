"""A rendered clip goes through the normal analysis path and is scored afterwards."""

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
    settings.ingest_inline = True
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


def test_an_experiment_requires_the_selected_video(client: TestClient):
    missing = client.post(
        "/api/runs",
        json={
            "provider_id": "deterministic",
            "model_id": "motion-baseline",
            "research_mode": "constrained_head_mounted",
            "scene": {"skip": False, "variant": "missing_washer"},
        },
    )
    assert missing.status_code == 400
    assert "rendering" not in missing.json()
    assert "simulation_id" not in missing.json()


def test_rendered_clip_is_scored_without_giving_the_run_the_scene(client: TestClient, tmp_path: Path):
    from app.simulation.render import find_blender
    from app.simulation.service import execute_simulation

    if find_blender() is None:
        pytest.skip("Blender is not installed")
    created = client.post(
        "/api/simulations/pair",
        json={
            "variants": ["correct", "missing_washer"],
            "camera_mode": "fixed",
            "seed": 3,
            "width": 160,
            "height": 120,
            "fps": 4,
            "duration": 2,
            "quality": "preview",
            "randomize": False,
        },
    )
    assert created.status_code == 200, created.text
    pair = created.json()
    assert pair["datasets"][0]["pair_key"] == pair["datasets"][1]["pair_key"]
    for item in pair["datasets"]:
        execute_simulation(item["job"]["id"])
    rows = {item["variant"]: client.get(f"/api/simulations/{item['id']}").json() for item in pair["datasets"]}
    assert rows["correct"]["status"] == "completed", rows["correct"]["error"]
    assert rows["missing_washer"]["status"] == "completed", rows["missing_washer"]["error"]

    correct = client.get(f"/api/simulations/{rows['correct']['id']}/ground-truth").json()
    missing = client.get(f"/api/simulations/{rows['missing_washer']['id']}/ground-truth").json()
    correct_washer = next(item for item in correct["observability"]["conditions"] if item["constraint_id"] == "washer_on_bolt")
    missing_washer = next(item for item in missing["observability"]["conditions"] if item["constraint_id"] == "washer_on_bolt")
    assert correct_washer["satisfied"] is True
    assert missing_washer["satisfied"] is False
    assert missing_washer["subject_visibility"] == "absent"
    assert "absent" not in correct_washer["subject_visibility"]

    rejected = client.post(
        f"/api/simulations/{rows['correct']['id']}/analyze",
        json={"provider_id": "fixture", "model_id": "authored-fixture", "perception_preset": "B_temporal"},
    )
    assert rejected.status_code == 400

    started = client.post(
        f"/api/simulations/{rows['correct']['id']}/analyze",
        json={"provider_id": "deterministic", "model_id": "motion-baseline", "perception_preset": "A_legacy_motion"},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    _run_job(run_id)
    bundle = client.get(f"/api/runs/{run_id}").json()
    assert bundle["run"]["status"] == "completed", bundle["run"]
    assert bundle["run"]["provider_id"] == "deterministic"
    assert bundle["run"]["fixture"] is False
    config = str(bundle["config"])
    for marker in ("ground_truth", "scene.json", "simulator", "inserted_into"):
        assert marker not in config

    graph = client.get(f"/api/runs/{run_id}/scene-graph")
    assert graph.status_code == 200, graph.text
    assert graph.json()["world_coordinates_supported"] is False
    assert graph.json()["metric_scale_known"] is False

    scored = client.post(f"/api/simulations/{rows['correct']['id']}/evaluate", json={"run_id": run_id})
    assert scored.status_code == 200, scored.text
    result = scored.json()["result"]
    assert result["is_a_detection_claim"] is False
    assert result["camera_pose"]["status"] == "NOT_EVALUATED"
    assert result["masks"]["status"] == "NOT_EVALUATED"
    assert result["outcomes"]["note"]

    exported = client.get(f"/api/simulations/{rows['correct']['id']}/export")
    assert exported.status_code == 200, exported.text
    assert exported.content[:2] == b"PK"
    assert (tmp_path / "simulations" / rows["correct"]["id"] / "videos" / "execution.mp4").exists()


def test_a_completed_scene_serves_the_object_photograph(client: TestClient):
    from app.db import SessionLocal
    from app.models.simulation import SimulationDataset
    from app.simulation.service import dataset_dir

    session = SessionLocal()
    row = SimulationDataset(
        name="objects",
        variant="objects",
        camera_mode="fixed",
        research_mode="bench",
        width=320,
        height=240,
        fps=8,
        duration=1,
        status="completed",
    )
    session.add(row)
    session.commit()
    dataset_id = row.id
    session.close()
    dest = dataset_dir(dataset_id) / "textures"
    dest.mkdir(parents=True)
    (dest / "Object_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    picture = client.get(f"/api/simulations/{dataset_id}/textures/Object_1")
    assert picture.status_code == 200, picture.text
    assert picture.headers["content-type"].startswith("image/png")
    assert picture.content.startswith(b"\x89PNG")
    assert client.get(f"/api/simulations/{dataset_id}/textures/missing").status_code == 404
    assert client.get(f"/api/simulations/{dataset_id}/textures/Object.1").status_code == 404
    assert client.get(f"/api/simulations/{dataset_id}/textures/Object_1.png").status_code == 404

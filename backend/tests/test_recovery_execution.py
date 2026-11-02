"""Dry run, replay, and live safety through the API. SAM is a fake. Nothing reaches a network."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest
from app.schemas.analysis import (
    AnalysisDocument,
    BBox,
    Evidence,
    InstanceRecord,
    ObjectPrediction,
    ObservationPrediction,
    PerceptionRecord,
)
from app.schemas.enums import EvidenceClass, Provenance
from sqlalchemy import func, select

BOX = {"x": 0.2, "y": 0.2, "w": 0.1, "h": 0.1}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LEARNLOOP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEARNLOOP_DATABASE_URL", f"sqlite:///{tmp_path}/lab.db")
    from app.config import settings
    from app.db import rebind_engine

    settings.data_dir = tmp_path
    settings.database_url = f"sqlite:///{tmp_path}/lab.db"
    settings.worker_enabled = False
    settings.auto_seed = False
    settings.ingest_inline = True
    settings.sam_price_per_frame_usd = None
    rebind_engine(settings.sqlalchemy_url)
    from app.main import create_app, init_database

    init_database()
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def no_language_model(monkeypatch):
    """Any route to a language model or the job queue fails the test."""
    from app.pipeline import infer
    from app.providers import registry

    def fail(*_args, **_kwargs):
        raise AssertionError("A language model or pipeline path was reached.")

    monkeypatch.setattr(infer, "produce_document", fail)
    monkeypatch.setattr(registry, "get_provider", fail)


class FakeSam:
    """Records calls and answers with whatever text the test gives for a prompt."""

    def __init__(self, answer=None, error=None, hang=False):
        self.answer = answer
        self.error = error
        self.hang = hang
        self.uploads = 0
        self.calls: list[dict] = []
        self.started = asyncio.Event() if hang else None

    async def upload_video(self, _path):
        self.uploads += 1
        return f"file-{self.uploads}"

    async def segment_video(self, _path, prompt, *, prompt_kind="concept", file_id="", propagation_direction=None):
        from app.providers.sam import SamImageResult, parse_sam_output

        self.calls.append({"prompt": prompt, "kind": prompt_kind, "direction": propagation_direction, "file_id": file_id})
        if self.hang:
            await asyncio.sleep(60)
        if self.error:
            raise self.error
        text = self.answer(prompt, propagation_direction) if self.answer else ""
        return SamImageResult(text, parse_sam_output(text), 12, f"req-{len(self.calls)}", 3, 4, file_id)


def _evidence(provenance=Provenance.CV_DETECTION):
    return Evidence(classification=EvidenceClass.DIRECTLY_OBSERVED, provenance=provenance)


def _setup(client, tmp_path: Path, *, with_seeds=True):
    """A real clip and an authored analysis: Object_1 has a measured gap, Object_2 shares its label."""
    from app.db import SessionLocal
    from app.demo.encode import write_clip
    from app.demo.scenarios import SCENARIOS, render_scenario
    from app.models.media import VideoAsset
    from app.models.runs import AnalysisRevision
    from app.pipeline.sampling_stage import timeline_for
    from app.services.experiments import create_run
    from app.services.materialize import materialize
    from app.services.storage import data_path

    project = client.post("/api/projects", json={"name": "Recovery"}).json()
    clip = tmp_path / "fixed.mp4"
    frames = render_scenario(SCENARIOS["fixed_camera"]())
    write_clip((frames * (150 // len(frames) + 1))[:150], clip)
    with clip.open("rb") as handle:
        uploaded = client.post(
            "/api/videos", data={"project_id": project["id"]}, files={"file": ("fixed.mp4", handle, "video/mp4")}
        )
    assert uploaded.status_code == 200, uploaded.text
    session = SessionLocal()
    video = session.get(VideoAsset, uploaded.json()["id"])
    run = create_run(
        session,
        {
            "video_id": video.id,
            "provider_id": "deterministic",
            "model_id": "motion-baseline",
            "research_mode": "fixed_camera",
            "modality": "sampled_frames",
            "segmentation_mode": "boxes_only",
        },
        enqueue=False,
    )
    pts = timeline_for(video, None).pts
    times = [pts[index] for index in (0, 15, 30, 110, 125, 140)]
    rows = []
    tracks = []
    for time in times:
        rows.append(
            ObservationPrediction(
                object_local_id="Object_1",
                timestamp=time,
                bbox=BBox(**BOX),
                evidence=_evidence(Provenance.AI_PREDICTION),
                geometry_origin="vlm_estimated",
                geometry_authority="vlm",
            )
        )
        tracks.append(
            ObservationPrediction(
                object_local_id="track_1",
                timestamp=time,
                bbox=BBox(**BOX),
                evidence=_evidence(),
                track_ref="track_1",
                geometry_origin="detected",
                geometry_authority="motion",
                detection_support="detected",
                temporal_support="repeated",
            )
        )
    document = AnalysisDocument(
        summary="authored",
        objects=[
            ObjectPrediction(local_id="Object_1", label="washer", instance_id="track_1" if with_seeds else None, association_status="associated"),
            ObjectPrediction(local_id="Object_2", label="washer"),
        ],
        observations=rows,
    )
    spatial = AnalysisDocument(
        observations=tracks,
        perception=PerceptionRecord(
            strategy="temporal",
            detector_id="motion",
            instances=[InstanceRecord(instance_id="track_1", status="active", first_seen=times[0], last_seen=times[-1])],
        ),
    )
    path = data_path("runs", run.id, "spatial.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spatial.model_dump_json())
    revision = AnalysisRevision(run_id=run.id, layer="validated", version=1, document_json=document.model_dump(mode="json"), note="seed")
    session.add(revision)
    session.flush()
    materialize(session, run.id, revision.id, document, [])
    run.active_revision_id = revision.id
    run.status = "completed"
    session.commit()
    ids = (run.id, revision.id, times)
    session.close()
    return ids


def _create(client, run_id: str, limits=None, target_ids=None) -> dict:
    candidates = client.get(f"/api/runs/{run_id}/recovery/candidates").json()
    gap = next(item for item in candidates["targets"] if "tracking_gap" in item["reasons"])
    response = client.post(
        f"/api/runs/{run_id}/recovery/experiments",
        json={"target_ids": target_ids or [gap["target_id"]], "limits": limits or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _answer(width=None, height=None, inside=True, x_shift=0.0):
    """Follow BOX on every planned frame of the prompt's tracked direction."""
    import re

    def build(prompt, direction):
        match = re.search(r"w=(\d+);h=(\d+)", prompt)
        w, h = int(match.group(1)), int(match.group(2))
        first = int(re.match(r"<(\d+)f>", prompt).group(1))
        x1, y1 = round((BOX["x"] + x_shift) * w), round(BOX["y"] * h)
        x2, y2 = round((BOX["x"] + x_shift + BOX["w"]) * w) - 1, round((BOX["y"] + BOX["h"]) * h) - 1
        frames = range(first, first + 60) if direction == "forward" else range(first, max(first - 60, -1), -1)
        line = "1<|box;x1={};y1={};x2={};y2={};w={};h={};c=0.9|><|mask;data=1,1,!A|>".format(x1, y1, x2, y2, width or w, height or h)
        return "\n".join(f"<{frame}f>{line}" for frame in frames)

    return build


def _counts():
    from app.db import SessionLocal
    from app.models.runs import AnalysisRevision, Job, ModelRequestRow, PipelineStageRun

    session = SessionLocal()
    data = {
        "requests": session.scalar(select(func.count()).select_from(ModelRequestRow)),
        "jobs": session.scalar(select(func.count()).select_from(Job)),
        "revisions": session.scalar(select(func.count()).select_from(AnalysisRevision)),
        "stages": sorted((row.stage, row.status) for row in session.scalars(select(PipelineStageRun))),
    }
    session.close()
    return data


def _install(monkeypatch, fake: FakeSam) -> FakeSam:
    from app.services import recovery

    monkeypatch.setattr(recovery, "_adapter", lambda *_args, **_kwargs: fake)
    return fake


def _live(client, experiment: dict, **extra):
    plan = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()["plan"]
    return client.post(
        f"/api/recovery/experiments/{experiment['id']}/live",
        json={"confirm": True, "plan_hash": plan["plan_hash"], **extra},
    )


def test_candidates_find_the_gap_and_its_seeds(client, tmp_path):
    run_id, _revision, times = _setup(client, tmp_path)
    data = client.get(f"/api/runs/{run_id}/recovery/candidates").json()
    gap = next(item for item in data["targets"] if "tracking_gap" in item["reasons"])
    assert gap["object_local_id"] == "Object_1"
    assert gap["time_start"] == pytest.approx(times[2])
    assert gap["time_end"] == pytest.approx(times[3])
    assert gap["recoverable"]
    assert data["seed_counts"]["independently_supported"] > 0


def test_creating_an_experiment_runs_nothing(client, tmp_path, monkeypatch, no_language_model):
    run_id, _revision, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam())
    before = _counts()
    experiment = _create(client, run_id)
    assert experiment["status"] == "draft"
    assert experiment["plan"] is None
    assert fake.calls == [] and _counts() == before


def test_dry_run_plans_with_zero_provider_calls_and_estimates(client, tmp_path, monkeypatch, no_language_model):
    run_id, _revision, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam())
    before = _counts()
    experiment = _create(client, run_id, limits={"max_requests": 4})
    result = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()
    plan = result["plan"]
    assert fake.calls == [] and fake.uploads == 0
    assert _counts() == before
    assert result["status"] == "planned"
    assert [spec["direction"] for spec in plan["requests"]] == ["forward", "backward"]
    assert plan["totals"]["requests"] == 2
    assert plan["totals"]["billed_frames"] == sum(spec["billed_frames"] for spec in plan["requests"])
    assert plan["totals"]["estimated_cost_usd"] is None
    assert "unknown" in plan["totals"]["cost_note"]
    forward, backward = plan["requests"]
    assert forward["prompt"].startswith("<0f>1<|box;")
    assert backward["prompt"].startswith(f"<{len(backward['frame_times']) - 1}f>")
    assert plan["violations"] == []


def test_a_configured_rate_produces_a_cost_and_enforces_the_budget(client, tmp_path):
    from app.config import settings

    run_id, _revision, _times = _setup(client, tmp_path)
    settings.sam_price_per_frame_usd = 0.01
    experiment = _create(client, run_id, limits={"cost_budget_usd": 0.05})
    plan = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()["plan"]
    assert plan["totals"]["estimated_cost_usd"] > 0.05
    assert any("budget" in item for item in plan["violations"])
    settings.sam_price_per_frame_usd = None


def test_recovery_only_clips_the_relevant_interval(client, tmp_path):
    run_id, _revision, times = _setup(client, tmp_path)
    experiment = _create(client, run_id, limits={"max_requests": 4})
    plan = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()["plan"]
    for spec in plan["requests"]:
        assert spec["source_start"] >= times[2] - 1.01
        assert spec["source_end"] <= times[3] + 1.01
        assert len(spec["frame_times"]) < 150


def test_no_trustworthy_seed_blocks_the_target_before_billing(client, tmp_path):
    run_id, _revision, _times = _setup(client, tmp_path, with_seeds=False)
    experiment = _create(client, run_id)
    result = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()
    assert result["plan"]["requests"] == []
    assert "trustworthy seed" in result["plan"]["blocked"][0]["reason"]


def test_a_candidate_needs_explicit_confirmation_then_overrides_automatic_choice(client, tmp_path):
    run_id, _revision, _times = _setup(client, tmp_path, with_seeds=False)
    experiment = _create(client, run_id)
    candidate = next(
        seed
        for seed in client.get(f"/api/runs/{run_id}/recovery/candidates").json()["seeds"]
        if seed["object_local_id"] == "Object_1" and seed["trust"] == "candidate"
    )
    target = experiment["targets"][0]["target_id"]
    url = f"/api/recovery/experiments/{experiment['id']}"
    client.patch(f"{url}/seeds", json={"targets": [{"target_id": target, "seed_id": candidate["seed_id"]}]})
    blocked = client.post(f"{url}/dry-run").json()["plan"]
    assert "confirmation" in blocked["blocked"][0]["reason"]
    client.patch(f"{url}/seeds", json={"targets": [{"target_id": target, "seed_id": candidate["seed_id"], "confirm_seed": True}]})
    plan = client.post(f"{url}/dry-run").json()["plan"]
    assert plan["requests"][0]["objects"][0]["seed"]["trust"] == "human_verified"


def test_a_manual_seed_box_and_points_override_the_automatic_seed(client, tmp_path):
    run_id, _revision, times = _setup(client, tmp_path)
    response = client.post(
        f"/api/runs/{run_id}/recovery/experiments",
        json={
            "manual_targets": [
                {
                    "object_local_id": "Object_2",
                    "time_start": times[2],
                    "time_end": times[3],
                    "direction": "forward",
                    "seed": {
                        "timestamp": times[2],
                        "bbox": {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1},
                        "points": [{"x": 0.51, "y": 0.51, "negative": True}],
                    },
                }
            ]
        },
    ).json()
    plan = client.post(f"/api/recovery/experiments/{response['id']}/dry-run").json()["plan"]
    spec = plan["requests"][0]
    assert spec["direction"] == "forward"
    assert spec["objects"][0]["seed"]["manual"] is True
    assert "-<|point;" in spec["prompt"]


def test_frame_limits_refuse_before_any_call(client, tmp_path, monkeypatch):
    run_id, _revision, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam())
    experiment = _create(client, run_id, limits={"max_frames_per_request": 5})
    plan = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()["plan"]
    assert plan["requests"] == []
    assert "limit per request" in plan["blocked"][0]["reason"]
    assert fake.calls == []


def test_request_and_total_frame_limits_block_a_live_run(client, tmp_path, monkeypatch):
    run_id, _revision, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam())
    experiment = _create(client, run_id, limits={"max_requests": 1})
    response = _live(client, experiment)
    assert response.status_code == 400
    assert "Nothing was submitted" in response.json()["detail"]
    experiment = _create(client, run_id, limits={"max_total_billed_frames": 10, "max_requests": 4})
    response = _live(client, experiment)
    assert response.status_code == 400
    assert "bills about" in response.json()["detail"]
    assert fake.calls == []


def test_live_needs_explicit_confirmation_and_the_approved_plan(client, tmp_path, monkeypatch):
    run_id, _revision, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam())
    experiment = _create(client, run_id, limits={"max_requests": 4})
    url = f"/api/recovery/experiments/{experiment['id']}/live"
    assert client.post(url, json={}).status_code == 400
    plan = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()["plan"]
    assert client.post(url, json={"plan_hash": plan["plan_hash"]}).status_code == 400
    stale = client.post(url, json={"confirm": True, "plan_hash": "not-this-plan"})
    assert stale.status_code == 409
    assert "Nothing was submitted" in stale.json()["detail"]
    assert fake.calls == [] and fake.uploads == 0


def test_replay_without_a_stored_response_stays_offline(client, tmp_path, monkeypatch, no_language_model):
    run_id, _revision, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam())
    experiment = _create(client, run_id)
    result = client.post(f"/api/recovery/experiments/{experiment['id']}/replay").json()
    assert fake.calls == [] and fake.uploads == 0
    assert "Nothing was sent" in result["error"]
    assert result["proposals"] == []


def test_live_then_replay_gives_the_same_rows_with_no_new_calls(client, tmp_path, monkeypatch, no_language_model):
    run_id, revision_id, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam(answer=_answer()))
    before = _counts()
    experiment = _create(client, run_id, limits={"max_requests": 4})
    result = _live(client, experiment)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "completed", data["error"]
    assert data["remote_submitted"] is True
    assert len(fake.calls) == 2
    assert {call["direction"] for call in fake.calls} == {"forward", "backward"}
    assert all(call["kind"] == "spatial" for call in fake.calls)
    proposal = data["proposals"][0]
    assert proposal["decision"] == "pending"
    assert proposal["metrics"]["counts"]["supported"] > 0
    assert proposal["metrics"]["previously_missing_frames"] > 0
    assert proposal["metrics"]["usage"]["requests"] == 2
    rows = sorted((row["timestamp"], row["outcome"]) for row in proposal["rows"])
    # The source analysis has not changed.
    after = _counts()
    assert after["revisions"] == before["revisions"] and after["stages"] == before["stages"] and after["jobs"] == before["jobs"]
    assert after["requests"] == before["requests"] + 2
    client.get(f"/api/runs/{run_id}").json()
    from app.db import SessionLocal
    from app.models.runs import AnalysisRun

    session = SessionLocal()
    assert session.get(AnalysisRun, run_id).active_revision_id == revision_id
    session.close()

    calls = len(fake.calls)
    replayed = client.post(f"/api/recovery/experiments/{experiment['id']}/replay").json()
    assert len(fake.calls) == calls
    assert replayed["status"] == "completed"
    current = next(item for item in replayed["proposals"] if item["current"])
    assert current["generation"] == 2
    assert sorted((row["timestamp"], row["outcome"]) for row in current["rows"]) == rows
    assert any(not item["current"] for item in replayed["proposals"])


def test_returned_times_map_to_source_frames_through_the_clip(client, tmp_path, monkeypatch):
    run_id, _revision, times = _setup(client, tmp_path)
    _install(monkeypatch, FakeSam(answer=_answer()))
    experiment = _create(client, run_id, limits={"max_requests": 4})
    data = _live(client, experiment).json()
    plan = {spec["key"]: spec for spec in data["plan"]["requests"]}
    proposal = data["proposals"][0]
    assert proposal["rows"][0]["timestamp"] >= times[2] - 1e-6
    for row in proposal["rows"]:
        spec = plan[row["request_key"]]
        assert spec["frame_times"][row["frame_index"]] == pytest.approx(row["timestamp"])


def test_a_changed_input_never_reuses_a_stored_response(client, tmp_path, monkeypatch):
    run_id, _revision, times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam(answer=_answer()))
    experiment = _create(client, run_id, limits={"max_requests": 4})
    _live(client, experiment)
    target = experiment["targets"][0]["target_id"]
    url = f"/api/recovery/experiments/{experiment['id']}"
    client.patch(
        f"{url}/seeds",
        json={"targets": [{"target_id": target, "direction": "forward", "seed": {"timestamp": times[2], "bbox": {"x": 0.21, "y": 0.2, "w": 0.1, "h": 0.1}}}]},
    )
    result = client.post(f"{url}/replay").json()
    assert "Nothing was sent" in result["error"]
    calls = len(fake.calls)
    assert calls == 2


def test_cache_keys_cover_media_prompt_direction_and_times():
    from app.services.recovery import cache_key

    base = cache_key("sum", [0.0, 0.1], 640, 360, "p", "forward")
    assert base == cache_key("sum", [0.0, 0.1], 640, 360, "p", "forward")
    for other in (
        cache_key("other", [0.0, 0.1], 640, 360, "p", "forward"),
        cache_key("sum", [0.0, 0.2], 640, 360, "p", "forward"),
        cache_key("sum", [0.0, 0.1], 320, 360, "p", "forward"),
        cache_key("sum", [0.0, 0.1], 640, 360, "q", "forward"),
        cache_key("sum", [0.0, 0.1], 640, 360, "p", "both"),
    ):
        assert other != base


def test_a_second_experiment_reuses_the_stored_response_for_free(client, tmp_path, monkeypatch):
    run_id, _revision, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam(answer=_answer()))
    _live(client, _create(client, run_id, limits={"max_requests": 4}))
    calls = len(fake.calls)
    second = _create(client, run_id, limits={"max_requests": 4})
    plan = client.post(f"/api/recovery/experiments/{second['id']}/dry-run").json()["plan"]
    assert plan["totals"]["remote_requests"] == 0
    result = client.post(f"/api/recovery/experiments/{second['id']}/replay").json()
    assert result["status"] == "completed"
    assert len(fake.calls) == calls


def test_a_failed_request_keeps_the_analysis_and_is_not_sent_again(client, tmp_path, monkeypatch):
    from app.providers.types import ProviderError

    run_id, revision_id, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam(error=ProviderError("provider exploded")))
    experiment = _create(client, run_id, limits={"max_requests": 4})
    data = _live(client, experiment).json()
    assert data["status"] == "failed"
    assert "was submitted" in data["error"]
    assert data["remote_submitted"] is True
    assert len(fake.calls) == 1
    assert data["requests"][0]["submission"] == "submitted"
    from app.db import SessionLocal
    from app.models.runs import AnalysisRun

    session = SessionLocal()
    assert session.get(AnalysisRun, run_id).active_revision_id == revision_id
    session.close()
    # A retry refuses the request that may have been billed, then runs only with approval.
    fake.error = None
    fake.answer = _answer()
    retried = _live(client, experiment).json()
    assert len(fake.calls) >= 1
    assert sum(call["prompt"] == fake.calls[0]["prompt"] for call in fake.calls) == 1
    assert "may have been billed" in retried["error"]
    keys = [item["cache_key"] for item in retried["requests"] if item["submission"] == "submitted"]
    approved = _live(client, experiment, resubmit_keys=keys).json()
    assert approved["status"] == "completed", approved["error"]
    assert sum(call["prompt"] == fake.calls[0]["prompt"] for call in fake.calls) == 2


def test_an_upload_failure_says_nothing_was_submitted(client, tmp_path, monkeypatch):
    class BadUpload(FakeSam):
        async def upload_video(self, _path):
            self.uploads += 1
            raise OSError("network down")

    run_id, _revision, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, BadUpload())
    experiment = _create(client, run_id, limits={"max_requests": 4, "max_upload_retries": 1})
    data = _live(client, experiment).json()
    assert data["status"] == "failed"
    assert "no SAM request was submitted" in data["error"]
    assert data["remote_submitted"] is False
    assert fake.uploads == 2 and fake.calls == []


def test_partial_responses_keep_what_arrived(client, tmp_path, monkeypatch):
    run_id, _revision, _times = _setup(client, tmp_path)

    class HalfWay(FakeSam):
        async def segment_video(self, path, prompt, **kwargs):
            if self.calls:
                self.calls.append({"prompt": prompt})
                raise OSError("connection reset")
            return await super().segment_video(path, prompt, **kwargs)

    _install(monkeypatch, HalfWay(answer=_answer()))
    experiment = _create(client, run_id, limits={"max_requests": 4})
    data = _live(client, experiment).json()
    assert data["status"] == "partial"
    assert data["proposals"][0]["rows"]


def test_malformed_output_rows_are_skipped_not_invented(client, tmp_path, monkeypatch):
    run_id, _revision, _times = _setup(client, tmp_path)
    _install(monkeypatch, FakeSam(answer=lambda *_args: "<0f>garbage\n<9999f>1<|box;x1=1;y1=1;x2=2;y2=2;w=640;h=360|><|mask;data=1,1,!A|>\nnot a line"))
    experiment = _create(client, run_id, limits={"max_requests": 4})
    data = _live(client, experiment).json()
    assert data["status"] == "completed"
    proposal = data["proposals"][0]
    assert proposal["rows"] == []
    assert proposal["metrics"]["remaining_gaps"]


def test_cancel_before_a_call_sends_nothing(client, tmp_path, monkeypatch):
    from app.db import SessionLocal
    from app.models.recovery import RecoveryExperiment
    from app.services import recovery

    run_id, _revision, _times = _setup(client, tmp_path)
    fake = FakeSam(answer=_answer())
    experiment = _create(client, run_id, limits={"max_requests": 4})
    plan = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()["plan"]
    session = SessionLocal()
    row = session.get(RecoveryExperiment, experiment["id"])
    recovery.authorize_live(session, row, {"confirm": True, "plan_hash": plan["plan_hash"]})
    recovery.cancel(session, row)
    asyncio.run(recovery.execute_live(session, row.id, fake))
    assert row.status == "canceled"
    assert fake.calls == []
    session.close()


def test_cancel_in_flight_stops_waiting_and_warns_about_billing(client, tmp_path):
    from app.db import SessionLocal
    from app.models.recovery import RecoveryExperiment
    from app.services import recovery

    run_id, _revision, _times = _setup(client, tmp_path)
    fake = FakeSam(hang=True)
    experiment = _create(client, run_id, limits={"max_requests": 4})
    plan = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()["plan"]
    session = SessionLocal()
    row = session.get(RecoveryExperiment, experiment["id"])
    recovery.authorize_live(session, row, {"confirm": True, "plan_hash": plan["plan_hash"]})

    async def scenario():
        fake.started = asyncio.Event()
        work = asyncio.create_task(recovery.execute_live(session, row.id, fake))
        while not fake.calls:
            await asyncio.sleep(0.05)
        other = SessionLocal()
        target = other.get(RecoveryExperiment, row.id)
        target.cancel_requested = True
        other.commit()
        other.close()
        await asyncio.wait_for(work, timeout=20)

    asyncio.run(scenario())
    session.refresh(row)
    assert row.status == "canceled"
    assert "may still be billed" in row.error
    session.close()


def test_a_request_past_its_timeout_is_abandoned_and_not_retried(client, tmp_path):
    from app.db import SessionLocal
    from app.models.recovery import RecoveryExperiment
    from app.services import recovery

    run_id, _revision, _times = _setup(client, tmp_path)
    fake = FakeSam(hang=True)
    experiment = _create(client, run_id, limits={"max_requests": 4, "timeout_s": 0.2})
    plan = client.post(f"/api/recovery/experiments/{experiment['id']}/dry-run").json()["plan"]
    session = SessionLocal()
    row = session.get(RecoveryExperiment, experiment["id"])
    recovery.authorize_live(session, row, {"confirm": True, "plan_hash": plan["plan_hash"]})
    asyncio.run(recovery.execute_live(session, row.id, fake))
    assert row.status == "failed"
    assert "may be billed" in row.error
    assert len(fake.calls) == 1
    session.close()


def test_two_recoveries_cannot_run_on_one_analysis(client, tmp_path, monkeypatch):
    from app.db import SessionLocal
    from app.models.recovery import RecoveryExperiment

    run_id, _revision, _times = _setup(client, tmp_path)
    fake = _install(monkeypatch, FakeSam())
    first = _create(client, run_id, limits={"max_requests": 4})
    second = _create(client, run_id, limits={"max_requests": 4})
    session = SessionLocal()
    session.get(RecoveryExperiment, first["id"]).status = "running"
    session.commit()
    session.close()
    response = _live(client, second)
    assert response.status_code == 409
    assert fake.calls == []


def test_interrupted_recovery_is_not_resumed_at_startup(client, tmp_path):
    from app.db import SessionLocal
    from app.models.recovery import RecoveryExperiment
    from app.services.recovery import recover_interrupted

    run_id, _revision, _times = _setup(client, tmp_path)
    experiment = _create(client, run_id)
    session = SessionLocal()
    session.get(RecoveryExperiment, experiment["id"]).status = "running"
    session.commit()
    assert recover_interrupted(session) == 1
    assert session.get(RecoveryExperiment, experiment["id"]).status == "interrupted"
    session.close()


def test_accepting_forks_a_revision_and_leaves_the_original_intact(client, tmp_path, monkeypatch, no_language_model):
    from app.db import SessionLocal
    from app.models.runs import AnalysisRevision, AnalysisRun

    run_id, revision_id, _times = _setup(client, tmp_path)
    _install(monkeypatch, FakeSam(answer=_answer()))
    experiment = _create(client, run_id, limits={"max_requests": 4})
    data = _live(client, experiment).json()
    session = SessionLocal()
    original = json.dumps(session.get(AnalysisRevision, revision_id).document_json, sort_keys=True)
    session.close()
    proposal = next(item for item in data["proposals"] if item["current"])
    url = f"/api/recovery/experiments/{experiment['id']}/proposals/decide"
    result = client.post(url, json={"decision": "accepted", "proposal_ids": [proposal["id"]]})
    assert result.status_code == 200, result.text
    decided = next(item for item in result.json()["proposals"] if item["id"] == proposal["id"])
    assert decided["decision"] == "accepted"
    session = SessionLocal()
    run = session.get(AnalysisRun, run_id)
    assert run.active_revision_id == decided["applied_revision_id"] != revision_id
    assert json.dumps(session.get(AnalysisRevision, revision_id).document_json, sort_keys=True) == original
    new = AnalysisDocument.model_validate(session.get(AnalysisRevision, run.active_revision_id).document_json)
    assert session.get(AnalysisRevision, run.active_revision_id).layer == "human"
    old = AnalysisDocument.model_validate(json.loads(original))
    assert any(row.source_model == "sam-3.1" for row in new.observations)
    assert new.actions == old.actions and new.relationships == old.relationships and new.workflow_steps == old.workflow_steps
    session.close()
    again = client.post(url, json={"decision": "accepted", "proposal_ids": [proposal["id"]]})
    assert again.status_code == 409


def test_rejecting_keeps_the_analysis_and_the_proposal(client, tmp_path, monkeypatch):
    run_id, revision_id, _times = _setup(client, tmp_path)
    _install(monkeypatch, FakeSam(answer=_answer()))
    experiment = _create(client, run_id, limits={"max_requests": 4})
    data = _live(client, experiment).json()
    proposal = data["proposals"][0]
    result = client.post(
        f"/api/recovery/experiments/{experiment['id']}/proposals/decide",
        json={"decision": "rejected", "proposal_ids": [proposal["id"]]},
    ).json()
    assert result["proposals"][0]["decision"] == "rejected"
    assert result["proposals"][0]["rows"]
    assert client.get(f"/api/runs/{run_id}").json()["revision"]["id"] == revision_id


def test_accept_refuses_a_changed_analysis(client, tmp_path, monkeypatch):
    from app.db import SessionLocal
    from app.models.runs import AnalysisRun
    from app.services.corrections import update_object

    run_id, _revision, _times = _setup(client, tmp_path)
    _install(monkeypatch, FakeSam(answer=_answer()))
    experiment = _create(client, run_id, limits={"max_requests": 4})
    data = _live(client, experiment).json()
    session = SessionLocal()
    update_object(session, session.get(AnalysisRun, run_id), "Object_1", {"label": "ring"})
    session.close()
    result = client.post(
        f"/api/recovery/experiments/{experiment['id']}/proposals/decide",
        json={"decision": "accepted", "proposal_ids": [data["proposals"][0]["id"]]},
    )
    assert result.status_code == 409


def test_recovery_code_cannot_reach_a_language_model_or_a_stage_reset():
    root = Path(__file__).resolve().parents[1] / "app"
    forbidden = {"produce_document", "retry_from", "execute_job", "get_provider", "run_video_segmentation", "_clear_sam_rows", "create_run"}
    for name in ("services/recovery.py", "api/recovery.py"):
        tree = ast.parse((root / name).read_text())
        used = {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert not used & forbidden, name


# ---- The normal pipeline ----


def _pipeline(client, tmp_path, monkeypatch, *, mode: str, strategy="concept", fake=None) -> str:
    from app.db import SessionLocal
    from app.demo.encode import write_clip
    from app.demo.scenarios import SCENARIOS, build_document, render_scenario
    from app.models.runs import Job
    from app.pipeline import infer, locate_pass, sam_stage
    from app.pipeline.runner import execute_job
    from app.providers.types import ProviderResponse, Usage
    from app.services import credentials

    text = json.dumps(build_document(SCENARIOS["fixed_camera"]()).model_dump(mode="json"))

    class Authored:
        async def generate(self, _secret, _request):
            return ProviderResponse(text=text, raw={}, usage=Usage(), latency_ms=1)

    for module in (infer, locate_pass):
        monkeypatch.setattr(module, "needs_secret", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(module, "get_provider", lambda *_args, **_kwargs: Authored())
        monkeypatch.setattr(module, "model_for_call", lambda _session, run: (run.model_id, run.model_id))
    monkeypatch.setattr(credentials, "load_secret", lambda *_args, **_kwargs: ("key", "test"))
    monkeypatch.setattr(sam_stage, "load_secret", lambda *_args, **_kwargs: ("key", "test"))
    if fake is not None:
        monkeypatch.setattr(sam_stage, "SamAdapter", lambda *_args, **_kwargs: fake)
    project = client.post("/api/projects", json={"name": mode}).json()
    clip = tmp_path / f"{mode}-{strategy}.mp4"
    write_clip(render_scenario(SCENARIOS["fixed_camera"]())[:60], clip)
    with clip.open("rb") as handle:
        video = client.post("/api/videos", data={"project_id": project["id"]}, files={"file": ("fixed.mp4", handle, "video/mp4")}).json()
    created = client.post(
        "/api/runs",
        json={
            "name": mode,
            "video_id": video["id"],
            "provider_id": "openai",
            "model_id": "gpt-4.1",
            "modality": "sampled_frames",
            "research_mode": "fixed_camera",
            "sampling_preset": "detailed",
            "analysis_pass": "single",
            "segmentation_mode": mode,
            "segmentation_model_id": "sam-3.1" if mode.startswith("sam") else "",
            "sam_prompt_strategy": strategy,
            "max_frames": 12,
        },
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    session = SessionLocal()
    job = session.scalar(select(Job).where(Job.run_id == run_id, Job.status == "pending"))
    job.status = "running"
    session.commit()
    asyncio.run(execute_job(session, job))
    session.close()
    return run_id


class PipelineSam(FakeSam):
    def __init__(self):
        super().__init__(answer=lambda *_args: "")

    async def segment_video(self, video_path, prompt, *, prompt_kind="concept", file_id="", propagation_direction=None):
        return await super().segment_video(
            video_path, prompt, prompt_kind=prompt_kind, file_id=file_id or "f", propagation_direction=propagation_direction
        )


def _checkpoint(run_id: str) -> dict:
    from app.db import SessionLocal
    from app.models.runs import PipelineStageRun

    session = SessionLocal()
    stage = session.scalar(select(PipelineStageRun).where(PipelineStageRun.run_id == run_id, PipelineStageRun.stage == "segmentation"))
    data = dict(stage.checkpoint_json or {})
    data["status"] = stage.status
    data["error"] = stage.error
    session.close()
    return data


def test_selecting_sam_does_not_change_geometry_before_segmentation(client, tmp_path, monkeypatch):
    """Same mocked upstream answers under two segmentation modes give identical pre-segmentation boxes."""
    from app.pipeline.lineage import load_lineage
    from app.services.storage import data_path

    boxes = _pipeline(client, tmp_path, monkeypatch, mode="boxes_only")
    sam = _pipeline(client, tmp_path, monkeypatch, mode="sam_video", fake=PipelineSam())
    assert _checkpoint(sam)["status"] == "completed"

    def before_segmentation(run_id):
        rows = []
        for row in load_lineage(run_id):
            if row["stage"] in {"segmentation", "evidence_fusion"}:
                break
            rows.append({key: row[key] for key in ("stage", "digest", "boxes", "sources", "upstream_config")})
        return rows

    left, right = before_segmentation(boxes), before_segmentation(sam)
    assert left and left == right
    raw = [json.loads(data_path("runs", item, "semantic_raw.json").read_text()) for item in (boxes, sam)]
    for document in raw:
        # Wall-clock measurements of the tracker. They differ between any two runs.
        document["perception"]["diagnostics"].pop("elapsed_sec", None)
        document["perception"]["diagnostics"].pop("processing_fps", None)
    assert raw[0] == raw[1]


def test_a_stale_sam_summary_cannot_reach_a_stage_before_segmentation(tmp_path, monkeypatch):
    from app.config import settings
    from app.perception.sam_fusion import sam_evidence_block, write_sam_summary

    settings.data_dir = tmp_path
    write_sam_summary("run-1", "Object_1 track")
    assert sam_evidence_block("run-1", "scene_analysis") == ""
    assert sam_evidence_block("run-1", "action_analysis") == ""
    assert sam_evidence_block("run-1", "state_tracking") == ""
    assert sam_evidence_block("run-1", "workflow")


def test_concept_is_the_default_and_a_legacy_run_stays_on_it(client, tmp_path, monkeypatch):
    fake = PipelineSam()
    run_id = _pipeline(client, tmp_path, monkeypatch, mode="sam_video", fake=fake)
    assert fake.calls and all(call["kind"] == "concept" for call in fake.calls)
    checkpoint = _checkpoint(run_id)
    assert checkpoint["prompt_strategy"] == "concept"
    from types import SimpleNamespace

    from app.pipeline.sam_stage import prompt_strategy

    assert prompt_strategy(SimpleNamespace(config_json={})) == "concept"


def test_an_unknown_strategy_is_refused(client, tmp_path):
    from app.services.experiments import _prompt_strategy

    with pytest.raises(ValueError):
        _prompt_strategy({"sam_prompt_strategy": "guess"})


def test_trusted_spatial_with_no_seed_makes_no_concept_call_and_says_so(client, tmp_path, monkeypatch):
    fake = PipelineSam()
    run_id = _pipeline(client, tmp_path, monkeypatch, mode="sam_video", strategy="trusted_spatial", fake=fake)
    checkpoint = _checkpoint(run_id)
    assert checkpoint["prompt_strategy"] == "trusted_spatial"
    assert all(call["kind"] == "spatial" for call in fake.calls)
    assert all(item["prompt"] in {"spatial", "unobserved", "seed_rejected"} for item in checkpoint["prompt_outcomes"])


def test_hybrid_records_each_prompt_kind_separately(client, tmp_path, monkeypatch):
    fake = PipelineSam()
    run_id = _pipeline(client, tmp_path, monkeypatch, mode="sam_video", strategy="hybrid", fake=fake)
    checkpoint = _checkpoint(run_id)
    assert checkpoint["prompt_strategy"] == "hybrid"
    assert sum(checkpoint["prompt_kinds_sent"].values()) == len(fake.calls)
    for call in fake.calls:
        assert ("<|" in call["prompt"]) == (call["kind"] == "spatial")
    assert not any(call["kind"] == "spatial" and call["direction"] is None for call in fake.calls)

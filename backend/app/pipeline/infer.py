"""Model calls, windowing, and local document construction."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.demo.scenarios import SCENARIOS, build_document
from app.models.media import FrameAsset, VideoAsset
from app.models.runs import AnalysisRun, ModelRequestRow
from app.pipeline.merge import restore_omitted_records
from app.pipeline.parsing import parse_analysis
from app.pipeline.prompts import compose
from app.providers.registry import get_provider
from app.providers.types import FrameInput, GenerationRequest, UnsupportedMode, rough_input_tokens
from app.schemas.analysis import AnalysisDocument, Coverage
from app.services.costs import fail_provider_call, finish_provider_call, open_provider_call
from app.services.credentials import load_secret
from app.services.storage import data_path, resolve_stored


class BudgetExceeded(RuntimeError):
    pass


class StageSkipped(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def load_working(run_id: str) -> AnalysisDocument:
    path = _working_path(run_id)
    if not path.exists():
        return AnalysisDocument()
    return AnalysisDocument.model_validate(json.loads(path.read_text()))


def save_working(run_id: str, document: AnalysisDocument) -> None:
    path = _working_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2))


def _working_path(run_id: str) -> Path:
    return data_path("runs", run_id, "working.json")


def fixture_document_for(video: VideoAsset) -> AnalysisDocument | None:
    index_path = data_path("fixture_index.json")
    if not index_path.exists():
        return None
    index = json.loads(index_path.read_text())
    key = index.get(video.checksum_sha256)
    if not key or key not in SCENARIOS:
        return None
    return build_document(SCENARIOS[key]())


def register_fixture(checksum: str, scenario_key: str) -> None:
    path = data_path("fixture_index.json")
    current = json.loads(path.read_text()) if path.exists() else {}
    current[checksum] = scenario_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2))


async def produce_document(
    session: Session,
    run: AnalysisRun,
    video: VideoAsset,
    frames: list[FrameAsset],
    stage: str,
    coverage: Coverage,
    spatial_document: AnalysisDocument | None,
    user_extra: str = "",
) -> AnalysisDocument:
    if run.provider_id == "fixture":
        document = fixture_document_for(video)
        if document is None:
            raise UnsupportedMode(
                "The fixture provider only returns an authored analysis when the video checksum "
                "matches a generated LearnLoop fixture. It will not invent results for other files."
            )
        document.coverage = coverage
        return document
    if run.provider_id == "deterministic":
        if spatial_document is None:
            raise UnsupportedMode("spatial tracking did not produce a document")
        spatial_document.coverage = coverage
        return spatial_document
    if run.provider_id == "manual_json":
        raise UnsupportedMode("Import JSON from the library instead of launching a model run.")

    config = run.config_json or {}
    prompt_key = {
        "scene_analysis": "scene",
        "action_analysis": "actions",
        "state_tracking": "states",
        "workflow": "workflow",
        "ambiguity": "ambiguity",
        "reinspection": "reinspect",
    }.get(stage, "scene")
    if config.get("analysis_pass") == "single" and stage == "scene_analysis":
        extra = "\n\n".join(
            [
                "Return one JSON object with objects, observations, states, transitions, relationships, "
                "actions, workflow_steps, dependencies, and ambiguities.",
            ]
        )
        prompt_key = "scene"
    else:
        extra = ""
    if stage != "scene_analysis":
        prior = load_working(run.id)
        extra += "\n\nPrior structured analysis JSON:\n" + prior.model_dump_json()
        extra += "\n\nReturn JSON for this stage only. Keep object ids stable."
    system = config.get("prompt_snapshot") or compose(prompt_key, run.research_mode, extra)
    if config.get("analysis_pass") == "single" and stage == "scene_analysis":
        system = compose("scene", run.research_mode, extra)
        system += "\n\n" + compose("actions", run.research_mode)
        system += "\n\n" + compose("states", run.research_mode)
        system += "\n\n" + compose("workflow", run.research_mode)
        system += "\n\n" + compose("ambiguity", run.research_mode)
    elif extra and "prompt_snapshot" not in config:
        system = compose(prompt_key, run.research_mode, extra)
    elif stage != "scene_analysis":
        system = compose(prompt_key, run.research_mode, extra)

    schema = AnalysisDocument.model_json_schema()
    user = (
        f"Recording duration {video.duration_sec:.3f} seconds. "
        f"Research mode {run.research_mode}. Input modality {run.modality}. "
        "Use only the attached evidence."
    )
    if user_extra:
        user += "\n\n" + user_extra
    if run.modality == "native_video":
        return await _call(
            session,
            run,
            stage,
            system,
            user,
            [],
            video,
            schema,
        )
    selected = _budget_frames(frames, int(config.get("max_frames") or 48))
    coverage.frames_submitted = len(selected)
    coverage.frame_timestamps = [frame.timestamp for frame in selected]
    windows = _windows(selected, int(config.get("max_images_per_call") or 16))
    partials: list[AnalysisDocument] = []
    for index, window in enumerate(windows):
        text_doc = await _call(
            session,
            run,
            stage,
            system,
            user + f"\n\nThis is window {index + 1} of {len(windows)} in temporal order.",
            window,
            video,
            schema,
        )
        partials.append(text_doc)
    if len(partials) == 1:
        return partials[0]
    merged = await _call(
        session,
        run,
        stage,
        system,
        "Merge these partial JSON analyses in time order. Do not invent events from gaps between windows. "
        "Keep every state, transition, relationship, workflow step, dependency, and ambiguity from the windows.\n\n"
        + "\n\n".join(item.model_dump_json() for item in partials),
        [],
        video,
        schema,
    )
    return restore_omitted_records(merged, partials)


async def _call(session, run, stage, system, user, frames, video, schema) -> AnalysisDocument:
    config = run.config_json or {}
    if run.input_tokens and False:
        pass
    sent = session.query(ModelRequestRow).filter(ModelRequestRow.run_id == run.id).count()
    if sent >= int(config.get("max_requests") or 12):
        raise BudgetExceeded("request budget reached; no further model calls were made")
    secret, _source = load_secret(session, run.provider_id)
    if run.provider_id not in {"deterministic", "fixture"} and not secret:
        raise UnsupportedMode(
            f"No API key is configured for {run.provider_id}. "
            "Add one on the provider page or export the documented environment variable."
        )
    provider = get_provider(run.provider_id, session)
    request = GenerationRequest(
        model_id=run.model_id,
        system_prompt=system,
        user_prompt=user,
        frames=[FrameInput(frame.timestamp, data_path(*Path(frame.path).parts)) for frame in frames],
        video_path=resolve_stored(video.proxy_path or video.stored_path)
        if run.modality == "native_video"
        else None,
        modality=run.modality,
        json_schema=schema,
        temperature=config.get("temperature"),
        reasoning_effort=config.get("reasoning_effort"),
        image_detail=config.get("image_detail") or "high",
        timeout_s=float(config.get("timeout_s") or 180),
    )
    estimate = rough_input_tokens(request)
    ceiling = config.get("cost_ceiling_usd")
    if ceiling is not None and run.cost_usd >= float(ceiling):
        raise BudgetExceeded(
            f"estimated spend {run.cost_usd:.4f} USD has reached the ceiling of {ceiling} USD"
        )
    row = open_provider_call(
        session,
        run,
        stage=stage,
        provider_id=run.provider_id,
        model_id=run.model_id,
        modality=run.modality,
        prompt_version_id=run.prompt_version_id,
        parameters={
            "temperature": request.temperature,
            "reasoning_effort": request.reasoning_effort,
            "image_detail": request.image_detail,
            "estimated_input_tokens": estimate,
            "sdk_note": "recorded on the response",
        },
        frame_timestamps=[frame.timestamp for frame in frames],
    )
    session.commit()
    try:
        response = await provider.generate(secret, request)
    except Exception as exc:
        fail_provider_call(row, exc)
        session.commit()
        raise
    finish_provider_call(session, run, row, response)
    session.commit()
    return parse_analysis(response.text)


def _budget_frames(frames: list[FrameAsset], limit: int) -> list[FrameAsset]:
    ordered = sorted(frames, key=lambda item: item.timestamp)
    if len(ordered) <= limit:
        return ordered
    step = len(ordered) / limit
    return [ordered[min(len(ordered) - 1, int(i * step))] for i in range(limit)]


def _windows(frames: list[FrameAsset], size: int) -> list[list[FrameAsset]]:
    size = max(size, 1)
    return [frames[index : index + size] for index in range(0, len(frames), size)] or [[]]

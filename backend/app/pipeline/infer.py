"""Model calls, windowing, and local document construction."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.demo.scenarios import SCENARIOS, build_document
from app.models.media import FrameAsset, VideoAsset
from app.models.runs import AnalysisRun, ModelRequestRow
from app.perception.evidence import evidence_text
from app.perception.ground import apply_perception
from app.perception.roster import align_to_roster, drop_split_objects
from app.pipeline.merge import restore_omitted_records
from app.pipeline.parsing import parse_analysis
from app.pipeline.prompts import compose
from app.providers.registry import get_provider, needs_secret, run_transport
from app.providers.types import FrameInput, GenerationRequest, UnsupportedMode, rough_input_tokens
from app.schemas.analysis import AnalysisDocument, Coverage
from app.services.costs import fail_provider_call, finish_provider_call, open_provider_call
from app.services.credentials import load_secret
from app.services.storage import data_path, resolve_stored
from app.video.sampling import budget_indices


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
    if spatial_document is not None:
        tracked = evidence_text(spatial_document)
        if tracked:
            user += "\n\n" + tracked
    if run.modality == "native_video":
        return _with_perception(
            await _call(
                session,
                run,
                stage,
                system,
                user,
                [],
                video,
                schema,
            ),
            spatial_document,
        )
    selected = _budget_frames(frames, int(config.get("max_frames") or 48))
    coverage.frames_submitted = len(selected)
    coverage.frame_timestamps = [frame.timestamp for frame in selected]
    windows = _windows(selected, int(config.get("max_images_per_call") or 16))
    partials: list[AnalysisDocument] = []
    for index, window in enumerate(windows):
        note = _window_note(index, windows)
        if partials:
            note += _closed_roster(partials[0])
        text_doc = await _call(
            session,
            run,
            stage,
            system,
            user + note,
            window,
            video,
            schema,
        )
        if partials:
            text_doc = align_to_roster(partials[0], text_doc)
        partials.append(text_doc)
    if len(partials) == 1:
        return _with_perception(partials[0], spatial_document)
    merged = await _call(
        session,
        run,
        stage,
        system,
        _merge_instruction(windows) + "\n\n" + "\n\n".join(item.model_dump_json() for item in partials),
        [],
        video,
        schema,
    )
    merged = drop_split_objects(partials[0], restore_omitted_records(merged, partials))
    return _with_perception(merged, spatial_document)


def _with_perception(document: AnalysisDocument, spatial: AnalysisDocument | None) -> AnalysisDocument:
    if spatial is None or spatial.perception is None:
        return document
    return apply_perception(document, spatial)


async def _call(session, run, stage, system, user, frames, video, schema) -> AnalysisDocument:
    config = run.config_json or {}
    if run.input_tokens and False:
        pass
    sent = session.query(ModelRequestRow).filter(ModelRequestRow.run_id == run.id).count()
    if sent >= int(config.get("max_requests") or 12):
        raise BudgetExceeded("request budget reached; no further model calls were made")
    transport = run_transport(run.provider_id, config)
    secret = ""
    if needs_secret(run.provider_id, transport):
        secret, _source = load_secret(session, run.provider_id)
        if not secret:
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
        metadata={"transport": transport} if run.provider_id in {"anthropic", "cursor"} else {},
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
            **({"anthropic_transport": transport} if run.provider_id == "anthropic" else {}),
            **({"cursor_transport": transport} if run.provider_id == "cursor" else {}),
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
    return [ordered[index] for index in budget_indices(len(ordered), limit)]


# Adjacent calls repeat this many frames so a window boundary is a shared moment,
# not a fresh scene. Two frames is enough to show the arrangement on both sides.
WINDOW_OVERLAP = 2


def _windows(
    frames: list[FrameAsset], size: int, overlap: int = WINDOW_OVERLAP
) -> list[list[FrameAsset]]:
    size = max(size, 1)
    if not frames:
        return [[]]
    if len(frames) <= size:
        return [list(frames)]
    overlap = min(max(overlap, 0), size - 1)
    step = size - overlap
    windows: list[list[FrameAsset]] = []
    start = 0
    while start < len(frames):
        windows.append(frames[start : start + size])
        if start + size >= len(frames):
            break
        start += step
    return windows


def _frame_time(frame: FrameAsset) -> float:
    return float(frame.timestamp)


def _shared_times(left: list[FrameAsset], right: list[FrameAsset]) -> list[float]:
    right_times = {round(_frame_time(frame), 3) for frame in right}
    return [_frame_time(frame) for frame in left if round(_frame_time(frame), 3) in right_times]


def _fmt_times(times: list[float]) -> str:
    parts = [f"{value:.3f}".rstrip("0").rstrip(".") + " s" for value in times]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _closed_roster(roster: AnalysisDocument) -> str:
    lines = [f"{obj.local_id}: {obj.label}" for obj in roster.objects if obj.label]
    if not lines:
        return ""
    return (
        "\n\nThe objects are already named. Use only these ids. A part seen at a new angle, "
        "including a second arm of a piece that was one object, keeps that id and one box around "
        "the whole piece. Do not add an object.\n" + "\n".join(lines)
    )


def _window_note(index: int, windows: list[list[FrameAsset]]) -> str:
    total = len(windows)
    sentences = [f"This is window {index + 1} of {total} in temporal order."]
    if total > 1:
        if index > 0:
            shared = _shared_times(windows[index - 1], windows[index])
            if shared:
                sentences.append(
                    f"The opening frames at {_fmt_times(shared)} repeat the end of the previous window "
                    "and are the same moments."
                )
        if index + 1 < total:
            shared = _shared_times(windows[index], windows[index + 1])
            if shared:
                sentences.append(
                    f"The closing frames at {_fmt_times(shared)} are repeated at the start of the next window."
                )
        sentences.append(
            "Continue existing object ids across repeated frames. A later view that shows fewer pieces "
            "because of occlusion, stacking, or an end-on angle keeps those ids and records an ambiguity. "
            "Create a new object id only when appearance contradicts every existing object."
        )
    sentences.append(_boxing_rule(windows[index]))
    return "\n\n" + " ".join(sentences)


def _boxing_rule(frames: list[FrameAsset]) -> str:
    times = _fmt_times([_frame_time(frame) for frame in frames])
    where = f"The supplied frames are at {times}. " if times else ""
    return (
        where
        + "On each of those frames, every object that is even partly visible gets its own observation "
        "and a tight box around that object alone. Hands, fingers, arms, and the stationary background "
        "the parts rest on are not objects in that list. A box around a larger object does not count for a "
        "smaller part that can still be distinguished, including a part resting on it, around it, or "
        "partly covered by a hand. Do not skip a frame because the object looks unchanged. A change in "
        "brightness, color cast, or shadow is not disappearance: if the shape and the color relative to "
        "the parts around it still match an earlier view, box that same object again. A darker part stays "
        "that part. Do not drop it, and do not redraw it as a shadow or as the edge of a lighter neighbor. "
        "A gap, hole, or edge that identified it before still identifies it. A long object is still that "
        "object when only its end, face, or head is showing. Do not wait for the long side or a repeating "
        "surface pattern. Box that end or head, including when it sits in the middle of a ring, and do not "
        "let the ring's box stand in for it. When the repeating surface and the head or top are both in "
        "view, the same box includes the head or top. Do not stop where the repeating surface ends. A box "
        "that holds only that surface leaves the head or top unidentified. A compact faceted part touching "
        "a ring is its own object. "
        "Box it when any face is visible, even if most of it is covered. Do not leave it out because "
        "the view is end-on or because the ring beside it already has a box. If that faceted part is seen "
        "face-on, surrounding an end and surrounded by the ring, it still gets its own box on the flats. "
        "The ring's box and the inner end's box do not count for it. When the edges are faint, keep "
        "the box tight on that part instead of growing it across the hand or a neighbor. A rotated or "
        "partly covered part is still that part: box the visible portion, even when its long side is no "
        "longer the side you saw before. Leave an object out of a frame when it is fully out of view or "
        "fully hidden, and do not invent a box then. Do not keep its id by boxing a different part, "
        "including a larger neighbor of a similar color. A box many times larger than the part is the wrong box."
    )


def _merge_instruction(windows: list[list[FrameAsset]]) -> str:
    shared: list[float] = []
    for left, right in zip(windows, windows[1:], strict=False):
        for timestamp in _shared_times(left, right):
            if timestamp not in shared:
                shared.append(timestamp)
    boundary = f" The shared timestamps are {_fmt_times(shared)}." if shared else ""
    return (
        "Merge these partial JSON analyses in time order. Adjacent windows share their boundary frames, "
        "and those shared timestamps are the same moments."
        + boundary
        + " Continue object ids across the boundary. When a later window shows fewer pieces of an arrangement "
        "already described, because the parts are occluded, stacked, or seen end-on, keep the earlier ids "
        "and record which of them remain possible as an ambiguity. Create a new object id only when appearance "
        "contradicts every earlier object. Do not duplicate actions that fall on the shared frames. "
        "Keep every observation. If a window boxed an object at a timestamp, that box stays; do not drop "
        "it because the object did not move or because a larger object already has a box. "
        "Do not invent events from gaps between windows. Keep every state, transition, relationship, "
        "workflow step, dependency, and ambiguity from the windows."
    )

"""Sampling, prompts, schema checks, and SAM grammar that do not need a database."""

from app.demo.scenarios import SCENARIOS, build_document
from app.pipeline.infer import _merge_instruction, _window_note, _windows
from app.pipeline.prompts import ACTIONS, FOUNDATIONAL, SCENE, TEMPLATES, WORKFLOW
from app.pipeline.validate import validate_document
from app.schemas.analysis import AnalysisDocument
from app.schemas.enums import RECOGNIZED_ACTIONS
from app.providers.sam import parse_sam_output
from app.video.probe import snap_timestamps
from app.video.sampling import plan_uniform, uniform_targets


def test_uniform_targets_use_duration_not_frame_index():
    times = uniform_targets(2.0, 2.0)
    assert times == [0.0, 0.5, 1.0, 1.5]


def test_timestamps_snap_to_presentation_times():
    snapped = snap_timestamps([0.1, 1.0], [0.0, 0.5, 1.0])
    assert snapped[0] == 0.0
    assert snapped[-1] == 1.0


def test_plan_uniform_records_reason():
    frames = plan_uniform(1.0, 1.0, [0.0, 0.5, 1.0])
    assert frames
    assert all(frame.reason == "uniform" for frame in frames)


def test_prompts_do_not_name_the_hardware_task():
    banned = ["bolt", "washer", "nut", "hexagonal", "lock washer"]
    blob = FOUNDATIONAL.lower() + "\n".join(body.lower() for _name, body in TEMPLATES.values())
    for word in banned:
        assert word not in blob


def test_fixture_documents_validate_for_every_mode():
    for key, factory in SCENARIOS.items():
        scenario = factory()
        document = build_document(scenario)
        issues = validate_document(document, scenario.duration)
        errors = [issue for issue in issues if issue.severity == "error"]
        assert errors == [], (key, errors)
        assert document.fixture is True
        assert document.camera is not None
        assert document.camera.world_coordinates_supported is False
        assert document.coverage is not None
        assert document.coverage.modality == "sampled_frames"


def test_sam_box_grammar():
    line = (
        "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334;c=0.5|>"
        "<|mask;x=0;y=0;c=0.5;data=27,60,~abc|>"
    )
    records = parse_sam_output(line)
    assert len(records) == 1
    assert records[0].frame_index == 0
    assert records[0].x1 == 211
    assert records[0].confidence == 0.5
    assert records[0].mask_encoding == "lossless"


class _Frame:
    def __init__(self, timestamp: float):
        self.timestamp = timestamp


def _evidence() -> dict:
    return {
        "classification": "DIRECTLY_OBSERVED",
        "provenance": "AI_PREDICTION",
        "frame_timestamps": [0.0],
    }


def test_windows_repeat_two_boundary_frames():
    frames = [_Frame(float(index)) for index in range(23)]
    windows = _windows(frames, 20)
    assert [len(window) for window in windows] == [20, 5]
    assert [frame.timestamp for frame in windows[1][:2]] == [18.0, 19.0]
    assert windows[0][-2:] == windows[1][:2]
    assert windows[-1][-1].timestamp == 22.0


def test_detailed_sampling_still_overlaps_every_boundary():
    frames = [_Frame(index / 2) for index in range(45)]
    windows = _windows(frames, 20)
    assert len(windows) == 3
    assert windows[0][-2:] == windows[1][:2]
    assert windows[1][-2:] == windows[2][:2]
    assert windows[-1][-1].timestamp == 22.0


def test_short_sequence_stays_one_window():
    frames = [_Frame(float(index)) for index in range(20)]
    assert _windows(frames, 20) == [frames]


def test_window_note_names_the_shared_timestamps():
    frames = [_Frame(float(index)) for index in range(23)]
    windows = _windows(frames, 20)
    opening = _window_note(1, windows)
    assert "The opening frames at 18 s and 19 s repeat" in opening
    assert "fewer pieces" in opening
    assert "The supplied frames are at 18 s, 19 s, 20 s, 21 s, and 22 s." in opening
    assert "tight box around that object alone" in opening
    assert "color cast, or shadow is not disappearance" in opening
    assert "do not redraw it as a shadow" in opening
    assert "only its end, face, or head is showing" in opening
    assert "compact faceted part" in opening
    assert "includes the head or top" in opening
    assert "own box on the flats" in opening
    assert "color cast" in SCENE
    assert "end, face, or head" in SCENE
    assert "includes the head or top" in SCENE
    assert "own box on the flats" in SCENE
    merge = _merge_instruction(windows)
    assert "18 s and 19 s" in merge
    assert "keep the earlier ids" in merge
    assert "Do not duplicate actions" in merge
    assert "Keep every observation" in merge


def test_unknown_action_is_in_the_vocabulary_and_approach_is_offered():
    assert "unknown" in RECOGNIZED_ACTIONS
    assert "approach" in ACTIONS
    assert "say unknown rather than forcing one" in ACTIONS
    assert "from_step and to_step must be workflow step ids" in WORKFLOW


def test_action_dependency_is_dropped_and_unknown_type_is_kept():
    document = AnalysisDocument.model_validate(
        {
            "objects": [{"local_id": "Object_1", "label": "part"}],
            "actions": [
                {
                    "local_id": "Action_13",
                    "action_type": "unknown",
                    "start": 20,
                    "end": 21,
                    "evidence": _evidence(),
                },
                {
                    "local_id": "Action_14",
                    "action_type": "move",
                    "start": 21,
                    "end": 22,
                    "evidence": _evidence(),
                },
            ],
            "workflow_steps": [
                {
                    "local_id": "Step_1",
                    "name": "Reorient the assembly",
                    "action_local_ids": ["Action_13", "Action_14"],
                    "evidence": _evidence(),
                }
            ],
            "dependencies": [
                {
                    "from_step": "Step_1",
                    "to_step": "Step_2",
                    "category": "observed_temporal",
                    "evidence": _evidence(),
                },
                {
                    "from_step": "Action_13",
                    "to_step": "Action_14",
                    "category": "observed_temporal",
                    "evidence": _evidence(),
                },
            ],
        }
    )
    issues = validate_document(document, 22.127)
    codes = [issue.code for issue in issues]
    assert "unrecognized_action" not in codes
    assert "dependency_names_action" in codes
    assert "missing_step" in codes
    assert [(item.from_step, item.to_step) for item in document.dependencies] == [("Step_1", "Step_2")]
    dropped = next(issue for issue in issues if issue.code == "dependency_names_action")
    assert dropped.severity == "warning"
    missing = next(issue for issue in issues if issue.code == "missing_step")
    assert missing.severity == "error"


def test_attempted_outcome_is_distinct_in_fixture():
    document = build_document(SCENARIOS["fixed_camera"]())
    outcomes = {action.local_id: action.outcome.value for action in document.actions}
    assert "attempted" in outcomes.values()
    assert "completed" in outcomes.values()

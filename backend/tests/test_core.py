"""Sampling, prompts, schema checks, and SAM grammar that do not need a database."""

from app.demo.scenarios import SCENARIOS, build_document
from app.pipeline.prompts import FOUNDATIONAL, TEMPLATES
from app.pipeline.validate import validate_document
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


def test_attempted_outcome_is_distinct_in_fixture():
    document = build_document(SCENARIOS["fixed_camera"]())
    outcomes = {action.local_id: action.outcome.value for action in document.actions}
    assert "attempted" in outcomes.values()
    assert "completed" in outcomes.values()

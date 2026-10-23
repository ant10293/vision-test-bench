"""Window merges must not drop states, transitions, or relationships."""

from app.pipeline.merge import restore_omitted_records
from app.schemas.analysis import AnalysisDocument
from app.schemas.enums import EvidenceClass, Provenance


def _evidence():
    return {
        "classification": EvidenceClass.DIRECTLY_OBSERVED.value,
        "provenance": Provenance.AI_PREDICTION.value,
        "frame_timestamps": [0.0],
    }


def _doc(**fields) -> AnalysisDocument:
    return AnalysisDocument.model_validate(fields)


def test_empty_merge_keeps_window_records_and_rewrites_reused_ids():
    first = _doc(
        objects=[{"local_id": "Object_1", "label": "threaded bolt"}, {"local_id": "Object_3", "label": "first washer"}],
        states=[
            {
                "object_local_id": "Object_1",
                "time_start": 0,
                "time_end": 1,
                "state_label": "on the surface",
                "evidence": _evidence(),
            }
        ],
        transitions=[
            {
                "object_local_id": "Object_1",
                "timestamp": 1,
                "from_state": "on the surface",
                "to_state": "held",
                "evidence": _evidence(),
            }
        ],
        relationships=[
            {
                "subject_local_id": "Object_3",
                "other_local_id": "Object_1",
                "kind": "near",
                "time_start": 0,
                "time_end": 1,
                "description": "washer near the bolt",
                "evidence": _evidence(),
            }
        ],
        workflow_steps=[
            {
                "local_id": "Step_1",
                "name": "Acquire bolt",
                "required_objects": ["Object_1"],
                "evidence": _evidence(),
            }
        ],
    )
    second = _doc(
        objects=[{"local_id": "Object_1", "label": "threaded bolt"}, {"local_id": "Object_3", "label": "white work surface"}],
        states=[
            {
                "object_local_id": "Object_3",
                "time_start": 20,
                "time_end": 21,
                "state_label": "supporting the work",
                "evidence": _evidence(),
            }
        ],
        transitions=[
            {
                "object_local_id": "Object_1",
                "timestamp": 21,
                "from_state": "held",
                "to_state": "threaded",
                "evidence": _evidence(),
            }
        ],
        relationships=[
            {
                "subject_local_id": "Object_3",
                "other_local_id": "Object_1",
                "kind": "supported_by",
                "time_start": 20,
                "time_end": 22,
                "description": "bolt above the surface",
                "evidence": _evidence(),
            }
        ],
        workflow_steps=[
            {
                "local_id": "Step_1",
                "name": "Position nut",
                "required_objects": ["Object_3"],
                "evidence": _evidence(),
            }
        ],
        dependencies=[
            {"from_step": "Step_1", "to_step": "Step_1", "category": "observed_temporal", "evidence": _evidence()}
        ],
    )
    merged = _doc(
        objects=[
            {"local_id": "Object_1", "label": "threaded bolt"},
            {"local_id": "Object_3", "label": "first washer"},
            {"local_id": "Object_8", "label": "white work surface"},
        ],
        actions=[{"local_id": "Action_1", "action_type": "pick_up", "start": 0, "end": 1, "evidence": _evidence()}],
    )

    restored = restore_omitted_records(merged, [first, second])

    assert [item.object_local_id for item in restored.states] == ["Object_1", "Object_8"]
    assert [item.timestamp for item in restored.transitions] == [1, 21]
    assert restored.relationships[0].subject_local_id == "Object_3"
    assert restored.relationships[1].subject_local_id == "Object_8"
    assert [item.local_id for item in restored.workflow_steps] == ["Step_1", "Step_2"]
    assert restored.workflow_steps[1].required_objects == ["Object_8"]
    assert restored.dependencies[0].from_step == "Step_2"
    assert restored.dependencies[0].to_step == "Step_2"
    assert restored.actions[0].local_id == "Action_1"


def test_merge_that_already_has_records_is_left_in_place():
    partial = _doc(
        objects=[{"local_id": "Object_1", "label": "bolt"}],
        transitions=[
            {
                "object_local_id": "Object_1",
                "timestamp": 1,
                "from_state": "a",
                "to_state": "b",
                "evidence": _evidence(),
            }
        ],
    )
    merged = _doc(
        objects=[{"local_id": "Object_1", "label": "bolt"}],
        transitions=[
            {
                "object_local_id": "Object_1",
                "timestamp": 3,
                "from_state": "kept",
                "to_state": "kept",
                "evidence": _evidence(),
            }
        ],
    )
    restored = restore_omitted_records(merged, [partial, partial])
    assert [item.timestamp for item in restored.transitions] == [3]

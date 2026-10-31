import pytest

from app.pipeline.parsing import ParseFailure, extract_json_object, parse_analysis


def test_extracts_a_fenced_object_after_prose():
    text = 'Done.\n```json\n{"ok": true}\n```'
    assert extract_json_object(text) == {"ok": True}


def test_repairs_a_missing_colon_before_a_string_value():
    text = '{"objects":[{"object_local_id"Object_4","n":1}]}'
    assert extract_json_object(text)["objects"][0]["object_local_id"] == "Object_4"


def test_repairs_missing_colons_before_a_number_and_an_object():
    text = '{"n"2,"child"{"a":1}}'
    assert extract_json_object(text) == {"n": 2, "child": {"a": 1}}


def test_keeps_the_later_document_when_the_first_was_abandoned():
    text = '{"summary":"partial","time_end{"schema_version":"1.0.0","ok":true}'
    assert extract_json_object(text) == {"schema_version": "1.0.0", "ok": True}


def test_two_complete_objects_stay_an_error():
    with pytest.raises(ParseFailure, match="malformed JSON"):
        extract_json_object('{"a": 1}{"b": 2}')


def test_a_sentence_after_a_key_is_not_rewritten():
    with pytest.raises(ParseFailure, match="malformed JSON"):
        extract_json_object('{"note"The part moved."}')


def test_alternate_action_and_step_names_are_kept():
    text = """
    {
      "objects": [{"local_id": "Object_1", "label": "gray body"}],
      "actions_detailed": [{
        "action_id": "Action_1",
        "name": "pick_up",
        "start": 0.5,
        "end": 1.5,
        "actor": "both hands",
        "object_local_ids": ["Object_1"],
        "outcome": "completed",
        "uncertainty": 0.3,
        "evidence": {
          "classification": "DIRECTLY_OBSERVED",
          "provenance": "AI_PREDICTION",
          "note": "hands grasp the body"
        }
      }],
      "workflow": [{
        "step_id": "Step_1",
        "name": "insert the clear piece",
        "action_local_ids": ["Action_1"],
        "preconditions": "both parts are loose",
        "completion_condition": "the clear piece stays in the body",
        "observed_outcome": "the free end remains",
        "time_window": {"start": 0.5, "end": 4.0},
        "uncertainty": 0.5
      }],
      "dependencies_if_any": [{
        "from_step_id": "Step_1",
        "to_step_id": "Step_2",
        "category": "observed_temporal"
      }]
    }
    """
    document = parse_analysis(text)
    action = document.actions[0]
    assert action.local_id == "Action_1"
    assert action.action_type == "pick_up"
    assert action.description == "hands grasp the body"
    step = document.workflow_steps[0]
    assert step.local_id == "Step_1"
    assert step.time_start == 0.5
    assert step.time_end == 4.0
    assert step.observed_result == "the free end remains"
    assert step.preconditions == ["both parts are loose"]
    assert document.dependencies[0].from_step == "Step_1"
    assert document.dependencies[0].to_step == "Step_2"


def test_a_nested_procedure_uses_the_model_field_names():
    text = """
    {
      "actions_and_workflow": {
        "actions": [{
          "local_id": "Action_1",
          "action_kind": "pick_up",
          "description": "pick up the body",
          "duration_start": 0.5,
          "duration_end": 2.0,
          "actor_or_basis": "both hands",
          "manipulated_object_local_id": "Object_1",
          "result": "completed",
          "uncertainty": 0.25,
          "evidence": {"classification": "DIRECTLY_OBSERVED", "provenance": "AI_PREDICTION", "note": "hands grasp"}
        }],
        "workflow": {
          "steps": [{
            "local_id": "Step_1",
            "description": "insert tiny piece into gray body",
            "step_kind": "insert",
            "objects_involved_local_ids": ["Object_1", "Object_6"],
            "supporting_action_local_ids": ["Action_1"],
            "time_start": 0.5,
            "time_end": 3.5,
            "evidence": {"classification": "INFERRED", "provenance": "AI_PREDICTION", "note": "Action_1"}
          }],
          "dependencies": [{
            "from_step_local_id": "Step_1",
            "to_step_local_id": "Step_2",
            "dependency_kind": "necessary_physical",
            "evidence": {"classification": "INFERRED", "provenance": "AI_PREDICTION", "note": "order"}
          }]
        }
      }
    }
    """
    document = parse_analysis(text)
    action = document.actions[0]
    assert action.action_type == "pick_up"
    assert action.start == 0.5 and action.end == 2.0
    assert action.object_local_ids == ["Object_1"]
    step = document.workflow_steps[0]
    assert step.name == "insert tiny piece into gray body"
    assert step.expected_action == "insert"
    assert step.required_objects == ["Object_1", "Object_6"]
    assert step.action_local_ids == ["Action_1"]
    assert document.dependencies[0].from_step == "Step_1"
    assert document.dependencies[0].category.value == "necessary_physical"


def test_flat_boxes_and_structured_procedure_are_kept():
    text = """
    {
      "objects": [{
        "local_id": "Object_1",
        "label": "gray body",
        "visual_characteristics_description": "elongated molded piece",
        "initial_resting_position": "on the table",
        "manipulated_by_hands": true,
        "identity_uncertain": true
      }],
      "observations": [{
        "object_local_id": "Object_1",
        "timestamp": 0,
        "x": 0.435, "y": 0.295, "w": 0.205, "h": 0.065,
        "visibility": "visible",
        "evidence": {"classification": "DIRECTLY_OBSERVED", "provenance": "AI_PREDICTION", "note": "on the table"}
      }, {
        "object_local_id": "Object_1",
        "timestamp": 1,
        "bbox_norm": [0.4, 0.3, 0.2, 0.1],
        "evidence": {"classification": "DIRECTLY_OBSERVED", "provenance": "AI_PREDICTION", "note": "still visible"}
      }],
      "actions_structured": [{
        "action_type": "pick_up",
        "actor_or_hand": "both hands",
        "description": "pick up the body",
        "object_ids": ["Object_1"],
        "outcome": "completed",
        "time_start": 0.8,
        "time_end": 2.0,
        "evidence": {"classification": "DIRECTLY_OBSERVED", "provenance": "AI_PREDICTION", "note": "hands grasp"}
      }],
      "workflow_steps_structured": [{
        "label": "attach black piece to body",
        "description": "the black piece stays on the body",
        "objects": ["Object_2", "Object_1"],
        "time_start": 4.0,
        "time_end": 7.5
      }],
      "workflow_dependencies": [{
        "dependency_type": "observed_temporal",
        "from_step": "attach black piece to body",
        "to_step": "attach first wheel",
        "evidence": {"classification": "INFERRED", "provenance": "AI_PREDICTION", "note": "order"}
      }],
      "relationships_structured": [{
        "label": "attached_to",
        "object_a": "Object_2",
        "object_b": "Object_1",
        "time_start": 6.5,
        "time_end": 21.9,
        "evidence_classification": "INFERRED"
      }],
      "ambiguities_structured": [{
        "label": "uncertain wheel identity swap",
        "description": "the two wheels could be swapped",
        "evidence": {"classification": "UNKNOWN", "provenance": "AI_PREDICTION", "note": "no marks"}
      }]
    }
    """
    document = parse_analysis(text)
    assert document.objects[0].manipulated is True
    assert document.objects[0].uncertain_identity is True
    assert document.objects[0].visual_characteristics == "elongated molded piece"
    assert document.observations[0].bbox.w == 0.205
    assert document.observations[1].bbox.x == 0.4
    action = document.actions[0]
    assert action.local_id == "Action_1"
    assert action.start == 0.8 and action.object_local_ids == ["Object_1"]
    step = document.workflow_steps[0]
    assert step.local_id == "Step_1"
    assert step.name == "attach black piece to body"
    assert step.observed_result == "the black piece stays on the body"
    assert document.dependencies[0].from_step == "Step_1"
    assert document.dependencies[0].to_step == "attach first wheel"
    assert document.relationships[0].kind.value == "attached"
    assert document.ambiguities[0].kind.value == "uncertain_identity"


def test_step_window_named_start_time_keeps_three_moments():
    text = """
    {
      "actions_structured": [{
        "action_local_id": "Action_1",
        "verb": "pick_up",
        "actor": "both hands",
        "object_local_ids": ["Object_1"],
        "start": 0.5,
        "end": 2.0,
        "outcome": "completed",
        "evidence_note": "hands lift the grey part",
        "classification": "DIRECTLY_OBSERVED",
        "provenance": "AI_PREDICTION",
        "uncertainty_estimate": 0.25
      }],
      "workflow_steps_structured": [{
        "local_id": "Step_1",
        "name": "Pick up grey body part",
        "object_local_ids": ["Object_1"],
        "support_action_ids_or_numbers": ["Action_1"],
        "time_window": {"start_time": 0.5, "end_time": 2.0},
        "expected_action": "The hands grasp and lift the grey part.",
        "completion_condition": "The grey part is lifted off the surface.",
        "observed_result": "By about 2s the grey part was held above the surface."
      }],
      "dependencies_structured": [{
        "from_step_local_id": "Step_1",
        "to_step_local_id": "Step_2",
        "dependency_type": "observed_temporal",
        "classification": "INFERRED",
        "provenance": "AI_PREDICTION",
        "rationale": "The clear part was handled after the grey part was lifted."
      }]
    }
    """
    document = parse_analysis(text)
    action = document.actions[0]
    assert action.local_id == "Action_1"
    assert action.action_type == "pick_up"
    assert action.description == "hands lift the grey part"
    assert action.evidence.classification.value == "DIRECTLY_OBSERVED"
    assert action.evidence.time_start == 0.5
    assert action.evidence.time_end == 2.0
    assert action.uncertainty == 0.25
    step = document.workflow_steps[0]
    assert step.time_start == 0.5
    assert step.time_end == 2.0
    assert step.required_objects == ["Object_1"]
    assert step.action_local_ids == ["Action_1"]
    dependency = document.dependencies[0]
    assert dependency.from_step == "Step_1"
    assert dependency.to_step == "Step_2"
    assert dependency.category.value == "observed_temporal"
    assert dependency.evidence.classification.value == "INFERRED"
    assert "clear part" in dependency.rationale


def test_a_combined_step_description_fills_expected_and_the_detail_line():
    text = """
    {
      "actions": [{
        "local_id": "Action_1",
        "action_type": "attach",
        "description": "attach the clear piece to the body; the free end remains",
        "start": 0.5,
        "end": 3.5,
        "object_local_ids": ["Object_1", "Object_6"],
        "evidence": {"classification": "DIRECTLY_OBSERVED", "provenance": "AI_PREDICTION", "note": "seated"}
      }],
      "workflow_steps_structured": [{
        "label": "insert the clear piece",
        "description": "Insert the clear piece: the free end remains after the fingers withdraw",
        "objects": ["Object_1", "Object_6"],
        "time_start": 0.5,
        "time_end": 4.0
      }]
    }
    """
    step = parse_analysis(text).workflow_steps[0]
    assert step.expected_action == "attach the clear piece to the body; the free end remains"
    assert step.completion_condition == "the free end remains after the fingers withdraw"
    assert "Insert the clear piece" in step.observed_result


def test_schema_actions_are_not_replaced_by_an_alias():
    text = """
    {
      "actions": [{
        "local_id": "Action_9",
        "action_type": "release",
        "start": 1,
        "end": 2,
        "evidence": {"classification": "INFERRED", "provenance": "AI_PREDICTION", "note": "kept"}
      }],
      "actions_detailed": [{
        "action_id": "Action_1",
        "name": "pick_up",
        "start": 0,
        "end": 1,
        "evidence": {"classification": "INFERRED", "provenance": "AI_PREDICTION", "note": "ignored"}
      }]
    }
    """
    document = parse_analysis(text)
    assert [item.local_id for item in document.actions] == ["Action_9"]


def test_timestamp_groups_keep_boxes_and_interactions():
    text = """
    {
      "objects": [{
        "local_id": "Object_1",
        "label": "light gray body",
        "initial_state_description": "loose on the purple surface",
        "is_manipulated": true
      }],
      "observations_timeline": [{
        "timestamp_s": 0,
        "object_observations": [{
          "local_id": "Object_1",
          "visibility": "visible",
          "bbox": {"x": 0.495, "y": 0.275, "w": 0.16, "h": 0.055},
          "confidence": 0.85
        }]
      }],
      "interactions": [{
        "actor": "hands",
        "classification": "DIRECTLY_OBSERVED",
        "description": "Hands pick up the body and the clear piece.",
        "object_ids": ["Object_1", "Object_6"],
        "time_range_s": [1.0, 4.0]
      }]
    }
    """
    document = parse_analysis(text)
    assert document.objects[0].manipulated is True
    assert document.objects[0].initial_state == "loose on the purple surface"
    box = document.observations[0]
    assert box.object_local_id == "Object_1"
    assert box.timestamp == 0
    assert box.bbox.x == 0.495
    action = document.actions[0]
    assert action.action_type == "interaction"
    assert action.start == 1 and action.end == 4
    assert action.object_local_ids == ["Object_1", "Object_6"]
    assert action.evidence.classification.value == "DIRECTLY_OBSERVED"


def test_list_suffixed_records_keep_objects_actions_and_boxes():
    text = """
    {
      "objects": [],
      "observations": [],
      "actions": [],
      "objects_list": [{
        "object_id": "Object_1",
        "label": "light gray body",
        "initial_state": "loose on purple surface",
        "confidence_as_prediction": 0.82
      }],
      "observations_list": [{
        "timestamp_sec": 0,
        "object_id": "Object_1",
        "bbox": {"x": 0.51, "y": 0.335, "w": 0.16, "h": 0.03},
        "visible": true,
        "occluded": false,
        "epistemic_status": "DIRECTLY_OBSERVED",
        "note": "loose on purple surface"
      }],
      "actions_list": [{
        "action_id": "A1",
        "verb": "pick_up",
        "start_sec": 1,
        "end_sec": 2,
        "actor": "both hands",
        "object_ids": ["Object_1"],
        "outcome": "completed",
        "uncertainty": 0.3,
        "epistemic_status": "DIRECTLY_OBSERVED",
        "evidence": "At 0s the body is loose."
      }],
      "relationships_list": [{
        "subject": "Object_6",
        "relation": "seated_at_end_of",
        "object": "Object_1",
        "from_sec": 3,
        "epistemic_status": "INFERRED"
      }],
      "ambiguities_list": [{
        "issue": "Object_3 versus Object_4 identity",
        "epistemic_status": "UNKNOWN",
        "detail": "The two wheels could be swapped."
      }],
      "steps": [{
        "step_id": "Step_1",
        "action": "insert",
        "target_objects": ["Object_6", "Object_1"],
        "time_window_sec": [1, 4],
        "epistemic_status": "INFERRED",
        "unclear_reason": "Fingers cover the meeting end."
      }]
    }
    """
    document = parse_analysis(text)
    assert document.objects[0].local_id == "Object_1"
    assert document.objects[0].confidence == 0.82
    assert document.observations[0].bbox.w == 0.16
    assert document.observations[0].description == "loose on purple surface"
    assert document.observations[0].evidence.classification.value == "DIRECTLY_OBSERVED"
    action = document.actions[0]
    assert action.local_id == "A1"
    assert action.action_type == "pick_up"
    assert action.start == 1 and action.end == 2
    assert action.description == "At 0s the body is loose."
    assert document.relationships[0].kind.value == "attached"
    assert document.relationships[0].time_start == 3
    assert document.ambiguities[0].kind.value == "uncertain_identity"
    assert "swapped" in document.ambiguities[0].description
    step = document.workflow_steps[0]
    assert step.name == "insert"
    assert step.time_start == 1 and step.time_end == 4
    assert step.required_objects == ["Object_6", "Object_1"]
    assert step.observed_result == "Fingers cover the meeting end."


def test_time_range_list_is_kept():
    text = """
    {
      "actions_structured": [{
        "action_id": "Action_1",
        "actor": "both_hands",
        "description": "Pick up the light body piece and the small translucent piece.",
        "evidence": {
          "classification": "DIRECTLY_OBSERVED",
          "note": "Both loose pieces leave the surface in the hands.",
          "provenance": "AI_PREDICTION"
        },
        "object_local_ids": ["Object_1", "Object_6"],
        "time_range": [0.8, 2.0],
        "uncertainty": 0.25,
        "verb": "pick_up"
      }, {
        "action_id": "Action_11",
        "actor": "both_hands",
        "description": "Rotate the finished assembly.",
        "evidence": {
          "classification": "DIRECTLY_OBSERVED",
          "note": "The hands turn the assembly.",
          "provenance": "AI_PREDICTION"
        },
        "object_local_ids": ["Object_1"],
        "time_range": [18.0, 21.935],
        "verb": "rotate"
      }]
    }
    """
    document = parse_analysis(text)
    assert [(item.local_id, item.action_type, item.start, item.end) for item in document.actions] == [
        ("Action_1", "pick_up", 0.8, 2.0),
        ("Action_11", "rotate", 18.0, 21.935),
    ]
    assert document.actions[0].object_local_ids == ["Object_1", "Object_6"]
    assert document.actions[0].evidence.classification.value == "DIRECTLY_OBSERVED"
    assert document.actions[0].evidence.time_start == 0.8
    assert document.actions[0].evidence.time_end == 2.0
    assert document.actions[0].uncertainty == 0.25


def test_labeled_actions_with_nested_times_and_step_ids_are_kept():
    text = """
    {
      "actions_structured": [{
        "action_id": "Action_2",
        "action_label": "Insert small clear part into light gray body",
        "classification": "INFERRED",
        "duration_endpoints": {
          "start_time": {"kind": "reported", "timestamp": 2.0},
          "end_time": {"kind": "derived", "timestamp": 3.0}
        },
        "evidence": {"note": "The clear part meets the gray body.", "provenance": "AI_PREDICTION"}
      }, {
        "action_id": "Action_4",
        "action_label": "Attach black branched part to gray body assembly",
        "classification": "INFERRED",
        "duration_endpoints": {
          "start_time": {"timestamp": 5.0},
          "end_time": {"timestamp": 7.0}
        },
        "evidence": {"note": "The black part stays with the gray body.", "provenance": "AI_PREDICTION"}
      }, {
        "action_id": "Action_11",
        "action_label": "Rotate and inspect assembled parts",
        "classification": "DIRECTLY_OBSERVED",
        "duration_endpoints": {
          "start_time": {"timestamp": 17.0},
          "end_time": {"timestamp": 21.9}
        },
        "evidence": {"note": "The hands turn the assembly.", "provenance": "AI_PREDICTION"}
      }],
      "steps": ["Step_1", "Step_2", "Step_6"],
      "dependencies_structured": [{
        "from_step": "Step_1",
        "to_step": "Step_2",
        "action_ids": ["Action_2", "Action_4"],
        "dependency_type": "observed_temporal",
        "evidence": {"note": "The clear part was joined first.", "provenance": "AI_PREDICTION"}
      }, {
        "from_step": "Step_2",
        "to_step": "Step_6",
        "action_ids": ["Action_4", "Action_11"],
        "dependency_type": "observed_temporal",
        "evidence": {"note": "The assembly was turned after the black part was joined.", "provenance": "AI_PREDICTION"}
      }]
    }
    """
    document = parse_analysis(text)
    assert [(item.local_id, item.action_type, item.start, item.end) for item in document.actions] == [
        ("Action_2", "insert", 2.0, 3.0),
        ("Action_4", "attach", 5.0, 7.0),
        ("Action_11", "rotate", 17.0, 21.9),
    ]
    assert document.actions[0].description == "Insert small clear part into light gray body"
    assert [item.local_id for item in document.workflow_steps] == ["Step_1", "Step_2", "Step_6"]
    assert document.workflow_steps[0].action_local_ids == ["Action_2"]
    assert document.workflow_steps[1].action_local_ids == ["Action_4"]
    assert document.workflow_steps[2].name == "Rotate and inspect assembled parts"
    assert [(item.from_step, item.to_step) for item in document.dependencies] == [
        ("Step_1", "Step_2"),
        ("Step_2", "Step_6"),
    ]

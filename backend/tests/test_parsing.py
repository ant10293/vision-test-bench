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

import pytest
from app.pipeline.parsing import ParseFailure, extract_json_object


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

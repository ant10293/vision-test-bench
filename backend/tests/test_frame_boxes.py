from app.pipeline.frame_boxes import (
    frame_box_rejection,
    frame_boxes_usable,
    merge_frame_boxes,
    parse_frame_boxes,
    square_normalized,
)
from app.schemas.analysis import AnalysisDocument, BBox, Evidence, ObservationPrediction
from app.schemas.enums import EvidenceClass, Provenance


def _obs(object_id: str, timestamp: float, w: float, h: float, x: float = 0.2, y: float = 0.2) -> ObservationPrediction:
    return ObservationPrediction(
        object_local_id=object_id,
        timestamp=timestamp,
        bbox=BBox(x=x, y=y, w=w, h=h),
        evidence=Evidence(
            classification=EvidenceClass.DIRECTLY_OBSERVED,
            provenance=Provenance.AI_PREDICTION,
        ),
    )


def test_square_fractions_on_a_tall_frame_need_another_pass():
    boxes = [_obs("Object_1", float(index), 0.05, 0.05) for index in range(8)]
    assert square_normalized(boxes, 2160, 3840)


def test_portrait_sized_boxes_stay():
    boxes = [_obs("Object_1", float(index), 0.08, 0.045) for index in range(8)]
    assert square_normalized(boxes, 2160, 3840) is False


def test_a_short_square_answer_is_not_copied_onto_a_tall_frame():
    boxes = [_obs("Object_1", float(index), 0.05, 0.05) for index in range(3)]
    assert square_normalized(boxes, 2160, 3840) is False
    assert frame_boxes_usable(boxes, 2160, 3840) is False


def test_a_square_frame_can_have_square_boxes():
    boxes = [_obs("Object_1", float(index), 0.05, 0.05) for index in range(8)]
    assert square_normalized(boxes, 1000, 1000) is False


def test_pixel_corners_become_fractions_of_the_frame():
    text = '{"observations":[{"object_local_id":"Object_1","timestamp":0,"bbox":{"x":940,"y":1318,"w":442,"h":250}}]}'
    document = parse_frame_boxes(text, 2160, 3840)
    box = document.observations[0].bbox
    assert box is not None
    assert round(box.x, 3) == round(940 / 2160, 3)
    assert round(box.y, 3) == round(1318 / 3840, 3)


def test_a_box_shared_by_three_parts_is_not_copied():
    boxes = [_obs(f"Object_{index}", 0.0, 0.08, 0.045, x=0.4, y=0.3) for index in range(1, 4)]
    assert frame_box_rejection(boxes, 2160, 3840, {item.object_local_id for item in boxes})


def test_an_absent_part_clears_the_video_box():
    original = AnalysisDocument(observations=[_obs("Object_1", 1.0, 0.08, 0.045)])
    fresh = AnalysisDocument(
        observations=[
            ObservationPrediction(
                object_local_id="Object_1",
                timestamp=1.0,
                bbox=None,
                visibility="absent",
                confidence=None,
                evidence=Evidence(classification=EvidenceClass.DIRECTLY_OBSERVED, provenance=Provenance.AI_PREDICTION),
            )
        ]
    )
    merged = merge_frame_boxes(original, fresh)
    assert merged.observations[0].bbox is None
    assert merged.observations[0].confidence is None


def test_two_ids_on_one_rectangle_keep_the_higher_score():
    original = AnalysisDocument(
        observations=[
            _obs("Object_1", 1.0, 0.08, 0.045, x=0.4, y=0.3),
            _obs("Object_2", 1.0, 0.08, 0.045, x=0.4, y=0.3),
        ]
    )
    original.observations[0].confidence = 0.4
    original.observations[1].confidence = 0.8
    fresh = AnalysisDocument(observations=original.observations)
    merged = merge_frame_boxes(original, fresh)
    kept = [item for item in merged.observations if item.bbox is not None]
    assert [item.object_local_id for item in kept] == ["Object_2"]


def test_frame_boxes_replace_the_same_moment_only():
    original = AnalysisDocument(observations=[_obs("Object_1", 1.0, 0.05, 0.05), _obs("Object_1", 2.0, 0.05, 0.05)])
    fresh = AnalysisDocument(observations=[_obs("Object_1", 1.0, 0.09, 0.05, x=0.4, y=0.3)])
    merged = merge_frame_boxes(original, fresh)
    first, second = merged.observations
    assert first.bbox is not None and (first.bbox.x, first.bbox.w, first.bbox.h) == (0.4, 0.09, 0.05)
    assert second.bbox is not None and second.bbox.w == 0.05


def test_frame_box_contract_only_allows_the_roster_ids():
    from app.pipeline.contracts import validate_strict_schema
    from app.pipeline.frame_boxes import FRAME_BOX_SCHEMA, frame_box_schema

    schema = frame_box_schema(["Object_2", "Object_3"])
    validate_strict_schema(schema)
    fields = schema["properties"]["observations"]["items"]["properties"]
    assert fields["object_local_id"]["enum"] == ["Object_2", "Object_3"]
    assert fields["visibility"]["enum"] == ["visible", "partial", "occluded", "absent"]
    assert FRAME_BOX_SCHEMA["properties"]["observations"]["items"]["properties"]["object_local_id"] == {
        "type": "string"
    }


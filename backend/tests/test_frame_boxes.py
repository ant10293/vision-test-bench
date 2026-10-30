from app.pipeline.frame_boxes import frame_boxes_usable, merge_frame_boxes, square_normalized
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


def test_frame_boxes_replace_the_same_moment_only():
    original = AnalysisDocument(observations=[_obs("Object_1", 1.0, 0.05, 0.05), _obs("Object_1", 2.0, 0.05, 0.05)])
    fresh = AnalysisDocument(observations=[_obs("Object_1", 1.0, 0.09, 0.05, x=0.4, y=0.3)])
    merged = merge_frame_boxes(original, fresh)
    first, second = merged.observations
    assert first.bbox is not None and (first.bbox.x, first.bbox.w, first.bbox.h) == (0.4, 0.09, 0.05)
    assert second.bbox is not None and second.bbox.w == 0.05

"""Image-plane scale, turn, and depth order. None of these are world coordinates."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from app.perception.geometry import (
    annotate_geometry,
    geometry_records,
    held_turns,
)
from app.perception.ground import apply_geometry
from app.perception.types import DepthUnavailable, GeometryReport, SpatialReport, TrackPoint
from app.pipeline.deterministic_doc import document_from_spatial
from app.schemas.analysis import (
    ActionPrediction,
    AnalysisDocument,
    Coverage,
    Evidence,
    ObjectPrediction,
    PerceptionRecord,
    RelationshipPrediction,
    TransitionPrediction,
    WorkflowStepPrediction,
)
from app.schemas.enums import EvidenceClass, Provenance, RelationKind
from app.verification.learn import learn_template
from app.verification.predicates import judge_predicate
from app.verification.read import STRONG_RELATIONS, signature
from app.verification.schema import RoleAssignment

SIZE = 320


def _texture(seed: int, size: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 255, (size // 8, size // 8), dtype=np.uint8)
    return cv2.resize(noise, (size, size), interpolation=cv2.INTER_NEAREST)


def _write(tmp_path, images: list[np.ndarray]) -> tuple[list, list[float]]:
    paths, times = [], []
    for index, image in enumerate(images):
        path = tmp_path / f"frame_{index:03d}.png"
        cv2.imwrite(str(path), image)
        paths.append(path)
        times.append(round(index * 0.5, 3))
    return paths, times


def _point(ref: str, timestamp: float, box, visibility: str = "visible", chunk: int = 0) -> TrackPoint:
    x, y, w, h = box
    return TrackPoint(
        track_ref=ref,
        timestamp=timestamp,
        x=x,
        y=y,
        w=w,
        h=h,
        motion_score=0.0,
        continuity="continuous",
        camera_compensated=False,
        scene_chunk=chunk,
        visibility=visibility,
    )


def _rotating_part(angles: list[float]) -> list[np.ndarray]:
    part = _texture(3, 90)
    frames = []
    for angle in angles:
        canvas = np.full((SIZE, SIZE), 128, dtype=np.uint8)
        matrix = cv2.getRotationMatrix2D((45, 45), angle, 1.0)
        turned = cv2.warpAffine(part, matrix, (90, 90), borderValue=128)
        canvas[115:205, 115:205] = turned
        frames.append(cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR))
    return frames


CENTER_BOX = (0.33, 0.33, 0.34, 0.34)


def test_fixed_camera_turn_is_measured_from_the_first_full_view(tmp_path):
    angles = [0, 5, 10, 20, 30, 45, 60, 75, 90]
    paths, times = _write(tmp_path, _rotating_part(angles))
    points = [_point("t1", time, CENTER_BOX) for time in times]
    annotate_geometry(points, paths, times, "fixed")
    measured = [point.angle_deg for point in points]
    assert measured[0] == 0.0
    for angle, value in zip(angles, measured, strict=True):
        # OpenCV angles turn counter-clockwise on screen; the trace is positive clockwise.
        assert value == pytest.approx(-angle, abs=1.0)


def test_jitter_does_not_accumulate_into_a_turn(tmp_path):
    paths, times = _write(tmp_path, _rotating_part([0, 2, 0, -2, 0, 2, 0, -2, 0]))
    points = [_point("t1", time, CENTER_BOX) for time in times]
    annotate_geometry(points, paths, times, "fixed")
    assert max(abs(point.angle_deg) for point in points) < 3.0
    assert held_turns(points) == []


def test_head_mounted_camera_rotation_is_not_reported_as_a_turned_part(tmp_path):
    scene = _texture(7, SIZE)
    frames = []
    for angle in [0, 3, 6, 9, 12]:
        matrix = cv2.getRotationMatrix2D((SIZE / 2, SIZE / 2), angle, 1.0)
        frames.append(cv2.cvtColor(cv2.warpAffine(scene, matrix, (SIZE, SIZE), borderMode=cv2.BORDER_REFLECT), cv2.COLOR_GRAY2BGR))
    paths, times = _write(tmp_path, frames)
    points = [_point("t1", time, CENTER_BOX) for time in times]
    annotate_geometry(points, paths, times, "head_mounted")
    measured = [point.angle_deg for point in points]
    assert all(value is not None for value in measured)
    assert max(abs(value) for value in measured) < 2.5


def _part_on_scene(angles: list[float | None]) -> list[np.ndarray]:
    """A turning part on a textured still scene. None is a blank frame."""
    scene = _texture(7, SIZE)
    part = _texture(3, 90)
    frames = []
    for angle in angles:
        if angle is None:
            frames.append(np.full((SIZE, SIZE, 3), 128, dtype=np.uint8))
            continue
        canvas = scene.copy()
        matrix = cv2.getRotationMatrix2D((45, 45), angle, 1.0)
        canvas[115:205, 115:205] = cv2.warpAffine(part, matrix, (90, 90), borderMode=cv2.BORDER_REFLECT)
        frames.append(cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR))
    return frames


def test_one_blank_frame_skips_a_sample_and_keeps_the_trace(tmp_path):
    paths, times = _write(tmp_path, _part_on_scene([0, 8, 16, None, 24, 24]))
    points = [_point("t1", time, CENTER_BOX) for index, time in enumerate(times) if index != 3]
    annotate_geometry(points, paths, times, "head_mounted")
    assert all(point.angle_deg is not None for point in points)
    assert abs(points[-1].angle_deg + 24) < 1.0


def test_a_long_unestimated_stretch_ends_the_turn_trace(tmp_path):
    paths, times = _write(tmp_path, _part_on_scene([0, 5, 10, None, None, None, None, None, 15, 20]))
    points = [_point("t1", time, CENTER_BOX) for index, time in enumerate(times) if index not in range(3, 8)]
    annotate_geometry(points, paths, times, "head_mounted")
    assert [point.angle_deg is not None for point in points] == [True, True, True, False, False]


def test_a_held_turn_on_a_moving_camera_is_inferred(tmp_path):
    angles = [0, 8, 16, 24, 24, 24]
    for viewpoint, expected in (("fixed", "DIRECTLY_OBSERVED"), ("head_mounted", "INFERRED")):
        paths, times = _write(tmp_path, _part_on_scene(angles))
        points = [_point("t1", time, CENTER_BOX) for time in times]
        report = annotate_geometry(points, paths, times, viewpoint)
        transitions, _ = geometry_records(report, {"t1": "Object_1"})
        assert [item.to_state for item in transitions] == ["image_plane_turned"]
        assert transitions[0].evidence.classification.value == expected


def test_partial_and_border_boxes_store_no_scale_or_angle(tmp_path):
    paths, times = _write(tmp_path, _rotating_part([0, 0, 0, 0, 0, 0]))
    points = [
        _point("t1", times[0], (0.30, 0.30, 0.20, 0.20)),
        _point("t1", times[1], (0.30, 0.30, 0.20, 0.20)),
        _point("t1", times[2], (0.30, 0.30, 0.20, 0.20)),
        _point("t1", times[3], (0.30, 0.30, 0.40, 0.40), visibility="partial"),
        _point("t1", times[4], (0.00, 0.30, 0.20, 0.20)),
        _point("t1", times[5], (0.30, 0.30, 0.28, 0.28)),
    ]
    annotate_geometry(points, paths, times, "fixed")
    assert [point.relative_scale for point in points[:3]] == [pytest.approx(1.0)] * 3
    assert points[3].relative_scale is None and points[3].angle_deg is None
    assert points[4].relative_scale is None and points[4].angle_deg is None
    assert points[5].relative_scale == pytest.approx(0.28 * 0.28 / 0.04)


def _angles(ref: str, values: list[float]) -> list[TrackPoint]:
    points = []
    for index, value in enumerate(values):
        point = _point(ref, index * 0.5, CENTER_BOX)
        point.angle_deg = value
        points.append(point)
    return points


def test_a_held_turn_is_one_transition_and_a_wobble_is_none():
    turned = held_turns(_angles("t1", [0, 5, 22, 24, 23, 25]))
    assert [(item.timestamp, item.to_state) for item in turned] == [(1.0, "image_plane_turned")]
    assert held_turns(_angles("t2", [0, 10, 15, 12, 14])) == []
    assert held_turns(_angles("t3", [0, 25, 5, 25, 5, 25])) == []
    back = held_turns(_angles("t4", [0, 30, 31, 30, 2, 1, 3]))
    assert [item.to_state for item in back] == ["image_plane_turned", "image_plane_initial"]


class _FakeDepth:
    depth_id = "fake_depth"

    def __init__(self, values: np.ndarray):
        self.values = values
        self.calls = 0

    def predict(self, image):
        self.calls += 1
        return self.values


def _gradient() -> np.ndarray:
    return np.tile(np.linspace(1.0, 0.0, SIZE, dtype=np.float32), (SIZE, 1))


def _blank(tmp_path, count: int = 2):
    return _write(tmp_path, [np.full((SIZE, SIZE, 3), 100, dtype=np.uint8) for _ in range(count)])


def test_fixed_camera_overlap_gets_a_depth_order(tmp_path):
    paths, times = _blank(tmp_path)
    depth = _FakeDepth(_gradient())
    points = []
    for time in times:
        points.append(_point("left", time, (0.10, 0.30, 0.40, 0.30)))
        points.append(_point("right", time, (0.40, 0.30, 0.40, 0.30)))
    report = annotate_geometry(points, paths, times, "fixed", depth=depth)
    assert depth.calls == 2
    assert {(order.nearer, order.farther) for order in report.orders} == {("left", "right")}


def test_no_depth_order_on_a_head_mounted_camera_or_a_small_margin(tmp_path):
    paths, times = _blank(tmp_path)
    depth = _FakeDepth(_gradient())
    side = [_point("a", times[0], (0.10, 0.30, 0.40, 0.30)), _point("b", times[0], (0.40, 0.30, 0.40, 0.30))]
    assert annotate_geometry(side, paths, times, "head_mounted", depth=depth).orders == []
    assert depth.calls == 0
    stacked = [_point("a", times[0], (0.30, 0.20, 0.20, 0.30)), _point("b", times[0], (0.30, 0.40, 0.20, 0.30))]
    assert annotate_geometry(stacked, paths, times, "fixed", depth=depth).orders == []


def test_a_failed_depth_model_is_recorded_and_not_replaced(tmp_path):
    class Broken:
        depth_id = "broken"

        def predict(self, image):
            raise DepthUnavailable("weights missing")

    paths, times = _blank(tmp_path)
    points = [_point("a", times[0], (0.10, 0.30, 0.40, 0.30)), _point("b", times[0], (0.40, 0.30, 0.40, 0.30))]
    report = annotate_geometry(points, paths, times, "fixed", depth=Broken())
    assert report.depth_status == "unavailable"
    assert report.orders == []


def _spatial_report(points, geometry) -> SpatialReport:
    return SpatialReport(
        viewpoint="fixed",
        task_constraint="constrained",
        research_mode="fixed_camera",
        global_motion=0.0,
        scene_change_count=0,
        camera_motion_compensated=False,
        compensation_model="none",
        coordinate_frame="image",
        world_coordinates_supported=False,
        notes="",
        tracks=points,
        perception=PerceptionRecord(strategy="temporal", detector_id="proposals"),
        geometry=geometry,
    )


def test_turns_and_depth_order_reach_a_model_document_once(tmp_path):
    paths, times = _blank(tmp_path)
    points = []
    for time in times:
        points.append(_point("track_1", time, (0.10, 0.30, 0.40, 0.30)))
        points.append(_point("track_2", time, (0.40, 0.30, 0.40, 0.30)))
    geometry = annotate_geometry(points, paths, times, "fixed", depth=_FakeDepth(_gradient()))
    geometry.turns = held_turns(_angles("track_1", [0, 30, 31, 30]))
    spatial = document_from_spatial(_spatial_report(points, geometry), Coverage(modality="sampled_frames", sampling_preset="standard"))
    model = AnalysisDocument(
        objects=[
            ObjectPrediction(local_id="Bracket", label="bracket", instance_id="track_1"),
            ObjectPrediction(local_id="Plate", label="plate", instance_id="track_2"),
        ]
    )
    once = apply_geometry(model, spatial)
    twice = apply_geometry(once, spatial)
    assert [(item.object_local_id, item.to_state) for item in twice.transitions] == [("Bracket", "image_plane_turned")]
    fronts = [item for item in twice.relationships if item.kind.value == "in_front_of"]
    assert [(item.subject_local_id, item.other_local_id) for item in fronts] == [("Bracket", "Plate")]
    assert len(twice.relationships) == len(once.relationships)


def test_geometry_records_skip_tracks_without_an_object():
    report = GeometryReport(turns=held_turns(_angles("lost", [0, 30, 31, 30])))
    assert geometry_records(report, {}) == ([], [])


def _evidence(time: float) -> Evidence:
    return Evidence(
        classification=EvidenceClass.DIRECTLY_OBSERVED,
        provenance=Provenance.DETERMINISTIC,
        time_start=time,
        time_end=time,
        frame_timestamps=[time],
    )


def _turn(to_state: str, time: float, from_state: str = "image_plane_initial") -> TransitionPrediction:
    return TransitionPrediction(
        object_local_id="Knob",
        timestamp=time,
        from_state=from_state,
        to_state=to_state,
        evidence=_evidence(time),
    )


def _demo(action_type: str) -> AnalysisDocument:
    return AnalysisDocument(
        objects=[ObjectPrediction(local_id="Knob", label="knob")],
        actions=[
            ActionPrediction(
                local_id="Action_1",
                action_type=action_type,
                start=1.0,
                end=3.0,
                object_local_ids=["Knob"],
                evidence=_evidence(1.0),
            )
        ],
        workflow_steps=[
            WorkflowStepPrediction(
                local_id="Step_1",
                name="Turn the knob",
                required_objects=["Knob"],
                action_local_ids=["Action_1"],
                time_start=1.0,
                time_end=3.0,
                evidence=_evidence(1.0),
            )
        ],
        transitions=[_turn("image_plane_turned", 2.0)],
    )


def test_a_turn_is_required_only_for_a_rotation_step():
    rotate = learn_template(_demo("rotate")).steps[0].completion_conditions
    pick = learn_template(_demo("pick_up")).steps[0].completion_conditions
    assert [item.evidence_requirement for item in rotate] == ["required"]
    assert [item.evidence_requirement for item in pick] == ["optional"]


def test_a_turn_check_reads_turn_records_only():
    predicate = learn_template(_demo("rotate")).steps[0].completion_conditions[0]
    roles = {predicate.subject_role: RoleAssignment(role_id=predicate.subject_role, status="assigned", object_local_ids=["Knob"])}
    unrelated = AnalysisDocument(
        objects=[ObjectPrediction(local_id="Knob", label="knob")],
        transitions=[_turn("visible", 2.0, from_state="hidden")],
    )
    judged = judge_predicate(predicate, unrelated, roles, video_id="v", attempted=False)
    assert judged.status == "cannot"
    turned = unrelated.model_copy(update={"transitions": [_turn("image_plane_turned", 2.5)]})
    assert judge_predicate(predicate, turned, roles, video_id="v", attempted=False).status == "pass"
    returned = unrelated.model_copy(
        update={"transitions": [_turn("image_plane_turned", 2.5), _turn("image_plane_initial", 4.0, from_state="image_plane_turned")]}
    )
    failed = judge_predicate(predicate, returned, roles, video_id="v", attempted=False)
    assert failed.status == "fail" and failed.error_category == "INCORRECT_PHYSICAL_STATE"
    inferred = _turn("image_plane_turned", 2.5)
    inferred.evidence.classification = EvidenceClass.INFERRED
    head_mounted = unrelated.model_copy(update={"transitions": [inferred]})
    assert judge_predicate(predicate, head_mounted, roles, video_id="v", attempted=False).status == "cannot"


def test_depth_order_is_not_a_placement_or_a_role_signature(tmp_path):
    assert "in_front_of" not in STRONG_RELATIONS
    paths, times = _blank(tmp_path, 1)
    points = [_point("track_1", times[0], (0.10, 0.30, 0.40, 0.30)), _point("track_2", times[0], (0.40, 0.30, 0.40, 0.30))]
    geometry = annotate_geometry(points, paths, times, "fixed", depth=_FakeDepth(_gradient()))
    spatial = document_from_spatial(_spatial_report(points, geometry), Coverage(modality="sampled_frames", sampling_preset="standard"))
    assert any(item.kind.value == "in_front_of" for item in spatial.relationships)
    assert not any(entry.startswith("in_front_of") for entry in signature(spatial, "Object_1"))


def test_object_boxes_get_their_own_scale_turn_and_depth_order(tmp_path, monkeypatch):
    from app.config import settings
    from app.perception.object_geometry import ObjectBox, object_geometry

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    paths, times = _write(tmp_path, _part_on_scene([0, 8, 16, 24]))
    frames = [(time, path.name) for time, path in zip(times, paths)]
    center = {"x": 0.33, "y": 0.33, "w": 0.34, "h": 0.34}
    boxes = [ObjectBox("Knob", time + 0.002, center, "visible") for time in times]
    boxes.append(ObjectBox("Panel", times[0] + 0.002, {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4}, "partial"))
    result = object_geometry("run_1", boxes, frames, "head_mounted", {"perception": {"depth": None}})
    knob = [item for item in result["boxes"] if item["object_local_id"] == "Knob"]
    assert [item["timestamp"] for item in knob] == [time + 0.002 for time in times]
    assert abs(knob[-1]["angle_deg"] + 24) < 1.0
    assert all(item["object_local_id"] != "Panel" for item in result["boxes"])
    assert result["depth_status"] == "skipped"
    assert object_geometry("run_1", boxes, frames, "head_mounted", None)["signature"] == result["signature"]


def test_partial_object_boxes_enter_depth_order(tmp_path, monkeypatch):
    from app.config import settings
    from app.perception.object_geometry import ObjectBox, measure_boxes

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    paths, times = _blank(tmp_path, 1)
    boxes = [
        ObjectBox("Left", times[0], {"x": 0.10, "y": 0.30, "w": 0.40, "h": 0.30}, "partial"),
        ObjectBox("Right", times[0], {"x": 0.40, "y": 0.30, "w": 0.40, "h": 0.30}, "partial"),
    ]
    result = measure_boxes(boxes, [(times[0], paths[0].name)], "fixed", None, depth=_FakeDepth(_gradient()))
    assert [(item["nearer"], item["farther"]) for item in result["orders"]] == [("Left", "Right")]


def _in_front(time: float) -> RelationshipPrediction:
    return RelationshipPrediction(
        subject_local_id="Knob",
        other_local_id="Plate",
        kind=RelationKind.IN_FRONT_OF,
        time_start=time,
        time_end=time,
        evidence=_evidence(time),
    )


def test_a_clear_depth_order_is_a_check_and_a_missing_rank_cannot_verify():
    demo = _demo("place").model_copy(
        update={
            "objects": [ObjectPrediction(local_id="Knob", label="knob"), ObjectPrediction(local_id="Plate", label="plate")],
            "transitions": [],
            "relationships": [_in_front(2.0)],
        }
    )
    demo.workflow_steps[0].required_objects = ["Knob", "Plate"]
    demo.actions[0].object_local_ids = ["Knob", "Plate"]
    [predicate] = learn_template(demo).steps[0].completion_conditions
    assert predicate.relation == "in_front_of" and predicate.evidence_requirement == "required"
    roles = {
        predicate.subject_role: RoleAssignment(role_id=predicate.subject_role, status="assigned", object_local_ids=["Knob"]),
        predicate.target_role: RoleAssignment(role_id=predicate.target_role, status="assigned", object_local_ids=["Plate"]),
    }
    missing = AnalysisDocument(objects=demo.objects)
    assert judge_predicate(predicate, missing, roles, video_id="v", attempted=False).status == "cannot"
    ranked = missing.model_copy(update={"relationships": [_in_front(2.5)]})
    assert judge_predicate(predicate, ranked, roles, video_id="v", attempted=False).status == "pass"


def test_a_turned_instance_notes_that_its_appearance_can_differ(tmp_path):
    paths, times = _write(tmp_path, _rotating_part([0, 8, 16, 24]))
    points = [_point("t1", time, CENTER_BOX) for time in times]
    annotate_geometry(points, paths, times, "fixed")
    assert ["appearance can differ" in point.note for point in points] == [False, False, False, True]

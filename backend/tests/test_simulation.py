"""Scene schema, kinematics, constraints, and counterfactual pairs. No Blender."""

import json

import numpy as np
import pytest
from app.simulation.dataset import DatasetError, validate_dataset
from app.simulation.evaluate import (
    evaluate_scene,
    events_from_scene,
    order_violations,
    workflow_from_scene,
)
from app.simulation.math3d import camera_matrix, intrinsics, project_point
from app.simulation.presets import build_assembly
from app.simulation.scoring import analysis_payload, assert_independent
from app.simulation.visibility import classify_visibility, justified_verdict, observability
from pydantic import ValidationError


def _final(scene):
    return evaluate_scene(scene, scene.sample_times()[-1])


def _satisfied(scene, constraint_id: str) -> bool:
    state = _final(scene)
    return next(item["satisfied"] for item in state["relations"] if item["constraint_id"] == constraint_id)


def test_duplicate_instance_ids_are_rejected():
    scene = build_assembly()
    raw = scene.model_dump()
    raw["objects"].append(raw["objects"][0])
    with pytest.raises(ValidationError):
        scene.__class__.model_validate(raw)


def test_projection_of_the_look_target_is_the_principal_point():
    eye = np.array([0.0, -0.62, 0.34])
    target = np.array([0.0, 0.0, 0.04])
    matrix = camera_matrix(eye, target)
    camera = intrinsics(320, 240, 42.0)
    projected = project_point(matrix, camera, target)
    assert projected is not None
    assert abs(projected["u"] - 160) < 1e-6
    assert abs(projected["v"] - 120) < 1e-6
    assert projected["depth_m"] > 0


def test_a_point_behind_the_camera_is_not_projected():
    matrix = camera_matrix(np.array([0.0, -1.0, 0.2]), np.array([0.0, 0.0, 0.2]))
    camera = intrinsics(320, 240, 40.0)
    assert project_point(matrix, camera, np.array([0.0, -2.0, 0.2])) is None


def test_correct_assembly_seats_every_constraint():
    scene = build_assembly("correct", seed=3)
    assert scene.kinematic is True
    assert scene.rigid_body is False
    final = _final(scene)
    for constraint_id, expected in scene.expectation.final_constraints.items():
        got = next(item for item in final["relations"] if item["constraint_id"] == constraint_id)
        assert got["satisfied"] is expected
        assert got["unresolved"] is False
    assert order_violations(scene) == []


def test_the_bolt_is_not_inserted_at_the_start():
    scene = build_assembly("correct")
    state = evaluate_scene(scene, 0.0)
    bolt = next(item for item in state["relations"] if item["constraint_id"] == "bolt_in_plate")
    assert bolt["satisfied"] is False


def test_missing_washer_is_absent_and_not_seated():
    scene = build_assembly("missing_washer")
    washer = next(item for item in scene.objects if item.instance_id == "washer")
    assert washer.omitted is True
    state = _final(scene)
    washer_state = next(item for item in state["objects"] if item["instance_id"] == "washer")
    assert washer_state["present"] is False
    relation = next(item for item in state["relations"] if item["constraint_id"] == "washer_on_bolt")
    assert relation["satisfied"] is False
    assert "absent" in relation["detail"]
    assert _satisfied(scene, "bolt_in_plate") is True


def test_incorrect_placement_keeps_the_washer_present():
    scene = build_assembly("incorrect_placement")
    state = _final(scene)
    washer = next(item for item in state["objects"] if item["instance_id"] == "washer")
    assert washer["present"] is True
    assert _satisfied(scene, "washer_on_bolt") is False
    assert _satisfied(scene, "bolt_in_plate") is True


def test_a_corrected_mistake_fails_and_then_passes():
    scene = build_assembly("corrected_mistake")
    events = events_from_scene(scene)
    misplace = next(item for item in events if item["id"] == "washer_misplace")
    placed = next(item for item in events if item["id"] == "washer_place")
    assert misplace["outcome"] == "not_achieved"
    assert placed["outcome"] == "achieved"
    assert _satisfied(scene, "washer_on_bolt") is True


def test_order_violation_is_kept_when_the_final_state_is_seated():
    scene = build_assembly("order_violation")
    assert _satisfied(scene, "washer_on_bolt") is True
    assert _satisfied(scene, "nut_on_bolt") is True
    violations = order_violations(scene)
    assert violations
    assert workflow_from_scene(scene)["order_violations"]


def test_identical_washers_keep_distinct_ids():
    scene = build_assembly("identity_swap")
    washers = [item for item in scene.objects if item.label == "washer"]
    assert len(washers) == 2
    assert washers[0].instance_id != washers[1].instance_id
    assert washers[0].material.color == washers[1].material.color
    assert _satisfied(scene, "washer_on_bolt") is True


def test_moving_the_camera_does_not_move_the_plate():
    fixed = build_assembly("correct", "fixed", seed=4)
    moving = build_assembly("correct", "constrained_head", seed=4)
    roaming = build_assembly("correct", "unconstrained_head", seed=4)
    t0 = fixed.sample_times()[0]
    t1 = fixed.sample_times()[len(fixed.sample_times()) // 2]
    def plate(scene, timestamp):
        state = evaluate_scene(scene, timestamp)
        return next(item["position"] for item in state["objects"] if item["instance_id"] == "plate")
    assert plate(moving, t0) == plate(moving, t1)
    assert plate(fixed, t1) == plate(roaming, t1)
    c0 = evaluate_scene(moving, t0)["camera"]["position"]
    c1 = evaluate_scene(moving, t1)["camera"]["position"]
    assert c0 != c1
    assert evaluate_scene(fixed, t0)["camera"]["position"] == evaluate_scene(fixed, t1)["camera"]["position"]


def test_counterfactual_pair_shares_everything_except_the_washer():
    correct = build_assembly("correct", "constrained_head", seed=9, randomize=True)
    missing = build_assembly("missing_washer", "constrained_head", seed=9, randomize=True)
    assert correct.pair_key == missing.pair_key
    assert correct.camera.model_dump() == missing.camera.model_dump()
    assert [item.model_dump() for item in correct.operations if item.object_id == "bolt"] == [
        item.model_dump() for item in missing.operations if item.object_id == "bolt"
    ]
    correct_plate = next(item for item in correct.objects if item.instance_id == "plate")
    missing_plate = next(item for item in missing.objects if item.instance_id == "plate")
    assert correct_plate.model_dump() == missing_plate.model_dump()
    assert any(item.object_id == "washer" for item in correct.operations)
    assert all(item.object_id != "washer" for item in missing.operations)
    assert next(item for item in missing.objects if item.instance_id == "washer").omitted is True


def test_the_same_seed_rebuilds_the_same_scene():
    left = build_assembly("correct", seed=11, randomize=True)
    right = build_assembly("correct", seed=11, randomize=True)
    assert left.model_dump() == right.model_dump()
    other = build_assembly("correct", seed=12, randomize=True)
    assert left.model_dump() != other.model_dump()


def test_occlusion_is_not_physical_absence():
    assert classify_visibility(True, 0, True) == "fully_occluded"
    assert classify_visibility(False, 0, False) == "absent"
    assert classify_visibility(True, 0, False) == "outside_fov"
    assert observability("fully_occluded", "fully_occluded") == "unknown"
    assert observability("absent", "fully_visible") == "observable"
    assert observability("absent", "fully_occluded") == "not_observable"
    assert justified_verdict(False, True, "observable") == "FAIL"
    assert justified_verdict(False, True, "not_observable") == "CANNOT_VERIFY"
    assert justified_verdict(True, True, "unknown") == "CANNOT_VERIFY"
    assert justified_verdict(True, False, "observable") == "NOT_APPLICABLE"


def test_analysis_payload_does_not_carry_the_scene():
    payload = analysis_payload("video-1", "fixed_camera")
    assert "scene" not in payload
    assert payload["provider_id"] == "deterministic"
    blob = json.dumps(payload)
    assert "washer" not in blob
    assert "inserted_into" not in blob


def test_fixture_provider_cannot_be_scored_as_independent():
    class Run:
        fixture = True
        provider_id = "fixture"
        config_json = {}

    with pytest.raises(ValueError):
        assert_independent(Run())


def test_manifest_rejects_an_absolute_path(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {"scene": "/tmp/scene.json"}}))
    with pytest.raises(DatasetError):
        validate_dataset(tmp_path)


def test_pipeline_modules_do_not_import_simulation():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    for relative in ("pipeline/infer.py", "pipeline/runner.py", "perception/tracker.py", "providers/registry.py"):
        text = (root / relative).read_text()
        assert "app.simulation" not in text


def test_an_experiment_stores_only_whether_the_scene_is_skipped():
    from types import SimpleNamespace

    from app.pipeline.infer import StageSkipped
    from app.pipeline.scene_stage import render_blender_scene
    from app.services.experiments import scene_flag

    assert scene_flag({}) == {"skip": True}
    assert scene_flag({"scene": {"skip": True}, "video_id": "clip"}) == {"skip": True}
    kept = scene_flag({"scene": {"skip": False, "variant": "missing_washer", "seed": 9}})
    assert kept == {"skip": False}
    blob = json.dumps(kept)
    assert "missing_washer" not in blob
    assert "ground_truth" not in blob
    assert "inserted_into" not in blob
    run = SimpleNamespace(config_json={}, research_mode="fixed_camera")
    with pytest.raises(StageSkipped, match="skipped"):
        render_blender_scene(None, run, None)
    run.config_json = {"scene": {"skip": True}}
    with pytest.raises(StageSkipped, match="skipped"):
        render_blender_scene(None, run, None)


def test_a_tracked_region_lands_on_its_box_in_the_video():
    from app.simulation.evaluate import evaluate_scene
    from app.simulation.from_video import FOV_X_DEG, scene_from_tracks
    from app.simulation.math3d import camera_matrix, intrinsics, project_point

    tracks = [
        {"track_ref": "region-1", "timestamp": 0.0, "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.25}},
        {"track_ref": "region-1", "timestamp": 1.0, "bbox": {"x": 0.55, "y": 0.15, "w": 0.2, "h": 0.2}},
        {"track_ref": "region-2", "timestamp": 0.5, "bbox": {"x": 0.4, "y": 0.6, "w": 0.15, "h": 0.15}},
    ]
    scene = scene_from_tracks(tracks, width=640, height=360, fps=10, duration=2.0, scene_id="clip")
    assert scene.variant == "tracked"
    assert scene.constraints == []
    assert {item.instance_id for item in scene.objects} == {"region-1", "region-2"}
    blob = scene.model_dump_json()
    assert "washer" not in blob
    assert "bolt" not in blob
    camera = camera_matrix(
        np.array(scene.camera.keys[0].eye, dtype=float),
        np.array(scene.camera.keys[0].target, dtype=float),
    )
    pixels = intrinsics(scene.width, scene.height, FOV_X_DEG)

    def center_at(instance_id: str, timestamp: float, bbox: dict) -> None:
        state = evaluate_scene(scene, timestamp)
        placed = next(item for item in state["objects"] if item["instance_id"] == instance_id)
        projected = project_point(camera, pixels, np.array(placed["position"], dtype=float))
        assert projected is not None
        expected_u = (bbox["x"] + bbox["w"] / 2) * scene.width
        expected_v = (bbox["y"] + bbox["h"] / 2) * scene.height
        assert abs(projected["u"] - expected_u) < 1e-4
        assert abs(projected["v"] - expected_v) < 1e-4

    center_at("region-1", 0.0, tracks[0]["bbox"])
    center_at("region-1", 1.0, tracks[1]["bbox"])
    center_at("region-2", 0.5, tracks[2]["bbox"])
    by_id = {item.instance_id: item for item in scene.objects}
    larger = by_id["region-1"]
    smaller = by_id["region-2"]
    assert larger.pieces[0].size[1] >= min(larger.pieces[0].size[0], larger.pieces[0].size[2]) * 0.5
    assert larger.position[2] == pytest.approx(0.0)
    assert smaller.position[2] == pytest.approx(0.0)
    assert larger.rotation_deg[0] == pytest.approx(-90.0)
    assert scene.camera.keys[0].eye[2] > abs(scene.camera.keys[0].eye[0]) + abs(scene.camera.keys[0].eye[1])


def test_each_research_mode_keeps_the_objects_on_one_floor():
    from app.simulation.evaluate import evaluate_scene
    from app.simulation.from_video import FOV_X_DEG, scene_from_tracks
    from app.simulation.math3d import camera_matrix, intrinsics, project_point, rotation_matrix

    tracks = [
        {"track_ref": "region-1", "timestamp": 0.0, "bbox": {"x": 0.3, "y": 0.3, "w": 0.2, "h": 0.15}},
    ]
    for mode, overhead in (
        ("fixed_camera", True),
        ("constrained_head_mounted", False),
        ("unconstrained_head_mounted", False),
    ):
        scene = scene_from_tracks(
            tracks, width=640, height=360, fps=8, duration=1.0, scene_id=mode, research_mode=mode
        )
        obj = scene.objects[0]
        assert obj.position[2] == pytest.approx(0.0)
        assert obj.rotation_deg[0] == pytest.approx(-90.0)
        facing = rotation_matrix(obj.rotation_deg) @ np.array([0.0, 1.0, 0.0])
        assert facing[2] == pytest.approx(-1.0, abs=1e-6)
        eye = scene.camera.keys[0].eye
        assert (eye[2] > abs(eye[0]) + abs(eye[1])) is overhead
        camera = camera_matrix(np.array(eye, dtype=float), np.array(scene.camera.keys[0].target, dtype=float))
        projected = project_point(camera, intrinsics(scene.width, scene.height, FOV_X_DEG), np.array(obj.position))
        assert projected is not None
        bbox = tracks[0]["bbox"]
        assert abs(projected["u"] - (bbox["x"] + bbox["w"] / 2) * scene.width) < 1e-4
        assert abs(projected["v"] - (bbox["y"] + bbox["h"] / 2) * scene.height) < 1e-4
    state = evaluate_scene(
        scene_from_tracks(
            [
                {
                    "track_ref": "region-1",
                    "timestamp": 0.0,
                    "bbox": {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.3},
                    "oriented": {"cx": 0.45, "cy": 0.4, "w": 0.22, "h": 0.08, "angle_deg": 25.0},
                },
                {
                    "track_ref": "region-1",
                    "timestamp": 1.0,
                    "bbox": {"x": 0.3, "y": 0.3, "w": 0.3, "h": 0.2},
                    "oriented": {"cx": 0.5, "cy": 0.5, "w": 0.18, "h": 0.07, "angle_deg": 40.0},
                },
            ],
            width=640,
            height=360,
            fps=8,
            duration=1.0,
            scene_id="turned",
        ),
        1.0,
    )
    placed = state["objects"][0]
    assert placed["position"][2] == pytest.approx(0.0)
    turned = rotation_matrix((-90.0, 0.0, 40.0))
    assert np.allclose(np.array(placed["rotation"]).reshape(3, 3), turned, atol=1e-6)


def test_a_rotated_box_follows_the_subject_instead_of_the_frame_edges():
    from app.simulation.textures import _warp_box, box_from_alpha

    horizontal = np.zeros((80, 140), dtype=np.uint8)
    horizontal[34:46, 15:125] = 255
    flat = box_from_alpha(horizontal, 0, 0, 140, 80, {"x": 0, "y": 0, "w": 1, "h": 1})
    assert abs(flat["angle_deg"]) < 8
    assert flat["w"] > flat["h"] * 2

    diagonal = np.zeros((120, 120), dtype=np.uint8)
    for offset in range(-4, 5):
        for step in range(20, 100):
            row = min(119, max(0, step + offset))
            col = min(119, max(0, step))
            diagonal[row, col] = 255
    turned = box_from_alpha(diagonal, 0, 0, 120, 120, {"x": 0, "y": 0, "w": 1, "h": 1})
    assert abs(abs(turned["angle_deg"]) - 45) < 12 or abs(abs(turned["angle_deg"]) - 135) < 12
    assert turned["w"] * turned["h"] < 0.5

    image = np.zeros((40, 80, 3), dtype=np.uint8)
    image[2, 8] = (0, 0, 255)
    warped = _warp_box(image, {"cx": 0.5, "cy": 0.5, "w": 1.0, "h": 1.0, "angle_deg": 0.0})
    red_rows = warped[:, :, 2].max(axis=1)
    assert int(np.argmax(red_rows)) < warped.shape[0] / 2


def test_a_portrait_frame_stays_even_so_the_encoder_can_open_it():
    from app.simulation.from_video import scene_from_tracks

    tracks = [{"track_ref": "region-1", "timestamp": 0.0, "bbox": {"x": 0.2, "y": 0.2, "w": 0.2, "h": 0.3}}]
    scene = scene_from_tracks(tracks, width=1080, height=1920, fps=30, duration=12, scene_id="portrait")
    assert scene.width % 2 == 0
    assert scene.height % 2 == 0
    assert (scene.width, scene.height) == (406, 720)
    assert scene.fps == 24
    assert scene.duration == 12


def test_the_scene_uses_analysis_objects_and_ignores_motion_tracks():
    from types import SimpleNamespace

    from app.simulation.from_video import records_from_document, scene_from_tracks

    def box(x, y):
        payload = {"x": x, "y": y, "w": 0.2, "h": 0.2}
        return SimpleNamespace(**payload, model_dump=lambda payload=payload: dict(payload))

    document = SimpleNamespace(
        objects=[
            SimpleNamespace(local_id="Object_1", label="cup", accepted=True),
            SimpleNamespace(local_id="Object_2", label="spoon", accepted=False),
        ],
        observations=[
            SimpleNamespace(
                object_local_id="Object_1",
                timestamp=0.0,
                bbox=box(0.1, 0.2),
                visibility="visible",
                accepted=True,
                track_ref="track_35",
            ),
            SimpleNamespace(
                object_local_id="Object_2",
                timestamp=0.0,
                bbox=box(0.6, 0.6),
                visibility="visible",
                accepted=True,
                track_ref="track_2",
            ),
            SimpleNamespace(
                object_local_id="track_99",
                timestamp=0.0,
                bbox=box(0.4, 0.4),
                visibility="visible",
                accepted=True,
                track_ref="track_99",
            ),
        ],
    )
    scene = scene_from_tracks(
        records_from_document(document),
        width=320,
        height=240,
        fps=8,
        duration=2,
        scene_id="objects",
    )
    assert [(item.instance_id, item.label) for item in scene.objects] == [("Object_1", "cup")]
    assert "track_" not in scene.model_dump_json()


def test_a_crop_covers_the_object_box():
    from app.simulation.textures import crop_normalized

    image = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)
    crop = crop_normalized(image, {"x": 0.25, "y": 0.10, "w": 0.50, "h": 0.20})
    assert crop.shape == (20, 100, 3)
    assert np.array_equal(crop[0, 0], image[10, 50])


def test_the_subject_is_kept_and_the_surrounding_color_is_clear():
    from app.simulation.textures import subject_rgba

    image = np.full((80, 80, 3), (210, 180, 220), np.uint8)
    image[24:56, 24:56] = (40, 40, 45)
    result = subject_rgba(image)
    assert result.shape == (80, 80, 4)
    assert result[0, 0, 3] == 0
    assert result[40, 40, 3] == 255
    assert np.array_equal(result[40, 40, :3], image[40, 40])


def test_a_crop_with_no_separate_subject_stays_opaque():
    from app.simulation.textures import subject_rgba

    image = np.full((40, 40, 3), (180, 160, 200), np.uint8)
    result = subject_rgba(image)
    assert int(result[:, :, 3].min()) == 255


def test_a_video_with_no_tracks_does_not_invent_an_assembly():
    from app.simulation.from_video import scene_from_tracks

    with pytest.raises(ValueError, match="no object boxes"):
        scene_from_tracks([], width=320, height=240, fps=8, duration=2.0, scene_id="empty")


def test_scene_round_trip():
    scene = build_assembly("occluded_washer", "unconstrained_head", seed=2, duration=4, fps=4, width=160, height=120)
    restored = scene.__class__.model_validate_json(scene.model_dump_json())
    assert restored.sample_times()[0] == 0
    assert len(restored.sample_times()) == restored.frame_count()
    assert _satisfied(restored, "washer_on_bolt") is True
    cover = next(item for item in _final(restored)["objects"] if item["instance_id"] == "cover")
    assert cover["present"] is True

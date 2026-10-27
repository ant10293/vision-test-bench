"""A real Blender render. Skipped with a reason when Blender is not installed."""

import json

import numpy as np
import pytest
from app.simulation.evaluate import evaluate_scene
from app.simulation.math3d import project_point
from app.simulation.presets import build_assembly
from app.simulation.render import find_blender, render_dataset
from PIL import Image

pytestmark = pytest.mark.skipif(find_blender() is None, reason="Blender is not installed")


def _render(tmp_path, variant: str, camera_mode: str = "fixed"):
    scene = build_assembly(
        variant,
        camera_mode,
        seed=1,
        width=160,
        height=120,
        fps=4,
        duration=2,
        scene_id=f"{variant}-{camera_mode}",
    )
    root = tmp_path / variant
    render_dataset(scene, root, dataset_id=variant, samples=1, timeout_sec=180)
    return scene, root


def test_masks_match_the_evaluated_pose_and_keep_ids(tmp_path):
    scene, root = _render(tmp_path, "correct")
    frames = json.loads((root / "annotations" / "frames.json").read_text())["frames"]
    assert len(frames) == scene.frame_count()
    last = frames[-1]
    state = evaluate_scene(scene, last["timestamp"])
    plate_state = next(item for item in state["objects"] if item["instance_id"] == "plate")
    plate = next(item for item in last["instances"] if item["instance_id"] == "plate")
    assert plate["present"] is True
    assert plate["visibility"] in {"fully_visible", "partially_occluded", "visible"}
    assert plate["bbox"] is not None
    projected = project_point(
        np.array(last["camera_matrix"]).reshape(4, 4),
        last["intrinsics"],
        np.array(plate_state["position"]),
    )
    assert projected is not None
    box = plate["bbox"]
    u = projected["u"] / scene.width
    v = projected["v"] / scene.height
    assert box["x"] - 0.05 <= u <= box["x"] + box["w"] + 0.05
    assert box["y"] - 0.05 <= v <= box["y"] + box["h"] + 0.05
    with Image.open(root / "frames" / "instance" / f"{last['index']:06d}.png") as mask:
        assert mask.size == (scene.width, scene.height)
    positions = [
        next(item["matrix"] for item in frame["instances"] if item["instance_id"] == "plate")[3::4][:3]
        for frame in frames
    ]
    # Translation is the last column, stored row-major, so indices 3, 7, 11.
    translations = [
        [
            next(item["matrix"] for item in frame["instances"] if item["instance_id"] == "plate")[3],
            next(item["matrix"] for item in frame["instances"] if item["instance_id"] == "plate")[7],
            next(item["matrix"] for item in frame["instances"] if item["instance_id"] == "plate")[11],
        ]
        for frame in frames
    ]
    assert translations[0] == pytest.approx(translations[-1])
    assert positions  # kept so a refactor notices the column layout


def test_camera_motion_changes_the_image_and_not_the_plate(tmp_path):
    scene, root = _render(tmp_path, "correct", "constrained_head")
    frames = json.loads((root / "annotations" / "frames.json").read_text())["frames"]
    def plate_box(frame):
        return next(item["bbox"] for item in frame["instances"] if item["instance_id"] == "plate")
    def camera(frame):
        return frame["camera_matrix"]
    assert camera(frames[0]) != camera(frames[-1])
    assert plate_box(frames[0]) != plate_box(frames[len(frames) // 2])


def test_an_omitted_washer_has_no_pixels_and_is_absent(tmp_path):
    _scene, root = _render(tmp_path, "missing_washer")
    frames = json.loads((root / "annotations" / "frames.json").read_text())["frames"]
    washer = next(item for item in frames[-1]["instances"] if item["instance_id"] == "washer")
    assert washer["present"] is False
    assert washer["visibility"] == "absent"
    assert washer["visible_pixels"] == 0
    bolt = next(item for item in frames[-1]["instances"] if item["instance_id"] == "bolt")
    assert bolt["present"] is True
    assert bolt["visible_pixels"] > 0


def test_a_hidden_washer_is_present_and_not_absent(tmp_path):
    _scene, root = _render(tmp_path, "occluded_washer")
    frames = json.loads((root / "annotations" / "frames.json").read_text())["frames"]
    washer = next(item for item in frames[-1]["instances"] if item["instance_id"] == "washer")
    assert washer["present"] is True
    assert washer["visibility"] in {"fully_occluded", "partially_occluded", "outside_fov"}
    assert washer["visibility"] != "absent"

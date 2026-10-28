"""Observed scene graph. Leaving the frame does not delete an object, and labels do not merge places."""

from pathlib import Path

import cv2
from app.demo.scenarios import SCENARIOS, render_scenario
from app.perception.presets import preset
from app.perception.scene_graph import consider_segment_link, graph_from_tracks
from app.video.sampling import measure_motion
from app.video.spatial import analyze_sequence


def _point(track, timestamp, continuity="continuous", compensated=True, status="tracked"):
    return {
        "track_ref": track,
        "timestamp": timestamp,
        "continuity": continuity,
        "camera_compensated": compensated,
        "status": status,
    }


def test_a_gap_keeps_the_object_and_does_not_call_it_removed():
    points = [
        _point("track-a", 0.0),
        _point("track-a", 0.2),
        _point("track-a", 2.4, continuity="uncertain"),
    ]
    graph = graph_from_tracks(points, [{"ordinal": 0, "time_start": 0, "time_end": 2.4, "scene_change": False}])
    node = graph["nodes"][0]
    assert node["removed"] is False
    assert node["instance_id"] == "track-a"
    assert any(item["status"] == "unobserved" and item["completed"] is False for item in node["intervals"])
    assert graph["world_coordinates_supported"] is False
    assert graph["metric_scale_known"] is False
    assert all(item["pose"] is None for item in graph["camera_estimates"])


def test_shared_labels_do_not_merge_segments():
    refused = consider_segment_link(["washer", "washer"], None)
    assert refused["status"] == "unresolved"
    proposed = consider_segment_link(["washer"], 0.4)
    assert proposed["status"] == "proposed"


def test_affine_compensation_is_not_a_pose():
    graph = graph_from_tracks([_point("track-a", 0.0, compensated=False)], compensation_model="affine_partial")
    estimate = graph["camera_estimates"][0]
    assert estimate["kind"] == "image_partial_affine"
    assert estimate["six_dof"] is False
    assert estimate["pose"] is None
    assert estimate["tracking_lost"] is True
    assert estimate["metric_scale_known"] is False


def test_unconstrained_fixture_is_tracked_without_inventing_a_world_frame(tmp_path: Path):
    scenario = SCENARIOS["unconstrained_head_mounted"]()
    frames = render_scenario(scenario)[::5]
    paths = []
    for index, frame in enumerate(frames):
        path = tmp_path / f"{index:04d}.png"
        cv2.imwrite(str(path), frame)
        paths.append(path)
    timestamps = [index * 0.5 for index in range(len(paths))]
    signals = measure_motion(paths, timestamps)
    report = analyze_sequence(
        paths,
        timestamps,
        signals,
        "unconstrained_head_mounted",
        config=preset("A_legacy_motion"),
    )
    graph = graph_from_tracks(report.tracks, report.chunks, report.compensation_model)
    assert report.world_coordinates_supported is False
    assert graph["world_coordinates_supported"] is False
    assert graph["metric_scale_known"] is False
    assert graph["coordinate_frame"] == "image"
    seen = {point.track_ref for point in report.tracks}
    kept = {node["instance_id"] for node in graph["nodes"]}
    assert seen == kept
    assert all(node["removed"] is False for node in graph["nodes"])
    assert all(item.get("pose") is None for item in graph["camera_estimates"])

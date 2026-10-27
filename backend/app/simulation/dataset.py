"""Dataset files, checksums, and structural checks. Paths inside the manifest are relative."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from PIL import Image

from app.simulation import DATASET_SCHEMA_VERSION, GENERATOR_VERSION
from app.simulation.evaluate import (
    evaluate_scene,
    events_from_scene,
    relation_map,
    workflow_from_scene,
)
from app.simulation.schema import Scene
from app.simulation.visibility import justified_verdict, observability

RELATIVE_FILES = {
    "scene": "scene.json",
    "video": "videos/execution.mp4",
    "frames": "annotations/frames.json",
    "objects": "annotations/objects.json",
    "events": "annotations/events.json",
    "relationships": "annotations/relationships.json",
    "workflow": "annotations/workflow.json",
    "observability": "annotations/observability.json",
}


class DatasetError(Exception):
    pass


def id_color(index: int) -> list[int]:
    """Stable RGB id. Neighbors differ by 32, so a 4-level edge cannot change identity."""
    if index < 1:
        raise ValueError("instance color index starts at 1")
    return [
        ((index % 7) + 1) * 32,
        (((index // 7) % 7) + 1) * 32,
        (((index // 49) % 7) + 1) * 32,
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2))
    temporary.replace(path)


def write_scene_files(scene: Scene, root: Path) -> dict[str, list[int]]:
    colors = {obj.instance_id: id_color(index) for index, obj in enumerate(scene.objects, start=1)}
    _write(root / "scene.json", json.loads(scene.model_dump_json()))
    _write(
        root / "annotations" / "objects.json",
        {
            "objects": [
                {
                    "instance_id": obj.instance_id,
                    "label": obj.label,
                    "description": obj.description,
                    "omitted": obj.omitted,
                    "id_color": colors[obj.instance_id],
                    "material": obj.material.model_dump(),
                    "pieces": [piece.model_dump() for piece in obj.pieces],
                }
                for obj in scene.objects
            ]
        },
    )
    _write(root / "annotations" / "events.json", {"events": events_from_scene(scene), "source": "scene_evaluator"})
    final = evaluate_scene(scene, scene.sample_times()[-1])
    _write(
        root / "annotations" / "relationships.json",
        {"timestamp": final["timestamp"], "relations": final["relations"], "source": "geometric_constraints"},
    )
    _write(root / "annotations" / "workflow.json", workflow_from_scene(scene))
    return colors


def write_observability(scene: Scene, root: Path) -> dict:
    frames = json.loads((root / "annotations" / "frames.json").read_text())["frames"]
    if len(frames) != scene.frame_count():
        raise DatasetError("frame annotations do not match the scene frame count")
    last = frames[-1]
    visibility = {item["instance_id"]: item["visibility"] for item in last["instances"]}
    state = evaluate_scene(scene, float(last["timestamp"]))
    relations = relation_map(state)
    conditions = []
    for constraint in scene.constraints:
        relation = relations[constraint.id]
        required = any(constraint.id in step.constraint_ids and step.required for step in scene.procedure)
        view = observability(
            visibility.get(constraint.subject_id, "absent"),
            visibility.get(constraint.host_id, "absent"),
        )
        conditions.append(
            {
                "constraint_id": constraint.id,
                "relation": constraint.relation,
                "timestamp": last["timestamp"],
                "satisfied": relation["satisfied"],
                "subject_visibility": visibility.get(constraint.subject_id, "absent"),
                "host_visibility": visibility.get(constraint.host_id, "absent"),
                "observability": view,
                "justified_verdict": justified_verdict(relation["satisfied"], required, view),
                "required": required,
                "detail": relation["detail"],
            }
        )
    payload = {
        "source": "rendered_visibility",
        "note": (
            "justified_verdict is what the picture supports. "
            "It is not a claim that a model reached that verdict."
        ),
        "conditions": conditions,
    }
    _write(root / "annotations" / "observability.json", payload)
    return payload


def write_manifest(root: Path, scene: Scene, blender_version: str, engine: str, dataset_id: str) -> dict:
    checksums = {}
    for name in ("scene.json", "annotations/frames.json", "videos/execution.mp4"):
        checksums[name] = sha256_file(root / name)
    frames = json.loads((root / "annotations" / "frames.json").read_text())
    manifest = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "dataset_id": dataset_id,
        "scene_id": scene.scene_id,
        "revision": scene.revision,
        "seed": scene.seed,
        "variant": scene.variant,
        "camera_mode": scene.camera_mode,
        "pair_key": scene.pair_key,
        "intervention": scene.intervention,
        "width": scene.width,
        "height": scene.height,
        "fps": scene.fps,
        "duration": scene.duration,
        "frame_count": scene.frame_count(),
        "coordinate_system": scene.coordinate_system,
        "image_coordinates": "top_left_normalized",
        "depth_convention": "not_exported",
        "kinematic": True,
        "rigid_body": False,
        "blender_version": blender_version,
        "engine": engine,
        "files": RELATIVE_FILES,
        "checksums": checksums,
        "ground_truth": "simulator",
        "status": "complete",
    }
    if len(frames["frames"]) != scene.frame_count():
        raise DatasetError("manifest frame count does not match annotations")
    _write(root / "manifest.json", manifest)
    return manifest


def validate_dataset(root: Path) -> None:
    problems: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise DatasetError("manifest.json is missing")
    manifest = json.loads(manifest_path.read_text())
    blob = json.dumps(manifest)
    if '": "/' in blob or "://" in blob:
        problems.append("manifest contains an absolute or URL path")
    for name, relative in (manifest.get("files") or {}).items():
        if not isinstance(relative, str) or relative.startswith("/") or ".." in relative.split("/"):
            problems.append(f"{name} is not a relative path")
            continue
        if not (root / relative).exists():
            problems.append(f"missing {relative}")
    for relative, digest in (manifest.get("checksums") or {}).items():
        path = root / relative
        if path.exists() and sha256_file(path) != digest:
            problems.append(f"checksum mismatch for {relative}")
    if problems:
        raise DatasetError("; ".join(problems))
    scene_path = root / "scene.json"
    if not scene_path.exists():
        raise DatasetError("scene.json is missing")
    scene = Scene.model_validate_json(scene_path.read_text())
    frames = json.loads((root / "annotations" / "frames.json").read_text())["frames"]
    if len(frames) != scene.frame_count():
        problems.append("annotation count does not match the scene")
    known = {obj.instance_id for obj in scene.objects}
    rgb = root / "frames" / "rgb"
    masks = root / "frames" / "instance"
    for expected, frame in zip(scene.sample_times(), frames, strict=False):
        if abs(float(frame["timestamp"]) - expected) > 1e-4:
            problems.append(f"frame {frame.get('index')} timestamp does not match the scene clock")
        if not math.isfinite(float(frame["timestamp"])):
            problems.append("a frame timestamp is not finite")
        for value in frame.get("camera_matrix") or []:
            if not math.isfinite(float(value)):
                problems.append("camera matrix is not finite")
                break
        seen = set()
        for instance in frame.get("instances") or []:
            if instance["instance_id"] not in known:
                problems.append(f"unknown instance {instance['instance_id']}")
            seen.add(instance["instance_id"])
            matrix = instance.get("matrix") or []
            if len(matrix) != 16 or any(not math.isfinite(float(value)) for value in matrix):
                problems.append(f"transform for {instance['instance_id']} is not finite")
        if seen != known:
            problems.append(f"frame {frame.get('index')} does not list every instance")
        index = int(frame["index"])
        rgb_path = rgb / f"{index:06d}.png"
        mask_path = masks / f"{index:06d}.png"
        if not rgb_path.exists() or not mask_path.exists():
            problems.append(f"frame {index} is missing a render")
            continue
        with Image.open(rgb_path) as rgb_image, Image.open(mask_path) as mask_image:
            if rgb_image.size != (scene.width, scene.height) or mask_image.size != rgb_image.size:
                problems.append(f"frame {index} size does not match the scene")
    events = json.loads((root / "annotations" / "events.json").read_text())["events"]
    for event in events:
        if event["start"] < -1e-6 or event["end"] > scene.duration + 1e-4:
            problems.append(f"event {event['id']} is outside the recording")
        if event["object_id"] not in known:
            problems.append(f"event {event['id']} names an unknown object")
    if problems:
        raise DatasetError("; ".join(problems))

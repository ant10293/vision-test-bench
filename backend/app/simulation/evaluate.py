"""Deterministic kinematic scene state. This is not a rigid-body simulation."""

from __future__ import annotations

import numpy as np

from app.simulation.constraints import evaluate_constraint
from app.simulation.math3d import (
    camera_matrix,
    interpolate_pose,
    intrinsics,
    matrix_to_quaternion,
    pose_matrix,
    rotation_matrix,
    smoothstep,
)
from app.simulation.schema import CameraRig, Operation, Scene, SceneObject


def object_matrix(position: tuple[float, float, float], rotation_deg: tuple[float, float, float]) -> np.ndarray:
    return pose_matrix(np.asarray(position, dtype=float), rotation_matrix(rotation_deg))


def _pose_at(obj: SceneObject, operations: list[Operation], timestamp: float) -> np.ndarray:
    pose = object_matrix(obj.position, obj.rotation_deg)
    for motion in sorted(operations, key=lambda item: item.start):
        if timestamp <= motion.start:
            break
        end = object_matrix(motion.position, motion.rotation_deg)
        if timestamp >= motion.end:
            pose = end
            continue
        amount = (timestamp - motion.start) / (motion.end - motion.start)
        pose = interpolate_pose(pose, end, amount)
        break
    return pose


def _present(obj: SceneObject, timestamp: float) -> bool:
    return not obj.omitted and timestamp >= -1e-9


def _camera_pose(rig: CameraRig, timestamp: float) -> np.ndarray:
    keys = sorted(rig.keys, key=lambda item: item.t)
    if timestamp <= keys[0].t:
        return camera_matrix(np.asarray(keys[0].eye, float), np.asarray(keys[0].target, float), keys[0].roll_deg)
    if timestamp >= keys[-1].t:
        last = keys[-1]
        return camera_matrix(np.asarray(last.eye, float), np.asarray(last.target, float), last.roll_deg)
    for earlier, later in zip(keys, keys[1:], strict=False):
        if earlier.t <= timestamp <= later.t:
            amount = smoothstep((timestamp - earlier.t) / (later.t - earlier.t))
            eye = (1.0 - amount) * np.asarray(earlier.eye, float) + amount * np.asarray(later.eye, float)
            target = (1.0 - amount) * np.asarray(earlier.target, float) + amount * np.asarray(later.target, float)
            roll = (1.0 - amount) * earlier.roll_deg + amount * later.roll_deg
            return camera_matrix(eye, target, roll)
    raise ValueError("camera keys did not cover the timestamp")


def evaluate_scene(scene: Scene, timestamp: float) -> dict:
    if timestamp < -1e-8 or timestamp > scene.duration + 1e-6:
        raise ValueError("timestamp is outside the scene")
    timestamp = min(scene.duration, max(0.0, timestamp))
    by_object: dict[str, list[Operation]] = {}
    for motion in scene.operations:
        by_object.setdefault(motion.object_id, []).append(motion)
    objects = []
    matrices: dict[str, np.ndarray] = {}
    present: dict[str, bool] = {}
    for obj in scene.objects:
        matrix = _pose_at(obj, by_object.get(obj.instance_id, []), timestamp)
        is_present = _present(obj, timestamp)
        matrices[obj.instance_id] = matrix
        present[obj.instance_id] = is_present
        objects.append(
            {
                "instance_id": obj.instance_id,
                "label": obj.label,
                "present": is_present,
                "matrix": matrix.reshape(-1).tolist(),
                "position": matrix[:3, 3].tolist(),
                "quaternion": matrix_to_quaternion(matrix[:3, :3]).tolist(),
                "rotation": matrix[:3, :3].reshape(-1).tolist(),
            }
        )
    relations = [evaluate_constraint(item, matrices, present) for item in scene.constraints]
    camera = _camera_pose(scene.camera, timestamp)
    active = [
        motion.id
        for motion in scene.operations
        if motion.start - 1e-9 <= timestamp <= motion.end + 1e-9
    ]
    step_id = None
    for step in scene.procedure:
        if step.start - 1e-9 <= timestamp <= step.end + 1e-9:
            step_id = step.id
    return {
        "timestamp": timestamp,
        "kinematic": True,
        "rigid_body": False,
        "objects": objects,
        "relations": relations,
        "active_operations": active,
        "active_step_id": step_id,
        "camera": {
            "mode": scene.camera.mode,
            "matrix": camera.reshape(-1).tolist(),
            "position": camera[:3, 3].tolist(),
            "intrinsics": intrinsics(scene.width, scene.height, scene.camera.fov_x_deg),
            "world_coordinates_are_simulator_truth": True,
        },
    }


def relation_map(state: dict) -> dict[str, dict]:
    return {item["constraint_id"]: item for item in state["relations"]}


def events_from_scene(scene: Scene) -> list[dict]:
    """Structural events from the operations, judged at their end time."""
    events = []
    for motion in scene.operations:
        state = evaluate_scene(scene, motion.end)
        relations = relation_map(state)
        checks = [relations[item] for item in motion.checks]
        unresolved = any(item["unresolved"] for item in checks)
        if not checks:
            outcome = "kinematic"
            achieved = None
        elif unresolved:
            outcome = "unresolved"
            achieved = False
        elif all(item["satisfied"] for item in checks):
            outcome = "achieved"
            achieved = True
        else:
            outcome = "not_achieved"
            achieved = False
        events.append(
            {
                "id": motion.id,
                "kind": motion.kind,
                "object_id": motion.object_id,
                "start": motion.start,
                "end": motion.end,
                "attempted": True,
                "outcome": outcome,
                "achieved": achieved,
                "checks": checks,
                "step_id": _step_for(scene, motion.id),
            }
        )
    return events


def _step_for(scene: Scene, operation_id: str) -> str | None:
    for step in scene.procedure:
        if operation_id in step.operation_ids:
            return step.id
    return None


def order_violations(scene: Scene) -> list[dict]:
    steps = {step.id: step for step in scene.procedure}
    found = []
    for step in scene.procedure:
        if step.order_category != "necessary_physical":
            continue
        state = evaluate_scene(scene, step.start)
        relations = relation_map(state)
        for prior_id in step.after:
            prior = steps[prior_id]
            for constraint_id in prior.constraint_ids:
                relation = relations.get(constraint_id)
                if relation is None or not relation["satisfied"]:
                    found.append(
                        {
                            "step_id": step.id,
                            "predecessor": prior_id,
                            "constraint_id": constraint_id,
                            "timestamp": step.start,
                            "detail": "A necessary predecessor was not satisfied when this step started.",
                        }
                    )
    return found


def workflow_from_scene(scene: Scene) -> dict:
    final = evaluate_scene(scene, scene.sample_times()[-1])
    relations = relation_map(final)
    steps = []
    for step in scene.procedure:
        checks = [relations[item] for item in step.constraint_ids if item in relations]
        steps.append(
            {
                "id": step.id,
                "name": step.name,
                "start": step.start,
                "end": step.end,
                "required": step.required,
                "after": step.after,
                "order_category": step.order_category,
                "constraint_ids": step.constraint_ids,
                "final_checks": checks,
            }
        )
    return {
        "steps": steps,
        "order_violations": order_violations(scene),
        "final_relations": final["relations"],
        "expectation": scene.expectation.model_dump(),
    }

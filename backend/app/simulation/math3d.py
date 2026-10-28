"""Rigid transforms and a pinhole camera.

World space matches Blender: meters, right-handed, Z up, X right, Y forward.
The camera looks along its local -Z. Camera space is X right, Y up, Z backward.
Optical-axis depth is -Z in camera space, in meters, and is positive in front
of the camera. That is not Euclidean distance from the camera to the point.

Image space is top-left origin, x right, y down. A pixel (i, j) covers
[i, i + 1] by [j, j + 1]. Projection returns continuous pixel coordinates.
"""

from __future__ import annotations

import math

import numpy as np

WORLD_UP = np.array([0.0, 0.0, 1.0])


def finite_vector(values: tuple[float, ...] | list[float], length: int) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.shape != (length,) or not np.all(np.isfinite(array)):
        raise ValueError(f"expected {length} finite numbers")
    return array


def rotation_matrix(rotation_deg: tuple[float, float, float] | np.ndarray) -> np.ndarray:
    """Extrinsic XYZ Euler angles in degrees. Local +Z is the mesh axis at identity."""
    rx, ry, rz = np.radians(finite_vector(rotation_deg, 3))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    rot_x = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    rot_y = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    rot_z = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return rot_z @ rot_y @ rot_x


def pose_matrix(position: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = position
    return matrix


def invert_rigid(matrix: np.ndarray) -> np.ndarray:
    rotation = matrix[:3, :3]
    translation = matrix[:3, 3]
    inverse = np.eye(4)
    inverse[:3, :3] = rotation.T
    inverse[:3, 3] = -rotation.T @ translation
    return inverse


def matrix_to_quaternion(rotation: np.ndarray) -> np.ndarray:
    """Returns w, x, y, z."""
    trace = float(np.trace(rotation))
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (rotation[2, 1] - rotation[1, 2]) / scale
        y = (rotation[0, 2] - rotation[2, 0]) / scale
        z = (rotation[1, 0] - rotation[0, 1]) / scale
    elif rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
        scale = math.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2.0
        w = (rotation[2, 1] - rotation[1, 2]) / scale
        x = 0.25 * scale
        y = (rotation[0, 1] + rotation[1, 0]) / scale
        z = (rotation[0, 2] + rotation[2, 0]) / scale
    elif rotation[1, 1] > rotation[2, 2]:
        scale = math.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2.0
        w = (rotation[0, 2] - rotation[2, 0]) / scale
        x = (rotation[0, 1] + rotation[1, 0]) / scale
        y = 0.25 * scale
        z = (rotation[1, 2] + rotation[2, 1]) / scale
    else:
        scale = math.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2.0
        w = (rotation[1, 0] - rotation[0, 1]) / scale
        x = (rotation[0, 2] + rotation[2, 0]) / scale
        y = (rotation[1, 2] + rotation[2, 1]) / scale
        z = 0.25 * scale
    quat = np.array([w, x, y, z], dtype=float)
    return quat / np.linalg.norm(quat)


def quaternion_to_matrix(quaternion: np.ndarray) -> np.ndarray:
    w, x, y, z = quaternion
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def slerp(start: np.ndarray, end: np.ndarray, amount: float) -> np.ndarray:
    left = start / np.linalg.norm(start)
    right = end / np.linalg.norm(end)
    dot = float(np.dot(left, right))
    if dot < 0.0:
        right = -right
        dot = -dot
    if dot > 0.9995:
        mixed = left + amount * (right - left)
        return mixed / np.linalg.norm(mixed)
    theta = math.acos(min(1.0, dot))
    mixed = (math.sin((1.0 - amount) * theta) * left + math.sin(amount * theta) * right) / math.sin(theta)
    return mixed


def interpolate_pose(start: np.ndarray, end: np.ndarray, amount: float) -> np.ndarray:
    """Smoothstep between two rigid matrices. Endpoints are exact."""
    if amount <= 0.0:
        return start.copy()
    if amount >= 1.0:
        return end.copy()
    smooth = amount * amount * (3.0 - 2.0 * amount)
    rotation = quaternion_to_matrix(
        slerp(matrix_to_quaternion(start[:3, :3]), matrix_to_quaternion(end[:3, :3]), smooth)
    )
    position = (1.0 - smooth) * start[:3, 3] + smooth * end[:3, 3]
    return pose_matrix(position, rotation)


def camera_matrix(eye: np.ndarray, target: np.ndarray, roll_deg: float = 0.0) -> np.ndarray:
    """Camera-to-world. Columns are camera X (right), Y (up), and Z (backward)."""
    forward = target - eye
    norm = float(np.linalg.norm(forward))
    if norm < 1e-8:
        raise ValueError("camera eye and target coincide")
    forward = forward / norm
    up = WORLD_UP.copy()
    if abs(float(np.dot(forward, up))) > 0.98:
        up = np.array([0.0, 1.0, 0.0])
    backward = -forward
    right = np.cross(up, backward)
    right_norm = float(np.linalg.norm(right))
    if right_norm < 1e-8:
        raise ValueError("camera up is parallel to the view direction")
    right = right / right_norm
    cam_up = np.cross(backward, right)
    if roll_deg:
        roll = math.radians(roll_deg)
        rolled_right = math.cos(roll) * right + math.sin(roll) * cam_up
        rolled_up = -math.sin(roll) * right + math.cos(roll) * cam_up
        right, cam_up = rolled_right, rolled_up
    return pose_matrix(eye, np.column_stack([right, cam_up, backward]))


def intrinsics(width: int, height: int, fov_x_deg: float) -> dict:
    if width < 2 or height < 2:
        raise ValueError("resolution is too small")
    if not 1.0 < fov_x_deg < 179.0:
        raise ValueError("horizontal field of view must be between 1 and 179 degrees")
    fx = (width / 2.0) / math.tan(math.radians(fov_x_deg) / 2.0)
    fy = fx
    return {
        "fx": fx,
        "fy": fy,
        "cx": width / 2.0,
        "cy": height / 2.0,
        "width": width,
        "height": height,
        "fov_x_deg": fov_x_deg,
        "pixel_origin": "top_left",
        "pixel_convention": "pixel (i, j) covers [i, i+1] x [j, j+1]; projection is continuous",
        "depth": "optical_axis_meters_positive_in_front",
    }


def project_point(camera_to_world: np.ndarray, camera_intrinsics: dict, point: np.ndarray) -> dict | None:
    """Project a world point. None when the point is on or behind the camera plane."""
    camera_point = invert_rigid(camera_to_world)[:3, :3] @ (point - camera_to_world[:3, 3])
    depth = float(-camera_point[2])
    if depth <= 1e-6:
        return None
    u = camera_intrinsics["cx"] + camera_intrinsics["fx"] * (float(camera_point[0]) / depth)
    v = camera_intrinsics["cy"] - camera_intrinsics["fy"] * (float(camera_point[1]) / depth)
    return {"u": u, "v": v, "depth_m": depth}


def normalized_box(x0: float, y0: float, x1: float, y1: float, width: int, height: int) -> dict:
    """Inclusive pixel bounds to a top-left normalized box. Values may fall outside 0..1."""
    return {
        "x": x0 / width,
        "y": y0 / height,
        "w": (x1 - x0 + 1.0) / width,
        "h": (y1 - y0 + 1.0) / height,
    }


def box_intersects_image(box: dict) -> bool:
    return box["w"] > 0 and box["h"] > 0 and box["x"] < 1 and box["y"] < 1 and box["x"] + box["w"] > 0 and box["y"] + box["h"] > 0


def smoothstep(amount: float) -> float:
    clamped = min(1.0, max(0.0, amount))
    return clamped * clamped * (3.0 - 2.0 * clamped)

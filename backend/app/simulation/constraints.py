"""Geometric assembly checks. A box overlap is not one of these."""

from __future__ import annotations

import math

import numpy as np

from app.simulation.schema import Constraint


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-8:
        raise ValueError("axis length is zero")
    return vector / norm


def _transform_point(matrix: np.ndarray, point: tuple[float, float, float]) -> np.ndarray:
    local = np.array([point[0], point[1], point[2], 1.0])
    return (matrix @ local)[:3]


def _transform_direction(matrix: np.ndarray, direction: tuple[float, float, float]) -> np.ndarray:
    return _unit(matrix[:3, :3] @ np.asarray(direction, dtype=float))


def evaluate_constraint(
    constraint: Constraint,
    matrices: dict[str, np.ndarray],
    present: dict[str, bool],
) -> dict:
    if constraint.kind == "unresolved":
        return _result(constraint, satisfied=False, unresolved=True, detail="This relationship has no geometric check.")
    if not present.get(constraint.subject_id, False):
        return _result(constraint, satisfied=False, unresolved=False, detail="The subject is physically absent.")
    if not present.get(constraint.host_id, False):
        return _result(constraint, satisfied=False, unresolved=False, detail="The host is physically absent.")
    try:
        subject = matrices[constraint.subject_id]
        host = matrices[constraint.host_id]
        subject_axis = _transform_direction(subject, constraint.subject_axis)
        host_axis = _transform_direction(host, constraint.host_axis)
        subject_point = _transform_point(subject, constraint.subject_point)
        host_point = _transform_point(host, constraint.host_point)
    except KeyError as exc:
        return _result(constraint, satisfied=False, unresolved=True, detail=f"Missing transform for {exc}.")
    except ValueError as exc:
        return _result(constraint, satisfied=False, unresolved=True, detail=str(exc))
    alignment = abs(float(np.dot(subject_axis, host_axis)))
    angle = math.degrees(math.acos(min(1.0, alignment)))
    offset = subject_point - host_point
    axial = float(np.dot(offset, host_axis))
    radial = float(np.linalg.norm(offset - axial * host_axis))
    aligned = angle <= constraint.max_angle_deg + 1e-6
    centered = radial <= constraint.max_radial_m + 1e-9
    if constraint.kind == "axial_insertion":
        penetration = float(np.dot(host_point - subject_point, host_axis))
        deep_enough = constraint.min_penetration_m - 1e-9 <= penetration <= constraint.max_penetration_m + 1e-9
        satisfied = aligned and centered and deep_enough
        detail = (
            f"angle {angle:.2f} deg, radial {radial:.4f} m, penetration {penetration:.4f} m"
        )
        return _result(constraint, satisfied=satisfied, unresolved=False, detail=detail, measures={
            "angle_deg": angle,
            "radial_m": radial,
            "penetration_m": penetration,
        })
    if constraint.kind == "coaxial_seat":
        seated = constraint.min_axial_m - 1e-9 <= axial <= constraint.max_axial_m + 1e-9
        satisfied = aligned and centered and seated
        detail = f"angle {angle:.2f} deg, radial {radial:.4f} m, axial {axial:.4f} m"
        return _result(constraint, satisfied=satisfied, unresolved=False, detail=detail, measures={
            "angle_deg": angle,
            "radial_m": radial,
            "axial_m": axial,
        })
    return _result(constraint, satisfied=False, unresolved=True, detail=f"Unknown constraint kind {constraint.kind}.")


def _result(constraint: Constraint, satisfied: bool, unresolved: bool, detail: str, measures: dict | None = None) -> dict:
    return {
        "constraint_id": constraint.id,
        "relation": constraint.relation,
        "subject_id": constraint.subject_id,
        "host_id": constraint.host_id,
        "satisfied": satisfied and not unresolved,
        "unresolved": unresolved,
        "detail": detail,
        "measures": measures or {},
    }

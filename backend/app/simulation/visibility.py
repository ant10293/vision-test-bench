"""Visibility from rendered pixels, and whether a condition can be checked by eye."""

from __future__ import annotations


def classify_visibility(present: bool, visible_pixels: int, amodal_intersects: bool) -> str:
    if not present:
        return "absent"
    if visible_pixels > 0 and not amodal_intersects:
        return "partially_occluded"
    if visible_pixels <= 0 and not amodal_intersects:
        return "outside_fov"
    if visible_pixels <= 0:
        return "fully_occluded"
    return "visible"


def visibility_fraction(visible_pixels: int, projected_pixels: int) -> float | None:
    if projected_pixels <= 0:
        return None
    return visible_pixels / projected_pixels


def refine_visibility(present: bool, visible_pixels: int, projected_pixels: int, amodal_intersects: bool) -> str:
    status = classify_visibility(present, visible_pixels, amodal_intersects)
    if status != "visible":
        return status
    fraction = visibility_fraction(visible_pixels, projected_pixels)
    if fraction is None:
        return "visible"
    if fraction >= 0.85:
        return "fully_visible"
    return "partially_occluded"


def observability(subject_visibility: str, host_visibility: str) -> str:
    """Whether a person looking at this frame could check the relationship.

    Returns observable, not_observable, or unknown. Unknown is not a failure.
    """
    hidden = {"fully_occluded", "outside_fov", "absent"}
    if subject_visibility == "absent" and host_visibility in {"fully_visible", "partially_occluded", "visible"}:
        return "observable"
    if subject_visibility in hidden or host_visibility in hidden:
        if subject_visibility == "absent" and host_visibility in hidden:
            return "not_observable"
        return "unknown"
    if subject_visibility in {"fully_visible", "partially_occluded", "visible"} and host_visibility in {
        "fully_visible",
        "partially_occluded",
        "visible",
    }:
        return "observable"
    return "unknown"


def justified_verdict(satisfied: bool, required: bool, view: str) -> str:
    """Evidence-grounded verdict. A hidden truth does not justify PASS or FAIL."""
    if not required:
        return "NOT_APPLICABLE"
    if view == "observable":
        return "PASS" if satisfied else "FAIL"
    if view in {"not_observable", "unknown"}:
        return "CANNOT_VERIFY"
    return "CANNOT_VERIFY"

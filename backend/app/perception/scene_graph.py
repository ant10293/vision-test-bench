"""Persistent object graph for real footage.

Image observations are the baseline. Leaving the frame does not delete an object.
No metric world pose is invented from a box or from a partial affine warp.
"""

from __future__ import annotations


def _get(point, name: str, default=None):
    if isinstance(point, dict):
        return point.get(name, default)
    return getattr(point, name, default)


def graph_from_tracks(points: list, chunks: list | None = None, compensation_model: str = "none") -> dict:
    grouped: dict[str, list] = {}
    for point in points:
        grouped.setdefault(str(_get(point, "track_ref")), []).append(point)
    nodes = []
    for track_ref, rows in grouped.items():
        ordered = sorted(rows, key=lambda item: float(_get(item, "timestamp")))
        nodes.append(_node(track_ref, ordered))
    return {
        "source": "observed",
        "coordinate_frame": "image",
        "world_coordinates_supported": False,
        "metric_scale_known": False,
        "nodes": nodes,
        "segments": _segments(chunks or []),
        "segment_links": [],
        "camera_estimates": _camera(points, compensation_model),
        "notes": (
            "Objects stay in the graph when they leave view. "
            "The partial affine model, when present, is an image-plane warp and not a camera pose."
        ),
    }


def _node(track_ref: str, rows: list) -> dict:
    times = [float(_get(item, "timestamp")) for item in rows]
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    short = [gap for gap in gaps if 0 < gap <= 0.5]
    baseline = sorted(short)[len(short) // 2] if short else 0.25
    threshold = max(0.75, baseline * 3)
    intervals = []
    for index, row in enumerate(rows):
        status = _status(row)
        start = times[index]
        end = times[index]
        intervals.append({"start": start, "end": end, "status": status, "state": "observed" if status == "observed" else "unknown"})
        if index + 1 < len(rows) and times[index + 1] - start > threshold:
            intervals.append(
                {
                    "start": start,
                    "end": times[index + 1],
                    "status": "unobserved",
                    "state": "unknown",
                    "unobserved_interval": True,
                    "completed": False,
                    "detail": "No observation in this gap. The object was not marked removed.",
                }
            )
    last = intervals[-1]["status"] if intervals else "unobserved"
    return {
        "instance_id": track_ref,
        "label": None,
        "status_now": last,
        "first_seen": times[0],
        "last_seen": times[-1],
        "intervals": intervals,
        "removed": False,
    }


def _status(row) -> str:
    continuity = str(_get(row, "continuity") or "")
    lifecycle = str(_get(row, "status") or "")
    if continuity in {"uncertain", "reassociated"} or lifecycle in {"tentative", "ambiguous"}:
        return "tentative"
    return "observed"


def _segments(chunks: list) -> list[dict]:
    segments = []
    for chunk in chunks:
        if isinstance(chunk, dict):
            segments.append(
                {
                    "ordinal": chunk.get("ordinal"),
                    "start": chunk.get("time_start"),
                    "end": chunk.get("time_end"),
                    "scene_change": bool(chunk.get("scene_change")),
                    "frame": "local_image",
                    "merged": False,
                }
            )
    return segments


def _camera(points: list, compensation_model: str) -> list[dict]:
    kind = "image_partial_affine" if compensation_model == "affine_partial" else "unavailable"
    by_time: dict[float, list] = {}
    for point in points:
        by_time.setdefault(round(float(_get(point, "timestamp")), 4), []).append(point)
    if kind == "unavailable":
        return [
            {
                "kind": "unavailable",
                "metric_scale_known": False,
                "six_dof": False,
                "pose": None,
                "tracking_lost": False,
                "detail": "No image-plane compensation was recorded. No metric pose was invented.",
            }
        ]
    estimates = []
    for timestamp, rows in sorted(by_time.items()):
        compensated = any(bool(_get(item, "camera_compensated")) for item in rows)
        estimates.append(
            {
                "timestamp": timestamp,
                "kind": "image_partial_affine",
                "metric_scale_known": False,
                "six_dof": False,
                "pose": None,
                "tracking_lost": not compensated,
                "detail": "Partial affine image warp. Not a 6-DoF camera pose and not a metric world frame.",
            }
        )
    return estimates


def consider_segment_link(shared_labels: list[str], appearance_margin: float | None) -> dict:
    """A shared name does not merge two places or two objects."""
    if appearance_margin is None:
        return {
            "status": "unresolved",
            "reason": "The segments share labels only. That is not enough to merge them.",
            "labels": shared_labels,
        }
    if appearance_margin < 0.2:
        return {
            "status": "unresolved",
            "reason": "The appearance margin is too small to relate these segments.",
            "labels": shared_labels,
        }
    return {
        "status": "proposed",
        "reason": "An appearance margin cleared the gate. The link is not confirmed and the frames stay separate.",
        "labels": shared_labels,
    }

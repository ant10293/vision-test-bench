"""Judge each box a prompted SAM track returned. No box overwrites anything here.

Detection confidence, association, temporal continuity, semantic identity, and
the final evidence class stay separate. A high SAM score never raises an outcome.
A rotating part or one moving toward the camera may change shape, so a large
step on its own only lowers a row to provisional.

Attachment: a box that grows over another known part is reported as conflicting,
so a tracked washer does not quietly turn into the whole assembly. When SAM stops
returning an object the interval stays unobserved. No box is invented for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.perception.assignment import iou

SUPPORTED = "supported"
PROVISIONAL = "provisional"
CONFLICTING = "conflicting"
REJECTED = "rejected"
UNOBSERVED = "unobserved"
_ORDER = {SUPPORTED: 0, PROVISIONAL: 1, CONFLICTING: 2, REJECTED: 3}


@dataclass
class ValidationPolicy:
    same_frame_sec: float = 0.02
    # Reuses the resolver's agreement level and the identity contradiction level.
    anchor_agree_iou: float = 0.5
    anchor_conflict_iou: float = 0.2
    # On its own prompt frame SAM should sit on the seed. Below this it followed something else.
    seed_iou: float = 0.3
    # A step is a tracking switch past this many box sizes and this multiple of the track's usual step.
    jump_sizes: float = 1.0
    jump_factor: float = 5.0
    scale_step: float = 2.0
    neighbor_iou: float = 0.5
    # This much of another object's box inside this one, after growth, means it swallowed that part.
    absorb_containment: float = 0.8
    absorb_growth: float = 1.5
    # Continuity from a trusted seed vouches for a row only this close to the seed.
    supported_horizon_sec: float = 2.0
    boundary_window: float = 0.45
    # An area under this share of the seed keeps only what is visible and is marked partial.
    partial_area: float = 0.5


def _t(box: dict) -> tuple[float, float, float, float]:
    return (box["x"], box["y"], box["w"], box["h"])


def _center(box: dict) -> tuple[float, float]:
    return (box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)


def _area(box: dict) -> float:
    return box["w"] * box["h"]


def _contained(inner: dict, outer: dict) -> float:
    left = max(inner["x"], outer["x"])
    top = max(inner["y"], outer["y"])
    right = min(inner["x"] + inner["w"], outer["x"] + outer["w"])
    bottom = min(inner["y"] + inner["h"], outer["y"] + outer["h"])
    if right <= left or bottom <= top:
        return 0.0
    return (right - left) * (bottom - top) / max(_area(inner), 1e-9)


def _near(rows: list[dict], timestamp: float, window: float) -> list[dict]:
    return [row for row in rows if abs(row["timestamp"] - timestamp) <= window]


def validate_track(
    samples: list[dict],
    *,
    seed: dict,
    association: str,
    anchors: list[dict],
    neighbors: list[dict],
    media_size: tuple[int, int],
    boundaries: list[float] | None = None,
    policy: ValidationPolicy | None = None,
) -> list[dict]:
    """One row per returned frame, ordered by time. Each carries its outcome and every check.

    `samples` hold timestamp, bbox, confidence, frame_index, source size, and mask
    fields. They can arrive in any order. Walking outward from the seed frame
    compares each box with the one nearer the seed, in time order rather than
    response order. `anchors` and `neighbors` hold timestamp and bbox.
    """
    policy = policy or ValidationPolicy()
    seed_time = float(seed["timestamp"])
    ordered = sorted(samples, key=lambda item: item["timestamp"])
    later = [item for item in ordered if item["timestamp"] >= seed_time]
    earlier = [item for item in reversed(ordered) if item["timestamp"] < seed_time]
    rows = [
        *_walk(later, seed, association, anchors, neighbors, media_size, boundaries, policy),
        *_walk(earlier, seed, association, anchors, neighbors, media_size, boundaries, policy),
    ]
    return sorted(rows, key=lambda item: item["timestamp"])


def _walk(samples, seed, association, anchors, neighbors, media_size, boundaries, policy) -> list[dict]:
    seed_time = float(seed["timestamp"])
    seed_box = seed.get("bbox")
    moves = [
        ((_center(a["bbox"])[0] - _center(b["bbox"])[0]) ** 2 + (_center(a["bbox"])[1] - _center(b["bbox"])[1]) ** 2) ** 0.5
        for a, b in zip(samples, samples[1:], strict=False)
    ]
    usual = sorted(moves)[len(moves) // 2] if moves else 0.0
    previous = None
    tripped = ""
    rows: list[dict] = []
    for sample in samples:
        box = sample["bbox"]
        checks: list[dict] = []
        hard: list[str] = []
        conflict: list[str] = []
        soft: list[str] = []

        def note(name: str, passed: bool, detail: str, bucket: list[str] | None = None) -> None:
            checks.append({"check": name, "passed": passed, "detail": detail})
            if not passed and bucket is not None:
                bucket.append(name)

        note("association", association == "associated", f"track association {association}", conflict if association == "conflict" else hard)
        width, height = media_size
        size_ok = not sample.get("source_width") or (sample["source_width"], sample["source_height"]) == (width, height)
        note(
            "source_coordinates",
            size_ok,
            f"SAM reported {sample.get('source_width')}x{sample.get('source_height')} for {width}x{height} media",
            hard,
        )
        has_mask = bool(sample.get("mask_data") or sample.get("mask_payload"))
        malformed = has_mask and (sample.get("mask_encoding") in {"", "unknown"} or int(sample.get("mask_height") or 0) <= 0)
        note("mask", not malformed, "mask header is malformed" if malformed else ("mask present" if has_mask else "no mask returned"), hard)
        if not has_mask:
            soft.append("mask")
        if seed_box and abs(sample["timestamp"] - seed_time) <= policy.same_frame_sec:
            overlap = iou(_t(box), _t(seed_box))
            note("seed_overlap", overlap >= policy.seed_iou, f"overlap with the seed {overlap:.2f}", conflict)
        anchor_rows = _near(anchors, sample["timestamp"], policy.same_frame_sec)
        agrees = False
        for anchor in anchor_rows:
            overlap = iou(_t(box), _t(anchor["bbox"]))
            if overlap >= policy.anchor_agree_iou:
                agrees = True
                note("anchor", True, f"agrees with {anchor.get('source', 'a measurement')} at {overlap:.2f}")
            elif overlap < policy.anchor_conflict_iou:
                note("anchor", False, f"disagrees with {anchor.get('source', 'a measurement')} at {overlap:.2f}", conflict)
            else:
                note("anchor", False, f"partly overlaps {anchor.get('source', 'a measurement')} at {overlap:.2f}", soft)
        if agrees and tripped:
            note("reassociated", True, "a measurement confirms the track again after the earlier break")
            tripped = ""
        if previous is not None:
            px, py = _center(previous["bbox"])
            cx, cy = _center(box)
            step = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
            size = max(_area(previous["bbox"]) ** 0.5, 1e-3)
            jumped = step > policy.jump_sizes * size and step > policy.jump_factor * max(usual, 1e-3)
            note("continuity", not jumped, f"moved {step / size:.2f} box sizes from the neighboring frame")
            if jumped and not agrees:
                tripped = "A large jump broke the track."
            ratio = _area(box) / max(_area(previous["bbox"]), 1e-9)
            changed = max(ratio, 1 / max(ratio, 1e-9)) >= policy.scale_step
            note("scale_step", not changed, f"area changed by {ratio:.2f} from the neighboring frame", soft)
        for other in _near(neighbors, sample["timestamp"], policy.same_frame_sec):
            overlap = iou(_t(box), _t(other["bbox"]))
            name = other.get("object_local_id", "another object")
            if overlap >= policy.neighbor_iou:
                note("competing_object", False, f"shares this box with {name} at {overlap:.2f}", conflict)
            elif (
                seed_box
                and _contained(other["bbox"], box) >= policy.absorb_containment
                and _area(box) >= policy.absorb_growth * _area(seed_box)
            ):
                note("absorbed_neighbor", False, f"grew over {name}, so it may now cover the assembly", conflict)
                tripped = tripped or f"The box grew over {name}."
        near_cut = next((item for item in boundaries or [] if abs(item - sample["timestamp"]) <= policy.boundary_window), None)
        note("scene_boundary", near_cut is None, "no hard cut nearby" if near_cut is None else f"hard cut at {near_cut:.2f}s", soft)
        if tripped:
            note("track_intact", False, tripped, conflict)
        seed_near = abs(sample["timestamp"] - seed_time) <= policy.supported_horizon_sec and seed.get("trust") in {
            "human_verified",
            "independently_supported",
        }
        if hard:
            outcome = REJECTED
        elif conflict:
            outcome = CONFLICTING
        elif agrees or (seed_near and not soft):
            outcome = SUPPORTED
        else:
            outcome = PROVISIONAL
        partial = bool(seed_box) and _area(box) < policy.partial_area * _area(seed_box)
        rows.append(
            {
                "timestamp": sample["timestamp"],
                "frame_index": sample.get("frame_index"),
                "bbox": box,
                "confidence": sample.get("confidence"),
                "outcome": outcome,
                "association": association,
                "partial": partial,
                "has_mask": has_mask,
                "anchor_agrees": agrees,
                "checks": checks,
            }
        )
        if outcome != REJECTED:
            previous = sample
    return rows


def combine_passes(passes: list[list[dict]], policy: ValidationPolicy | None = None) -> list[dict]:
    """One row per frame from two tracking passes. Two passes that disagree stay a conflict.

    Agreement lifts a provisional row to supported because the passes began from
    separate seeds. It cannot lift a conflicting or rejected row.
    """
    policy = policy or ValidationPolicy()
    if len(passes) == 1:
        return passes[0]
    merged: list[dict] = []
    for rows in passes:
        for row in rows:
            match = next(
                (item for item in merged if abs(item["timestamp"] - row["timestamp"]) <= policy.same_frame_sec),
                None,
            )
            if match is None:
                merged.append(dict(row))
                continue
            overlap = iou(_t(match["bbox"]), _t(row["bbox"]))
            if overlap >= policy.anchor_agree_iou:
                check = {"check": "pass_agreement", "passed": True, "detail": f"the other pass agrees at {overlap:.2f}"}
                best = min(match, row, key=lambda item: _ORDER[item["outcome"]])
                outcome = best["outcome"]
                if max(_ORDER[match["outcome"]], _ORDER[row["outcome"]]) <= _ORDER[PROVISIONAL]:
                    outcome = SUPPORTED
                match.update(best)
                match["outcome"] = outcome
                match["checks"] = [*best["checks"], check]
            elif overlap < policy.anchor_conflict_iou:
                check = {"check": "pass_agreement", "passed": False, "detail": f"the other pass disagrees at {overlap:.2f}"}
                match["outcome"] = REJECTED if match["outcome"] == REJECTED else CONFLICTING
                match["checks"] = [*match["checks"], check]
            else:
                check = {"check": "pass_agreement", "passed": False, "detail": f"the other pass partly overlaps at {overlap:.2f}"}
                if match["outcome"] == SUPPORTED:
                    match["outcome"] = PROVISIONAL
                match["checks"] = [*match["checks"], check]
    return sorted(merged, key=lambda item: item["timestamp"])


def remaining_gaps(rows: list[dict], frame_times: list[float], start: float, end: float) -> list[dict]:
    """Submitted frames in the interval with no usable box. These are unobserved, not absent."""
    wanted = [time for time in frame_times if start - 1e-6 <= time <= end + 1e-6]
    covered = {round(row["timestamp"], 3) for row in rows if row["outcome"] in {SUPPORTED, PROVISIONAL}}
    gaps: list[dict] = []
    current: list[float] = []
    for time in wanted:
        if round(time, 3) in covered:
            if current:
                gaps.append(_gap(current))
                current = []
        else:
            current.append(time)
    if current:
        gaps.append(_gap(current))
    return gaps


def _gap(times: list[float]) -> dict:
    return {"start": times[0], "end": times[-1], "frames": len(times), "outcome": UNOBSERVED}

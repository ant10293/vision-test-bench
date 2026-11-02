"""What is known about each object's visual tracks, and what could reconnect them. Plans only.

Nothing here contacts a provider or changes a row. Every plan names the evidence it
would use. A drifting track is never offered as a seed for itself. An object with no
trustworthy evidence stays unresolved, and the plan says so.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.media import VideoAsset
from app.models.recovery import RecoveryExperiment, RecoveryProposal, RecoveryRequest
from app.models.runs import AnalysisRun
from app.perception.assignment import iou
from app.perception.recovery_validate import REACQUIRED
from app.perception.seeds import (
    CANDIDATE,
    HUMAN_VERIFIED,
    INDEPENDENTLY_SUPPORTED,
    Seed,
    collect_seeds,
)
from app.perception.track_segments import LINKED, unresolved_spans
from app.pipeline.sampling_stage import timeline_for
from app.providers.sam import parse_sam_output
from app.services.recovery import _boundaries, _document, _usable_spatial, load_response, settings
from app.services.recovery_diagnostics import raw_boxes

# How far past the uncertain interval to look for stored boxes and frames worth checking.
SEARCH_AFTER_SEC = 3.0
FORWARD_AFTER_SEED_SEC = 1.0
MAX_CANDIDATES = 8
MAX_FRAMES_TO_CHECK = 5
COMPETING_IOU = 0.5


def _usable_after(failure: dict, segment: dict) -> bool:
    """A later linked segment with supported boxes, on the far side of the failure from its own seed."""
    if segment["identity"]["status"] != LINKED or not segment["counts"]["supported"] or segment.get("decision") == "rejected":
        return False
    if segment["segment_id"] == failure["segment_id"]:
        return False
    if failure["direction"] == "forward":
        return segment["end"] > failure["time"]
    return segment["start"] < failure["time"]


def _seed_row(seed: Seed, others: list[Seed]) -> dict:
    return {
        "source": seed.source,
        "trust": seed.trust,
        "seed_id": seed.seed_id,
        "timestamp": seed.timestamp,
        "bbox": seed.bbox,
        "explanation": seed.explanation,
        "needs_confirmation": seed.trust == CANDIDATE,
        "may_be": _competitors(seed.bbox, seed.timestamp, others),
    }


def _competitors(box: dict | None, time: float, seeds: list[Seed]) -> list[str]:
    if not box:
        return []
    names = set()
    for other in seeds:
        if abs(other.timestamp - time) > 0.02 or not other.bbox:
            continue
        one = (box["x"], box["y"], box["w"], box["h"])
        two = (other.bbox["x"], other.bbox["y"], other.bbox["w"], other.bbox["h"])
        if iou(one, two) >= COMPETING_IOU:
            names.add(other.object_local_id)
    return sorted(names)


def _cached_candidates(experiments: list[RecoveryExperiment], object_local_id: str, start: float, end: float, seeds: list[Seed]) -> list[dict]:
    """Stored SAM boxes tied to no object: concept passes and tracks nobody prompted. A person has to identify these."""
    found: list[dict] = []
    seen: set[tuple] = set()
    for experiment in experiments:
        for spec in (experiment.plan_json or {}).get("requests") or []:
            if spec.get("prompt_kind") == "concept" and spec.get("object_local_id") != object_local_id:
                continue
            if spec.get("prompt_kind") != "concept" and object_local_id not in spec["bindings"].values():
                continue
            payload = load_response(experiment.run_id, spec["key"])
            if payload is None:
                continue
            for box in raw_boxes(parse_sam_output(payload.get("text") or ""), spec, {}):
                if box["status"] != "unassigned" or box["bbox"] is None or not start <= box["timestamp"] <= end:
                    continue
                identity = (spec["key"], box["raw_track_id"])
                if identity in seen:
                    continue
                seen.add(identity)
                found.append(
                    {
                        "source": "cached_concept" if spec.get("prompt_kind") == "concept" else "cached_unprompted_track",
                        "trust": CANDIDATE,
                        "seed_id": "",
                        "timestamp": box["timestamp"],
                        "bbox": box["bbox"],
                        "raw_track_id": box["raw_track_id"],
                        "explanation": "SAM found something here. Nothing says it is this physical part.",
                        "needs_confirmation": True,
                        "may_be": _competitors(box["bbox"], box["timestamp"], seeds),
                    }
                )
    return sorted(found, key=lambda item: item["timestamp"])


def _proposed(failure: dict, evidence: dict | None, strategy: str, interval_start: float, interval_end: float) -> dict:
    target: dict = {
        "object_local_id": failure["object_local_id"],
        "reacquires": {
            "failure_id": failure["failure_id"],
            "failure_time": failure["time"],
            "experiment_id": failure["experiment_id"],
            "strategy": strategy,
        },
    }
    if strategy == "concept_redetection":
        target["strategy"] = "concept"
    if evidence is None:
        return {**target, "time_start": interval_start, "time_end": interval_end, "direction": "auto"}
    time = evidence["timestamp"]
    return {
        **target,
        "time_start": min(interval_start, time),
        "time_end": max(time + FORWARD_AFTER_SEED_SEC, interval_start),
        "direction": "both",
        "seed_id": evidence["seed_id"],
        "confirm_seed": evidence["needs_confirmation"],
    }


def _strategies(failure, seeds, cached, phrase, attempted, interval_start, interval_end) -> list[dict]:
    # Anywhere past the failure counts, nearest first. The dry run reports whether the clip fits the limits.
    forward = failure["direction"] == "forward"
    window = sorted(
        (
            seed
            for seed in seeds
            if seed.object_local_id == failure["object_local_id"]
            and seed.seed_id not in attempted
            # A prompted SAM track cannot vouch for itself, so its own output is never a reacquisition seed.
            and seed.source != "sam_spatial"
            and (seed.timestamp > failure["time"] if forward else seed.timestamp < failure["time"])
        ),
        key=lambda seed: (abs(seed.timestamp - failure["time"]), seed.seed_id),
    )
    independent = [_seed_row(seed, seeds) for seed in window if seed.trust == INDEPENDENTLY_SUPPORTED]
    human = [_seed_row(seed, seeds) for seed in window if seed.trust == HUMAN_VERIFIED]
    candidates = [_seed_row(seed, seeds) for seed in window if seed.trust == CANDIDATE] + cached
    candidates = candidates[:MAX_CANDIDATES]
    has_cached = bool(independent or human or candidates)
    return [
        {
            "strategy": "independent_observation",
            "available": bool(independent),
            "requires_remote": True,
            "evidence": independent[:MAX_CANDIDATES],
            "note": "A measurement from another producer locates the object after the failure.",
        },
        {
            "strategy": "human_seed",
            "available": bool(human),
            "requires_remote": True,
            "evidence": human[:MAX_CANDIDATES],
            "note": "A person placed a box on the object after the failure.",
        },
        {
            "strategy": "candidate_detection",
            "available": bool(candidates),
            "requires_remote": True,
            "evidence": candidates,
            "note": "Stored boxes may show the object. A person has to confirm which one, if any.",
        },
        {
            "strategy": "concept_redetection",
            "available": not has_cached,
            "requires_remote": True,
            "evidence": [],
            "concept": phrase,
            "time_start": interval_start,
            "time_end": interval_end,
            "note": "A short SAM concept pass could find possible locations. It cannot say which physical part each one is.",
        },
        {
            "strategy": "manual",
            "available": True,
            "requires_remote": True,
            "evidence": [],
            "note": "Find a frame where the object is visible again and draw a box on it.",
        },
    ]


def _explanation(failure: dict, raw_after: int, recommended: dict, attempts: list[dict], resolved: dict | None, spans: list[dict]) -> str:
    time = failure["time"]
    if resolved is not None:
        text = (
            f"A new seed at {resolved['seed']['timestamp']:.2f}s started a separate segment with "
            f"{resolved['counts']['supported']} supported boxes."
        )
        if spans:
            text += f" {spans[0]['start']:.2f}–{spans[-1]['end']:.2f}s has frames with no validated box and stays unresolved."
        return text
    if failure["reason"] == "no_detection":
        lead = f"SAM stopped returning this object at {time:.2f}s."
    else:
        lead = f"SAM kept returning boxes ({raw_after} after the failure), and tracking became unreliable at {time:.2f}s."
    if attempts:
        lead += f" {len(attempts)} reacquisition attempts did not produce a verified segment, so stronger evidence is needed."
    if recommended["strategy"] in {"independent_observation", "human_seed"}:
        return f"{lead} A trusted seed is available after the failure. Nothing has been sent."
    if recommended["strategy"] == "candidate_detection":
        return f"{lead} Stored boxes may show the object, and a person has to confirm one. Nothing has been sent."
    return f"{lead} No independently verified reappearance seed was available, so automatic reacquisition was not attempted."


def reacquisition(session: Session, run: AnalysisRun) -> dict:
    experiments = list(
        session.scalars(
            select(RecoveryExperiment).where(RecoveryExperiment.run_id == run.id).order_by(RecoveryExperiment.created_at)
        )
    )
    proposals: list[tuple[RecoveryExperiment, RecoveryProposal]] = []
    for experiment in experiments:
        rows = session.scalars(
            select(RecoveryProposal).where(
                RecoveryProposal.experiment_id == experiment.id, RecoveryProposal.generation == experiment.generation
            )
        ).all()
        proposals.extend((experiment, row) for row in rows)
    document = _document(session, run.active_revision_id)
    seeds = collect_seeds(document, _usable_spatial(run.id), boundaries=_boundaries(session, run.id))
    timeline = timeline_for(session.get(VideoAsset, run.video_id), None)
    labels = {obj.local_id: obj.label for obj in document.objects}
    instances = {obj.local_id: obj.instance_id for obj in document.objects}
    grouped: dict[str, list[tuple[RecoveryExperiment, RecoveryProposal]]] = {}
    for item in proposals:
        grouped.setdefault(item[1].object_local_id, []).append(item)
    objects = [
        _object_history(session, local_id, items, experiments, seeds, timeline.pts, labels.get(local_id, local_id), instances.get(local_id))
        for local_id, items in sorted(grouped.items())
    ]
    return {
        "run_id": run.id,
        "objects": objects,
        "note": "Plans only. Nothing here was sent, and any new SAM request needs its own dry run and approval.",
        "price_per_frame_usd": settings.sam_price_per_frame_usd,
    }


def _object_history(session, local_id, items, experiments, seeds, frames, label, instance_id) -> dict:
    segments: dict[tuple, dict] = {}
    failures: dict[str, dict] = {}
    usable: set[float] = set()
    returned: set[float] = set()
    spans_input: list[tuple[float, float]] = []
    raw_frames = associated = supported = provisional = conflicting = rejected = 0
    limits: set[str] = set()
    attempted_targets: dict[str, list[dict]] = {}
    keys: set[str] = set()
    ranges: dict[str, tuple[float, float]] = {}
    for experiment, proposal in items:
        track = (proposal.metrics_json or {}).get("track")
        if not track:
            limits.add("An older result has no segment data. Replay it for free to add it.")
            continue
        rows = proposal.rows_json or []
        spans_input.append((proposal.time_start, proposal.time_end))
        for row in rows:
            key = round(row["timestamp"], 3)
            returned.add(key)
            keys.add(row["request_key"])
            if row["outcome"] in {"supported", "provisional"} and proposal.decision != "rejected":
                usable.add(key)
        raw_frames += len(rows)
        associated += sum(1 for row in rows if row["association"] == "associated")
        counts = (proposal.metrics_json or {}).get("counts", {})
        supported += counts.get("supported", 0)
        provisional += counts.get("provisional", 0)
        conflicting += counts.get("conflicting", 0)
        rejected += counts.get("rejected", 0)
        limits.update(track.get("evidence_limits") or [])
        for segment in track["segments"]:
            identity = (segment["request_key"], segment["raw_track_id"], segment["seed"]["seed_id"], segment["direction"], segment["start"], segment["end"])
            segments.setdefault(identity, {**segment, "decision": proposal.decision, "proposal_id": proposal.id})
        for failure in track["failures"]:
            if failure["failure_id"] not in failures:
                failures[failure["failure_id"]] = {**failure, "decision": proposal.decision}
                last_row = max((row["timestamp"] for row in rows), default=proposal.time_end)
                first_row = min((row["timestamp"] for row in rows), default=proposal.time_start)
                ranges[failure["failure_id"]] = (min(proposal.time_start, first_row), max(proposal.time_end, last_row))
    ordered_segments = sorted(segments.values(), key=lambda item: item["start"])
    frames_used = sorted({time for start, end in spans_input for time in frames if start - 1e-6 <= time <= end + 1e-6})
    spans = unresolved_spans(frames_used, usable, returned, min((s for s, _ in spans_input), default=0.0), max((e for _, e in spans_input), default=0.0))
    # Every run attempt counts, including one that returned nothing and so has no segment to show.
    for experiment in experiments:
        for target in experiment.targets_json or []:
            reacquires = target.get("reacquires")
            if not reacquires or target["object_local_id"] != local_id or experiment.status in {"draft", "planned", "running"}:
                continue
            proposal = next((row for owner, row in items if owner.id == experiment.id and row.target_id == target["target_id"]), None)
            own = [segment for segment in ordered_segments if proposal is not None and segment.get("proposal_id") == proposal.id]
            specs = (experiment.plan_json or {}).get("requests") or []
            attempted_targets.setdefault(reacquires["failure_id"], []).append(
                {
                    "experiment_id": experiment.id,
                    "status": experiment.status,
                    "decision": proposal.decision if proposal is not None else "",
                    "seed_ids": sorted(
                        {
                            entry["seed"]["seed_id"]
                            for spec in specs
                            for entry in spec["objects"]
                            if entry["target_id"] == target["target_id"]
                        }
                    ),
                    "strategy": reacquires.get("strategy", ""),
                    "succeeded": any(segment["status"] == REACQUIRED and segment["decision"] != "rejected" for segment in own),
                }
            )
    plans = []
    for failure in sorted(failures.values(), key=lambda item: item["time"]):
        start, end = ranges[failure["failure_id"]]
        if failure["direction"] == "forward":
            interval_start, interval_end = failure["time"], max(end, failure["time"])
        else:
            interval_start, interval_end = min(start, failure["time"]), failure["time"]
        attempts = attempted_targets.get(failure["failure_id"], [])
        resolved = next((segment for segment in ordered_segments if _usable_after(failure, segment)), None)
        cached = _cached_candidates(experiments, local_id, interval_start, interval_end + SEARCH_AFTER_SEC, seeds)
        attempted = {seed for attempt in attempts if not attempt["succeeded"] for seed in attempt["seed_ids"]}
        strategies = _strategies(failure, seeds, cached, label, attempted, interval_start, interval_end)
        recommended = next(item for item in strategies if item["available"])
        evidence = recommended["evidence"][0] if recommended["evidence"] else None
        if evidence is not None and not evidence["seed_id"]:
            evidence = None
        checks = [item["timestamp"] for item in recommended["evidence"]]
        if not checks:
            after = [time for time in frames if interval_start < time <= interval_end + SEARCH_AFTER_SEC]
            stride = max(len(after) // MAX_FRAMES_TO_CHECK, 1)
            checks = after[::stride][:MAX_FRAMES_TO_CHECK]
        raw_after = sum(
            1
            for _experiment, proposal in items
            for row in proposal.rows_json or []
            if row.get("lifecycle") == "lost" and row.get("request_key") == failure["request_key"]
        )
        window_spans = [item for item in spans if item["end"] >= interval_start and item["start"] <= interval_end]
        plans.append(
            {
                "failure": failure,
                "status": "resolved" if resolved else "open",
                "uncertain_interval": {"start": interval_start, "end": resolved["start"] if resolved and resolved["start"] > interval_start else interval_end},
                "last_supported_time": failure["last_supported_time"],
                "reappearance_frames": checks,
                "strategies": strategies,
                "recommended": recommended["strategy"],
                "proposed_target": _proposed(failure, evidence, recommended["strategy"], interval_start, interval_end),
                # Only a person may start this. A candidate also has to be confirmed.
                "automatic_start_allowed": False,
                "blocked_reason": (
                    "" if recommended["strategy"] in {"independent_observation", "human_seed"}
                    else "No trustworthy evidence locates the object after this failure. A person has to identify it first."
                ),
                "attempts": attempts,
                "needs_stronger_evidence": bool(attempts) and not any(item["succeeded"] for item in attempts),
                "explanation": _explanation(failure, raw_after, recommended, [a for a in attempts if not a["succeeded"]], resolved, window_spans),
            }
        )
    requests = list(
        session.scalars(
            select(RecoveryRequest).where(
                RecoveryRequest.experiment_id.in_([item[0].id for item in items]), RecoveryRequest.cache_key.in_(keys or {""})
            )
        )
    )
    live = [row for row in requests if row.source == "live"]
    costs = [row.cost_usd for row in live if row.cost_usd is not None]
    every_attempt = [attempt for group in attempted_targets.values() for attempt in group]
    return {
        "object_local_id": local_id,
        "physical_instance_id": instance_id or local_id,
        "label": label,
        "segments": ordered_segments,
        "failures": sorted(failures.values(), key=lambda item: item["time"]),
        "unresolved": spans,
        "plans": plans,
        "stats": {
            "raw_frames_returned": raw_frames,
            "associated": associated,
            "supported": supported,
            "provisional": provisional,
            "conflicting": conflicting,
            "rejected": rejected,
            "no_detection_frames": sum(item["no_detection_frames"] for item in spans),
            "unusable_frames": sum(item["unusable_frames"] for item in spans),
            "track_breaks": len(failures),
            "reacquisition_attempts": len(every_attempt),
            "reacquisitions_succeeded": sum(1 for item in every_attempt if item["succeeded"]),
            "reacquisitions_failed": sum(1 for item in every_attempt if not item["succeeded"]),
            "identity_ambiguities": sum(1 for item in ordered_segments if item["identity"]["status"] in {"ambiguous", "rejected"}),
            "api_requests": len(live),
            "recorded_cost_usd": round(sum(costs), 6) if costs else None,
        },
        "evidence_limits": sorted(limits),
    }

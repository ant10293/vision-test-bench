"""Score an analysis document against simulator truth. The document is not modified."""

from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.metrics import evaluate_documents, greedy_match, interval_iou, prf
from app.perception.assignment import iou
from app.perception.identity_eval import evaluate_identity

ASSEMBLY_ACTIONS = {"insert", "attach", "detach", "remove", "align"}
ASSEMBLY_RELATIONS = {"attached", "inside", "held_by", "supported_by"}


def analysis_payload(
    video_id: str,
    research_mode: str,
    provider_id: str = "deterministic",
    model_id: str = "motion-baseline",
    perception_preset: str = "B_temporal",
    name: str = "Simulation playback",
) -> dict:
    """What a benchmark run is allowed to know. Scene truth is not in here."""
    return {
        "video_id": video_id,
        "name": name,
        "research_mode": research_mode,
        "provider_id": provider_id,
        "model_id": model_id,
        "modality": "sampled_frames",
        "sampling_preset": "standard",
        "perception_preset": perception_preset,
        "segmentation_mode": "disabled",
        "analysis_pass": "single",
    }


def assert_independent(run) -> None:
    if run.fixture or run.provider_id == "fixture":
        raise ValueError(
            "The fixture provider authors the answer. Benchmark evaluation requires an independent analysis."
        )
    blob = json.dumps(run.config_json or {})
    for marker in ("ground_truth", "scene.json", "simulator", "inserted_into"):
        if marker in blob:
            raise ValueError("The analysis config contains simulation ground truth.")


def _visible_observations(dataset: Path) -> list[dict]:
    frames = json.loads((dataset / "annotations" / "frames.json").read_text())["frames"]
    rows = []
    for frame in frames:
        for instance in frame["instances"]:
            if instance["visibility"] not in {"fully_visible", "partially_occluded", "visible"}:
                continue
            if not instance.get("bbox"):
                continue
            rows.append(
                {
                    "instance_id": instance["instance_id"],
                    "timestamp": frame["timestamp"],
                    "bbox": instance["bbox"],
                }
            )
    return rows


def _prediction_observations(document: dict) -> list[dict]:
    rows = []
    for item in document.get("observations") or []:
        instance = item.get("instance_id") or item.get("track_ref") or item.get("object_local_id")
        if not instance or item.get("timestamp") is None or not item.get("bbox"):
            continue
        rows.append({**item, "instance_id": instance})
    return rows


def _detection(truth: list[dict], predicted: list[dict]) -> dict:
    if not truth:
        return {"status": "NOT_EVALUATED", "evaluated": False, "reason": "No visibly present ground-truth boxes."}
    if not predicted:
        return {
            "status": "NOT_EVALUATED",
            "evaluated": False,
            "reason": "The prediction has no boxes. That is not a detection score of zero.",
        }
    pairs = []
    for i, pred in enumerate(predicted):
        for j, gold in enumerate(truth):
            if abs(float(pred["timestamp"]) - float(gold["timestamp"])) > 0.35:
                continue
            score = iou(
                (pred["bbox"]["x"], pred["bbox"]["y"], pred["bbox"]["w"], pred["bbox"]["h"]),
                (gold["bbox"]["x"], gold["bbox"]["y"], gold["bbox"]["w"], gold["bbox"]["h"]),
            )
            pairs.append((score, i, j))
    matches = greedy_match(pairs, 0.3)
    matched_p = {i for i, _j, _s in matches}
    matched_g = {j for _i, j, _s in matches}
    ious = [score for _i, _j, score in matches]
    return {
        "evaluated": True,
        "scores_are_probabilities": False,
        "positive_class": "visibly_present",
        "occluded_and_absent_are_not_false_negatives": True,
        **prf(len(matches), len(predicted) - len(matched_p), len(truth) - len(matched_g)),
        "mean_iou": round(sum(ious) / len(ious), 4) if ious else None,
    }


def _claims(document: dict) -> list[dict]:
    found = []
    for action in document.get("actions") or []:
        evidence = action.get("evidence") or {}
        if evidence.get("classification") != "directly_observed":
            continue
        if action.get("action_type") not in ASSEMBLY_ACTIONS:
            continue
        if action.get("outcome") not in {"completed", "failed"}:
            continue
        found.append(
            {
                "kind": "action",
                "id": action.get("local_id"),
                "action_type": action.get("action_type"),
                "outcome": action.get("outcome"),
                "start": action.get("start"),
                "end": action.get("end"),
            }
        )
    for relation in document.get("relationships") or []:
        evidence = relation.get("evidence") or {}
        if evidence.get("classification") != "directly_observed":
            continue
        if relation.get("kind") not in ASSEMBLY_RELATIONS:
            continue
        found.append(
            {
                "kind": "relationship",
                "id": relation.get("local_id") or relation.get("kind"),
                "relation": relation.get("kind"),
                "start": evidence.get("time_start"),
                "end": evidence.get("time_end"),
            }
        )
    return found


def _audit(document: dict, conditions: list[dict]) -> dict:
    claims = _claims(document)
    hidden = [item for item in conditions if item.get("required") and item.get("justified_verdict") == "CANNOT_VERIFY"]
    unsupported = []
    for claim in claims:
        for condition in hidden:
            start = float(claim.get("start") or 0)
            end = float(claim.get("end") or start)
            if interval_iou(start, end, float(condition["timestamp"]), float(condition["timestamp"])) > 0 or abs(
                ((start + end) / 2) - float(condition["timestamp"])
            ) <= 0.5:
                unsupported.append(
                    {
                        **claim,
                        "constraint_id": condition["constraint_id"],
                        "reason": "A definitive assembly outcome was marked directly observed while that condition could not be verified from the picture.",
                    }
                )
                break
    return {
        "prediction_asserted_assembly_outcome": bool(claims),
        "unsupported_claims": unsupported,
        "physical_conditions": conditions,
        "note": (
            "A condition the simulator knows and the camera cannot show has justified verdict CANNOT_VERIFY. "
            "Guessing the hidden state is not accuracy."
        ),
    }


def score_dataset(dataset: Path, document: dict) -> dict:
    truth_obs = _visible_observations(dataset)
    pred_obs = _prediction_observations(document)
    truth_doc = {"observations": truth_obs, "objects": [], "actions": []}
    pred_doc = {"observations": pred_obs, "objects": document.get("objects") or [], "actions": document.get("actions") or []}
    events = json.loads((dataset / "annotations" / "events.json").read_text())["events"]
    truth_doc["actions"] = [
        {
            "local_id": item["id"],
            "action_type": item["kind"],
            "start": item["start"],
            "end": item["end"],
            "description": item["outcome"],
        }
        for item in events
        if item["outcome"] != "kinematic"
    ]
    observability = json.loads((dataset / "annotations" / "observability.json").read_text())
    identity = evaluate_identity(
        {"observations": pred_obs},
        {"observations": truth_obs},
        {"identity_time_sec": 0.35, "identity_box_iou": 0.3},
    )
    actions = evaluate_documents(pred_doc, truth_doc)
    masks_present = any((item.get("mask") for item in document.get("observations") or []))
    return {
        "source": "simulator_ground_truth",
        "is_a_detection_claim": False,
        "detection": _detection(truth_obs, pred_obs),
        "identity": identity,
        "actions": {
            "interval_match": actions.get("actions"),
            "mean_boundary_error_sec": actions.get("mean_boundary_error_sec"),
            "label_object_match_is_not_identity": True,
            "errors": actions.get("errors"),
        },
        "camera_pose": {
            "status": "NOT_EVALUATED",
            "evaluated": False,
            "reason": "This analysis has no 6-DoF pose. Image-plane affine compensation is not scored as camera error.",
        },
        "masks": {
            "status": "NOT_EVALUATED",
            "evaluated": False,
            "reason": "The prediction has no instance masks." if not masks_present else "Mask IoU is not computed for this output.",
        },
        "outcomes": _audit(document, observability.get("conditions") or []),
        "scores_are_probabilities": False,
    }

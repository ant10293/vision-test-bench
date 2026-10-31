"""JSON extraction from untrusted model text."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.schemas.analysis import AnalysisDocument


class ParseFailure(Exception):
    def __init__(self, message: str, raw_text: str = ""):
        super().__init__(message)
        self.raw_text = raw_text


_COLON_REPAIR_LIMIT = 4
_VALUE_START = set('{["0123456789-tfn')
_BARE_STRING = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\"")


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ParseFailure("model response did not contain a JSON object", text) from None
        blob = stripped[start : end + 1]
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError as exc:
            payload = _recover_object(blob, exc)
            if payload is None:
                raise ParseFailure(f"malformed JSON: {exc}", text) from exc
    if not isinstance(payload, dict):
        raise ParseFailure("model JSON must be an object", text)
    return payload


def _recover_object(blob: str, exc: json.JSONDecodeError) -> dict[str, Any] | None:
    """Recover two shapes long answers actually come back in.

    A missing colon is one dropped ``:"`` between a key and its value. An
    abandoned document is a fragment, then a second object that runs to the
    closing brace. A first object that already parses is left alone.
    """
    repaired = _repair_missing_colons(blob, exc)
    if repaired is not None:
        return repaired
    try:
        json.JSONDecoder().raw_decode(blob)
    except json.JSONDecodeError:
        return _object_through_end(blob)
    return None


def _repair_missing_colons(blob: str, exc: json.JSONDecodeError) -> dict[str, Any] | None:
    current = blob
    error = exc
    for _ in range(_COLON_REPAIR_LIMIT):
        if error.msg != "Expecting ':' delimiter":
            return None
        nxt = _with_colon(current, error.pos)
        if nxt is None:
            return None
        try:
            payload = json.loads(nxt)
        except json.JSONDecodeError as again:
            current = nxt
            error = again
            continue
        if isinstance(payload, dict):
            return payload
        return None
    return None


def _with_colon(blob: str, pos: int) -> str | None:
    if pos <= 0 or pos >= len(blob) or blob[pos - 1] != '"':
        return None
    nxt = blob[pos]
    if nxt in _VALUE_START:
        return blob[:pos] + ":" + blob[pos:]
    if _BARE_STRING.match(blob[pos:]):
        return blob[:pos] + ':"' + blob[pos:]
    return None


def _object_through_end(blob: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    start = 0
    while True:
        index = blob.find("{", start)
        if index < 0:
            return None
        try:
            payload, stop = decoder.raw_decode(blob, index)
        except json.JSONDecodeError:
            start = index + 1
            continue
        if isinstance(payload, dict) and stop == len(blob):
            return payload
        start = index + 1


_EVIDENCE_CLASS = {"DIRECTLY_OBSERVED", "INFERRED", "UNKNOWN"}
_PROVENANCE = {
    "AI_PREDICTION",
    "CV_DETECTION",
    "DETERMINISTIC",
    "HUMAN_ANNOTATION",
    "HUMAN_CORRECTION",
}
_OUTCOMES = {"completed", "attempted", "failed", "unknown"}
_DEPENDENCY = {"necessary_physical", "observed_temporal", "inferred", "unknown"}
_RELATIONS = {
    "near",
    "touching",
    "inside",
    "around",
    "attached",
    "supported_by",
    "held_by",
    "above",
    "below",
    "left_of",
    "right_of",
    "occluded_by",
    "unknown",
}


def align_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy answers that used nearby key names into the schema's fields.

    A model that returns actions_detailed, or a workflow list, otherwise loses
    those records. Unknown keys are ignored, and one bad record is skipped so
    it cannot reject the objects and boxes that did match.
    """
    data = dict(payload)
    _hoist_grouped(data)
    workflow = data.get("workflow")
    if isinstance(workflow, dict):
        steps = workflow.get("steps") or workflow.get("workflow_steps")
        if not _present(data.get("workflow_steps")) and _present(steps):
            data["workflow_steps"] = steps
        if not _present(data.get("dependencies")) and _present(workflow.get("dependencies")):
            data["dependencies"] = workflow["dependencies"]
        data["workflow"] = steps or []
    _fill_alias(data, "objects", (), _align_object)
    _fill_alias(data, "observations", (), _align_observation)
    _fill_alias(data, "actions", ("actions_detailed", "detailed_actions", "actions_structured"), _align_action)
    _fill_alias(data, "workflow_steps", ("workflow", "workflow_steps_structured"), _align_step)
    _fill_alias(data, "dependencies", ("dependencies_if_any", "workflow_dependencies"), _align_dependency)
    _bind_step_names(data)
    if not _present(data.get("relationships")):
        pairs = _pair_relationships(data.get("relationships_at_intervals"))
        if pairs:
            data["relationships"] = pairs
    _fill_alias(data, "relationships", ("relationships_structured",), _align_relationship)
    _fill_alias(data, "ambiguities", ("ambiguities_structured",), _align_ambiguity)
    _fill_step_detail(data)
    return data


def parse_analysis(text: str) -> AnalysisDocument:
    payload = align_payload(extract_json_object(text))
    try:
        return AnalysisDocument.model_validate(payload)
    except ValidationError as exc:
        raise ParseFailure(f"response did not match the analysis schema: {exc}", text) from exc


def _hoist_grouped(data: dict[str, Any]) -> None:
    """A model sometimes nests the procedure under one object, such as actions_and_workflow."""
    reserved = {"actions", "workflow", "workflow_steps", "dependencies"}
    for key, value in list(data.items()):
        if key in reserved or not isinstance(value, dict):
            continue
        if not _present(data.get("actions")) and _present(value.get("actions")):
            data["actions"] = value["actions"]
        workflow = value.get("workflow")
        if not isinstance(workflow, dict):
            continue
        if not _present(data.get("workflow_steps")):
            steps = workflow.get("steps") or workflow.get("workflow_steps")
            if _present(steps):
                data["workflow_steps"] = steps
        if not _present(data.get("dependencies")) and _present(workflow.get("dependencies")):
            data["dependencies"] = workflow["dependencies"]


def _fill_alias(data: dict[str, Any], canonical: str, aliases: tuple[str, ...], align) -> None:
    raw = data.get(canonical) if _present(data.get(canonical)) else None
    if raw is None:
        for name in aliases:
            if _present(data.get(name)):
                raw = data[name]
                break
    if not isinstance(raw, list):
        return
    kept = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        row = align(item, index)
        if row is not None:
            kept.append(row)
    if kept:
        data[canonical] = kept


def _present(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0


def _align_object(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    from app.schemas.analysis import ObjectPrediction

    row = dict(item)
    if not row.get("visual_characteristics") and row.get("visual_characteristics_description"):
        row["visual_characteristics"] = str(row["visual_characteristics_description"])
    if not row.get("description"):
        text = row.get("visual_characteristics") or row.get("descriptive_name")
        if text:
            row["description"] = str(text)
    if not row.get("initial_state") and row.get("initial_resting_position"):
        row["initial_state"] = str(row["initial_resting_position"])
    if row.get("manipulated") is not True and row.get("manipulated_by_hands") is True:
        row["manipulated"] = True
    if row.get("uncertain_identity") is not True and row.get("identity_uncertain") is True:
        row["uncertain_identity"] = True
    if not row.get("instance_id") and row.get("instance_id_or_local_ref"):
        row["instance_id"] = str(row["instance_id_or_local_ref"])
    return _accepted(ObjectPrediction, row)


def _align_observation(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    from app.schemas.analysis import ObservationPrediction

    row = dict(item)
    box = _coerce_box(row)
    if box is not None:
        row["bbox"] = box
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    note = str(evidence.get("note") or row.get("description") or "")
    row["evidence"] = _evidence(evidence, note)
    return _accepted(ObservationPrediction, row)


def _coerce_box(row: dict[str, Any]) -> dict[str, Any] | None:
    from app.schemas.analysis import BBox

    candidate = None
    bbox = row.get("bbox")
    keys = ("x", "y", "w", "h")
    if isinstance(bbox, dict) and all(key in bbox for key in keys):
        candidate = {key: bbox[key] for key in keys}
    elif all(_number(row.get(key)) for key in keys):
        candidate = {key: row[key] for key in keys}
    else:
        norm = row.get("bbox_norm")
        if isinstance(norm, (list, tuple)) and len(norm) == 4 and all(_number(value) for value in norm):
            candidate = dict(zip(keys, norm, strict=True))
    if candidate is None:
        return None
    try:
        return BBox.model_validate(candidate).model_dump()
    except ValidationError:
        return None


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _align_action(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    from app.schemas.analysis import ActionPrediction

    row = dict(item)
    if not row.get("local_id") and row.get("action_id"):
        row["local_id"] = str(row["action_id"])
    if not row.get("local_id"):
        row["local_id"] = f"Action_{index}"
    if not row.get("action_type"):
        kind = row.get("action_kind") or row.get("name")
        if kind:
            row["action_type"] = str(kind)
    if row.get("start") is None and row.get("duration_start") is not None:
        row["start"] = row["duration_start"]
    if row.get("start") is None and row.get("time_start") is not None:
        row["start"] = row["time_start"]
    if row.get("end") is None and row.get("duration_end") is not None:
        row["end"] = row["duration_end"]
    if row.get("end") is None and row.get("time_end") is not None:
        row["end"] = row["time_end"]
    if not row.get("actor"):
        actor = row.get("actor_or_basis") or row.get("actor_or_hand")
        if actor:
            row["actor"] = str(actor)
    ids = list(row.get("object_local_ids") or [])
    for guest in row.get("object_ids") or []:
        if guest not in ids:
            ids.append(guest)
    guest = row.get("manipulated_object_local_id")
    if guest and guest not in ids:
        ids.insert(0, guest)
    target = row.get("target_or_destination")
    if isinstance(target, str) and target.startswith("Object_") and target not in ids:
        ids.append(target)
    row["object_local_ids"] = ids
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    if not row.get("description"):
        row["description"] = str(evidence.get("note") or row.get("name") or row.get("action_type") or "")
    if row.get("outcome") not in _OUTCOMES:
        result = str(row.get("result") or "").lower()
        row["outcome"] = result if result in _OUTCOMES else "unknown"
    row["evidence"] = _evidence(evidence, row["description"])
    return _accepted(ActionPrediction, row)


def _align_step(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    from app.schemas.analysis import WorkflowStepPrediction

    row = dict(item)
    if not row.get("local_id") and row.get("step_id"):
        row["local_id"] = str(row["step_id"])
    if not row.get("local_id"):
        row["local_id"] = f"Step_{index}"
    window = row.get("time_window") if isinstance(row.get("time_window"), dict) else {}
    if row.get("time_start") is None and "start" in window:
        row["time_start"] = window.get("start")
    if row.get("time_end") is None and "end" in window:
        row["time_end"] = window.get("end")
    if not row.get("name") and row.get("label"):
        row["name"] = str(row["label"])
    if not row.get("name") and row.get("description"):
        row["name"] = str(row["description"])
    if not row.get("expected_action"):
        action = row.get("step_kind") or row.get("expected_physical_action") or row.get("how")
        if action:
            row["expected_action"] = str(action)
    if not row.get("completion_condition"):
        completion = row.get("completion") or row.get("observable_completion") or row.get("completion_criteria")
        if completion:
            row["completion_condition"] = str(completion)
    if not row.get("required_objects") and isinstance(row.get("objects_involved_local_ids"), list):
        row["required_objects"] = row["objects_involved_local_ids"]
    if not row.get("required_objects") and isinstance(row.get("objects"), list):
        row["required_objects"] = row["objects"]
    if not row.get("action_local_ids") and isinstance(row.get("supporting_action_local_ids"), list):
        row["action_local_ids"] = row["supporting_action_local_ids"]
    if not row.get("action_local_ids") and isinstance(row.get("action_ids"), list):
        row["action_local_ids"] = row["action_ids"]
    if not row.get("observed_result") and row.get("observed_outcome"):
        row["observed_result"] = str(row["observed_outcome"])
    if not row.get("observed_result") and row.get("description") and row.get("name") != row.get("description"):
        row["observed_result"] = str(row["description"])
    preconditions = row.get("preconditions")
    if isinstance(preconditions, str):
        row["preconditions"] = [preconditions] if preconditions else []
    note = str(row.get("observed_result") or row.get("name") or "")
    row["evidence"] = _evidence(row.get("evidence") if isinstance(row.get("evidence"), dict) else {}, note)
    return _accepted(WorkflowStepPrediction, row)


def _align_dependency(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    from app.schemas.analysis import DependencyPrediction

    row = dict(item)
    if not row.get("from_step"):
        source = row.get("from_step_id") or row.get("from_step_local_id")
        if source:
            row["from_step"] = str(source)
    if not row.get("to_step"):
        source = row.get("to_step_id") or row.get("to_step_local_id")
        if source:
            row["to_step"] = str(source)
    if row.get("category") not in _DEPENDENCY:
        kind = row.get("dependency_kind") or row.get("dependency_type")
        if kind in _DEPENDENCY:
            row["category"] = kind
    if row.get("category") not in _DEPENDENCY:
        row["category"] = "unknown"
    if not row.get("from_step") or not row.get("to_step"):
        return None
    note = f"{row['from_step']} to {row['to_step']}"
    row["evidence"] = _evidence(row.get("evidence") if isinstance(row.get("evidence"), dict) else {}, note)
    return _accepted(DependencyPrediction, row)


def _fill_step_detail(data: dict[str, Any]) -> None:
    """Fill the three step sentences from actions and from a combined description.

    Some answers put the whole step in one description and leave expected_action
    and completion_condition empty. The page then shows a blank detail line and
    a blank Expected line. Overlapping action descriptions are the detail line,
    and the clause after a colon or semicolon is the expected end state.
    """
    steps = data.get("workflow_steps")
    actions = data.get("actions")
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        if not step.get("expected_action") and isinstance(actions, list):
            lines, ids = _actions_during(step, actions)
            if lines:
                step["expected_action"] = "; ".join(lines)
            if ids and not step.get("action_local_ids"):
                step["action_local_ids"] = ids
        if not step.get("completion_condition"):
            clause = _result_clause(str(step.get("observed_result") or "")) or _result_clause(
                str(step.get("expected_action") or "")
            )
            if clause:
                step["completion_condition"] = clause
            elif step.get("observed_result") and str(step.get("observed_result")).strip().lower() != str(step.get("name") or "").strip().lower():
                step["completion_condition"] = str(step["observed_result"])


def _actions_during(step: dict[str, Any], actions: list) -> tuple[list[str], list[str]]:
    start, end = step.get("time_start"), step.get("time_end")
    if start is None or end is None:
        return [], []
    lines: list[str] = []
    ids: list[str] = []
    linked = {str(item) for item in (step.get("action_local_ids") or [])}
    for action in actions:
        if not isinstance(action, dict):
            continue
        begin, finish = action.get("start"), action.get("end")
        if begin is None or finish is None or finish <= start or begin >= end:
            continue
        local_id = str(action.get("local_id") or "")
        if linked and local_id and local_id not in linked:
            continue
        text = str(action.get("description") or action.get("action_type") or "").strip()
        if text and text not in lines:
            lines.append(text)
        if local_id and local_id not in ids:
            ids.append(local_id)
    return lines, ids


def _result_clause(text: str) -> str:
    for separator in (":", ";"):
        if separator not in text:
            continue
        tail = text.split(separator, 1)[1].strip()
        tail = re.sub(r"^(?:the )?observed result is\s+", "", tail, flags=re.IGNORECASE)
        if tail:
            return tail
    return ""


def _bind_step_names(data: dict[str, Any]) -> None:
    """Dependencies sometimes name a step by its label instead of its id."""
    steps = data.get("workflow_steps")
    deps = data.get("dependencies")
    if not isinstance(steps, list) or not isinstance(deps, list):
        return
    ids: set[str] = set()
    by_name: dict[str, str] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        local_id = str(step.get("local_id") or "")
        if not local_id:
            continue
        ids.add(local_id)
        name = str(step.get("name") or "").strip().lower()
        if name:
            by_name[name] = local_id
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        for key in ("from_step", "to_step"):
            value = str(dep.get(key) or "")
            if value in ids:
                continue
            match = by_name.get(value.strip().lower())
            if match:
                dep[key] = match


_RELATION_ALIAS = {
    "attached_to": "attached",
    "inside_or_seated_in": "inside",
    "seated_in": "inside",
    "holding": "held_by",
    "held": "held_by",
}


def _align_relationship(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    from app.schemas.analysis import RelationshipPrediction

    row = dict(item)
    if not row.get("subject_local_id"):
        subject = row.get("object_a") or row.get("subject")
        if subject:
            row["subject_local_id"] = str(subject)
    if not row.get("other_local_id"):
        other = row.get("object_b") or row.get("other")
        if other:
            row["other_local_id"] = str(other)
    kind = str(row.get("kind") or row.get("relation") or row.get("label") or "unknown")
    kind = _RELATION_ALIAS.get(kind, kind)
    if kind not in _RELATIONS:
        return None
    row["kind"] = kind
    if not row.get("description"):
        row["description"] = str(row.get("label") or kind)
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    if not evidence and row.get("evidence_classification"):
        evidence = {
            "classification": row["evidence_classification"],
            "provenance": row.get("provenance") or "AI_PREDICTION",
        }
    row["evidence"] = _evidence(evidence, str(row.get("description") or kind))
    return _accepted(RelationshipPrediction, row)


def _align_ambiguity(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    from app.schemas.analysis import AmbiguityPrediction
    from app.schemas.enums import AmbiguityKind

    row = dict(item)
    if not row.get("local_id"):
        row["local_id"] = f"Ambiguity_{index}"
    if not row.get("description") and row.get("label"):
        row["description"] = str(row["label"])
    if row.get("kind") not in {kind.value for kind in AmbiguityKind}:
        row["kind"] = _ambiguity_kind(str(row.get("label") or ""), str(row.get("description") or ""))
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    row["evidence"] = _evidence(evidence, str(row.get("description") or ""))
    return _accepted(AmbiguityPrediction, row)


def _ambiguity_kind(label: str, description: str) -> str:
    label_text = label.lower()
    text = f"{label} {description}".lower()
    if "occlu" in label_text:
        return "occlusion"
    if "unverifiable" in text or "completion" in text:
        return "unverifiable_completion"
    if "identity" in text:
        return "uncertain_identity"
    if "occlu" in text:
        return "occlusion"
    if any(word in text for word in ("gap", "coverage", "sampled", "unobserved")):
        return "insufficient_coverage"
    if "camera" in text:
        return "camera_motion"
    if "state" in text:
        return "ambiguous_state"
    if "conflict" in text:
        return "conflicting_evidence"
    if "missing" in text:
        return "missing_evidence"
    return "alternative_interpretation"


def _pair_relationships(raw: Any) -> list[dict[str, Any]]:
    from app.schemas.analysis import RelationshipPrediction

    if not isinstance(raw, list):
        return []
    kept = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ids = item.get("object_ids")
        if not isinstance(ids, list) or len(ids) != 2:
            continue
        validity = item.get("validity") if isinstance(item.get("validity"), dict) else {}
        kind = str(item.get("relation") or item.get("kind") or "unknown")
        if kind not in _RELATIONS:
            continue
        row = {
            "subject_local_id": str(ids[0]),
            "other_local_id": str(ids[1]),
            "kind": kind,
            "time_start": validity.get("start", item.get("time_start", 0)),
            "time_end": validity.get("end", item.get("time_end", 0)),
            "description": str(item.get("description") or ""),
            "evidence": _evidence({}, str(item.get("relation") or kind)),
        }
        accepted = _accepted(RelationshipPrediction, row)
        if accepted is not None:
            kept.append(accepted)
    return kept


def _evidence(raw: dict[str, Any], note: str) -> dict[str, Any]:
    classification = raw.get("classification")
    provenance = raw.get("provenance")
    if classification in _EVIDENCE_CLASS and provenance in _PROVENANCE:
        return raw
    return {
        "classification": "INFERRED",
        "provenance": "AI_PREDICTION",
        "note": note,
    }


def _accepted(model: type, row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return model.model_validate(row).model_dump(mode="json")
    except ValidationError:
        return None

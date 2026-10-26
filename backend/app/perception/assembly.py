"""Read insertion from a part's own extent, not from a narration of approach.

A free part that is carried to a host and then stops existing as its own
visible thing has been inserted. The seated remainder is inside the host, so
the frames do not show a separate silhouette. Treating that as "brought near,
completion unverified" drops the action the frames contain. Proximity alone
still is not insertion: the part has to have been separate, then meet the
host, and then not come back as its own part.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.perception.assignment import center, iou
from app.schemas.analysis import (
    ActionPrediction,
    AnalysisDocument,
    BBox,
    Evidence,
    RelationshipPrediction,
    StatePrediction,
    TransitionPrediction,
)
from app.schemas.enums import ActionOutcome, EvidenceClass, Provenance, RelationKind

CONTACT_GAP = 0.045
MOVE = 0.05
MIN_SIGHTINGS = 3
MIN_ABSENT = 2
BORDER = 0.02
# Actions whose wording is allowed to become an insertion. A pickup or a
# rotation that merely overlaps the interval stays what it is.
_REWRITABLE = {"insert", "approach", "move", "hold", "unknown", "align", "adjust", "push"}


@dataclass(frozen=True)
class Insertion:
    guest_id: str
    host_ids: tuple[str, ...]
    contact_time: float
    last_seen: float
    gone_time: float
    until: float


def apply_assembly(document: AnalysisDocument) -> AnalysisDocument:
    """Rewrite insertion actions and the steps that cite them from the tracks."""
    events = find_insertions(document)
    if not events:
        return document
    actions = list(document.actions)
    steps = list(document.workflow_steps)
    relationships = list(document.relationships)
    states = list(document.states)
    transitions = list(document.transitions)
    objects = list(document.objects)
    labels = {obj.local_id: obj.label or obj.local_id for obj in objects}
    for event in events:
        guest = labels.get(event.guest_id, event.guest_id)
        host = _host_phrase(event.host_ids, labels)
        statement = _statement(guest, host, event)
        action = _rewrite_action(actions, event, guest, host, statement)
        _rewrite_steps(steps, action.local_id, guest, host, statement, event)
        _ensure_relationship(relationships, event, statement)
        _ensure_state(states, transitions, event, statement)
        _set_final_state(objects, event, guest, host)
    return document.model_copy(
        update={
            "actions": actions,
            "workflow_steps": steps,
            "relationships": relationships,
            "states": states,
            "transitions": transitions,
            "objects": objects,
        }
    )


def find_insertions(document: AnalysisDocument) -> list[Insertion]:
    times = sorted({round(item.timestamp, 3) for item in document.observations})
    boxes = _boxes(document)
    events: list[Insertion] = []
    for guest_id, series in boxes.items():
        if len(series) < MIN_SIGHTINGS:
            continue
        last_time, last_box = series[-1]
        later = [timestamp for timestamp in times if timestamp > last_time + 1e-6]
        if len(later) < MIN_ABSENT or _on_border(last_box):
            continue
        start_time, start_box = series[0]
        if math.hypot(center(start_box)[0] - center(last_box)[0], center(start_box)[1] - center(last_box)[1]) < MOVE:
            continue
        early = set(_hosts_at(boxes, guest_id, start_time, start_box))
        hosts = [host for host in _hosts_at(boxes, guest_id, last_time, last_box) if host not in early]
        if not hosts:
            continue
        contact_time = last_time
        for timestamp, box in series:
            if any(host in hosts for host in _hosts_at(boxes, guest_id, timestamp, box)):
                contact_time = timestamp
                break
        events.append(
            Insertion(
                guest_id=guest_id,
                host_ids=tuple(hosts),
                contact_time=contact_time,
                last_seen=last_time,
                gone_time=later[0],
                until=later[-1],
            )
        )
    return events


def _boxes(document: AnalysisDocument) -> dict[str, list[tuple[float, tuple[float, float, float, float]]]]:
    grouped: dict[str, list[tuple[float, tuple[float, float, float, float]]]] = {}
    for observation in document.observations:
        if observation.bbox is None or observation.visibility.value == "absent":
            continue
        grouped.setdefault(observation.object_local_id, []).append(
            (round(observation.timestamp, 3), _tuple(observation.bbox))
        )
    for series in grouped.values():
        series.sort()
    return grouped


def _hosts_at(
    boxes: dict[str, list[tuple[float, tuple[float, float, float, float]]]],
    guest_id: str,
    timestamp: float,
    guest_box: tuple[float, float, float, float],
) -> list[str]:
    found: list[tuple[float, str]] = []
    for other_id, series in boxes.items():
        if other_id == guest_id:
            continue
        other = _at(series, timestamp)
        if other is None:
            continue
        gap = _gap(guest_box, other)
        if gap <= CONTACT_GAP or iou(guest_box, other) > 0:
            found.append((gap, other_id))
    found.sort()
    return [object_id for _gap_value, object_id in found]


def _at(
    series: list[tuple[float, tuple[float, float, float, float]]], timestamp: float
) -> tuple[float, float, float, float] | None:
    for time, box in series:
        if abs(time - timestamp) < 1e-6:
            return box
    return None


def _rewrite_action(actions: list[ActionPrediction], event: Insertion, guest: str, host: str, statement: str) -> ActionPrediction:
    chosen = _matching_action(actions, event)
    evidence = _evidence(event, statement)
    description = statement
    final_state = f"Inserted into {host}. The separate visible extent ends at {event.gone_time:.0f}s and does not return."
    if chosen is None:
        local_id = _fresh_id(actions)
        chosen = ActionPrediction(
            local_id=local_id,
            action_type="insert",
            description=description,
            start=event.contact_time,
            end=event.gone_time,
            actor=None,
            object_local_ids=[event.guest_id, *event.host_ids],
            initial_state=f"{guest} separate from {host}",
            final_state=final_state,
            outcome=ActionOutcome.COMPLETED,
            uncertainty=0.4,
            evidence=evidence,
        )
        actions.append(chosen)
        return chosen
    ids = list(dict.fromkeys([*chosen.object_local_ids, event.guest_id, *event.host_ids]))
    updated = chosen.model_copy(
        update={
            "action_type": "insert",
            "description": description,
            "initial_state": chosen.initial_state or f"{guest} separate from {host}",
            "final_state": final_state,
            "outcome": ActionOutcome.COMPLETED,
            "uncertainty": min(chosen.uncertainty, 0.4),
            "object_local_ids": ids,
            "evidence": evidence,
            "end": max(chosen.end, event.gone_time),
        }
    )
    index = actions.index(chosen)
    actions[index] = updated
    return updated


def _matching_action(actions: list[ActionPrediction], event: Insertion) -> ActionPrediction | None:
    window = (event.contact_time, event.gone_time)
    ranked: list[tuple[float, ActionPrediction]] = []
    for action in actions:
        if event.guest_id not in action.object_local_ids:
            continue
        if action.action_type not in _REWRITABLE:
            continue
        overlap = min(action.end, window[1]) - max(action.start, window[0])
        if overlap < -0.2 and not (action.start <= event.last_seen <= action.end):
            continue
        preference = 0 if action.action_type == "insert" else 1
        ranked.append((preference - max(overlap, 0), action))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _rewrite_steps(steps, action_id: str, guest: str, host: str, statement: str, event: Insertion) -> None:
    for index, step in enumerate(steps):
        if action_id not in step.action_local_ids:
            continue
        steps[index] = step.model_copy(
            update={
                "name": f"Insert the {guest} into {host}",
                "expected_action": f"Insert the {guest} into {host}",
                "completion_condition": (
                    f"The {guest}'s separate visible extent ends at {host} and the {guest} does not return as its own part"
                ),
                "observed_result": statement,
                "uncertainty": min(step.uncertainty, 0.4),
                "evidence": _evidence(event, statement),
            }
        )


def _ensure_relationship(relationships: list[RelationshipPrediction], event: Insertion, statement: str) -> None:
    host_id = event.host_ids[0]
    for index, relation in enumerate(relationships):
        kind = relation.kind.value if hasattr(relation.kind, "value") else relation.kind
        if relation.subject_local_id == event.guest_id and relation.other_local_id == host_id and kind == "inside":
            relationships[index] = relation.model_copy(
                update={
                    "time_start": event.contact_time,
                    "time_end": event.until,
                    "description": statement,
                    "evidence": _evidence(event, statement),
                }
            )
            return
    relationships.append(
        RelationshipPrediction(
            subject_local_id=event.guest_id,
            other_local_id=host_id,
            kind=RelationKind.INSIDE,
            time_start=event.contact_time,
            time_end=event.until,
            description=statement,
            evidence=_evidence(event, statement),
        )
    )


def _ensure_state(states: list[StatePrediction], transitions: list[TransitionPrediction], event: Insertion, statement: str) -> None:
    evidence = _evidence(event, statement)
    for index, state in enumerate(states):
        if state.object_local_id != event.guest_id:
            continue
        if state.time_end < event.last_seen - 1e-6 or state.time_start > event.until + 1e-6:
            continue
        text = f"{state.state_label} {state.description}".lower()
        if state.state_label == "inserted" or any(token in text for token in ("not clearly", "not_clear", "unverif", "unknown", "hidden")):
            previous = state.state_label
            states[index] = state.model_copy(
                update={
                    "state_label": "inserted",
                    "description": statement,
                    "time_start": min(state.time_start, event.gone_time),
                    "time_end": max(state.time_end, event.until),
                    "evidence": evidence,
                }
            )
            if previous != "inserted":
                transitions.append(
                    TransitionPrediction(
                        object_local_id=event.guest_id,
                        timestamp=event.gone_time,
                        from_state=previous,
                        to_state="inserted",
                        description=statement,
                        evidence=evidence,
                        certainty=0.55,
                    )
                )
            return
    states.append(
        StatePrediction(
            object_local_id=event.guest_id,
            time_start=event.gone_time,
            time_end=event.until,
            state_label="inserted",
            description=statement,
            evidence=evidence,
        )
    )


def _set_final_state(objects, event: Insertion, guest: str, host: str) -> None:
    for index, obj in enumerate(objects):
        if obj.local_id != event.guest_id:
            continue
        objects[index] = obj.model_copy(
            update={
                "final_state": (
                    f"Inserted into {host} by {event.gone_time:.0f}s. "
                    f"The {guest} was separate, met {host}, and its own visible extent then ended."
                )
            }
        )
        return


def _statement(guest: str, host: str, event: Insertion) -> str:
    if event.last_seen > event.contact_time + 1e-6:
        seen = (
            f"The {guest} is separate, then meets {host} at {event.contact_time:.0f}s "
            f"and is still its own visible part at {event.last_seen:.0f}s. "
        )
    else:
        seen = f"The {guest} is separate, then meets {host} at {event.contact_time:.0f}s. "
    return (
        seen
        + f"From {event.gone_time:.0f}s through {event.until:.0f}s it has no separate visible extent and does not return. "
        "That is an insertion: the free part goes into the host. "
        "A part left beside the host would still be visible as itself."
    )


def _host_phrase(host_ids: tuple[str, ...], labels: dict[str, str]) -> str:
    if len(host_ids) >= 2:
        return "the assembly"
    return f"the {labels.get(host_ids[0], host_ids[0])}"


def _evidence(event: Insertion, statement: str) -> Evidence:
    frames = [event.contact_time, event.last_seen, event.gone_time]
    unique = list(dict.fromkeys(frames))
    return Evidence(
        classification=EvidenceClass.INFERRED,
        provenance=Provenance.DETERMINISTIC,
        note=statement,
        frame_timestamps=unique,
        time_start=event.contact_time,
        time_end=event.until,
    )


def _fresh_id(actions: list[ActionPrediction]) -> str:
    taken = {action.local_id for action in actions}
    number = 1
    while f"Action_insert_{number}" in taken:
        number += 1
    return f"Action_insert_{number}"


def _tuple(bbox: BBox) -> tuple[float, float, float, float]:
    return (bbox.x, bbox.y, bbox.w, bbox.h)


def _gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    dx = max(0.0, b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]))
    dy = max(0.0, b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]))
    return math.hypot(dx, dy)


def _on_border(box: tuple[float, float, float, float]) -> bool:
    return box[0] <= BORDER or box[1] <= BORDER or box[0] + box[2] >= 1 - BORDER or box[1] + box[3] >= 1 - BORDER

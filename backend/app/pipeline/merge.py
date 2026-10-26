"""Keep records a window returned when a later merge call leaves that list empty.

A model asked to merge several windows can spend its output on objects and
actions and omit states, transitions, and relationships. Those lists are
restored from the windows. Object ids are rewritten when the merge reused an
id for a different label.
"""

from __future__ import annotations

from app.schemas.analysis import (
    AmbiguityPrediction,
    AnalysisDocument,
    DependencyPrediction,
    ObservationPrediction,
    RelationshipPrediction,
    StatePrediction,
    TransitionPrediction,
    WorkflowStepPrediction,
)


def restore_omitted_records(merged: AnalysisDocument, partials: list[AnalysisDocument]) -> AnalysisDocument:
    if len(partials) < 2:
        return merged
    maps = [_object_id_map(partial, merged) for partial in partials]
    observations = _union_observations(partials, maps, merged)
    states = list(merged.states) or _states(partials, maps)
    transitions = list(merged.transitions) or _transitions(partials, maps)
    relationships = list(merged.relationships) or _relationships(partials, maps)
    if merged.workflow_steps or merged.dependencies:
        steps, dependencies = list(merged.workflow_steps), list(merged.dependencies)
    else:
        steps, dependencies = _workflow(partials, maps)
    ambiguities = list(merged.ambiguities) or _ambiguities(partials, maps)
    unchanged = (
        observations == list(merged.observations)
        and states == list(merged.states)
        and transitions == list(merged.transitions)
        and relationships == list(merged.relationships)
        and steps == list(merged.workflow_steps)
        and dependencies == list(merged.dependencies)
        and ambiguities == list(merged.ambiguities)
    )
    if unchanged:
        return merged
    return merged.model_copy(
        update={
            "observations": observations,
            "states": states,
            "transitions": transitions,
            "relationships": relationships,
            "workflow_steps": steps,
            "dependencies": dependencies,
            "ambiguities": ambiguities,
        }
    )


def _union_observations(
    partials: list[AnalysisDocument],
    maps: list[dict[str, str]],
    merged: AnalysisDocument,
) -> list[ObservationPrediction]:
    """Keep a box a window actually saw when the text-only merge leaves that frame out.

    The merge's own box wins when both describe the same part at the same time.
    A window id that was not aligned to a merged part is left out: copying it
    through would put that part's box on a different object.
    """
    chosen: dict[tuple, ObservationPrediction] = {}
    order: list[tuple] = []

    def consider(item: ObservationPrediction, replace: bool) -> None:
        key = (item.object_local_id, round(float(item.timestamp), 3))
        current = chosen.get(key)
        if current is None:
            chosen[key] = item
            order.append(key)
            return
        if replace and item.bbox is not None:
            chosen[key] = item
            return
        if current.bbox is None and item.bbox is not None:
            chosen[key] = item

    for partial, mapping in zip(partials, maps, strict=True):
        for item in partial.observations:
            if item.object_local_id not in mapping:
                continue
            consider(item.model_copy(update={"object_local_id": mapping[item.object_local_id]}), replace=False)
    for item in merged.observations:
        consider(item, replace=True)
    return [chosen[key] for key in order]


def _object_id_map(partial: AnalysisDocument, merged: AnalysisDocument) -> dict[str, str]:
    from app.perception.ground import object_id_map

    return object_id_map(partial, merged)


def _obj(mapping: dict[str, str], local_id: str) -> str:
    return mapping.get(local_id, local_id)


def _states(partials: list[AnalysisDocument], maps: list[dict[str, str]]) -> list[StatePrediction]:
    seen: set[tuple] = set()
    rows: list[StatePrediction] = []
    for partial, mapping in zip(partials, maps, strict=True):
        for item in partial.states:
            copy = item.model_copy(update={"object_local_id": _obj(mapping, item.object_local_id)})
            key = (copy.object_local_id, round(copy.time_start, 3), round(copy.time_end, 3), copy.state_label)
            if key in seen:
                continue
            seen.add(key)
            rows.append(copy)
    return rows


def _transitions(partials: list[AnalysisDocument], maps: list[dict[str, str]]) -> list[TransitionPrediction]:
    seen: set[tuple] = set()
    rows: list[TransitionPrediction] = []
    for partial, mapping in zip(partials, maps, strict=True):
        for item in partial.transitions:
            copy = item.model_copy(update={"object_local_id": _obj(mapping, item.object_local_id)})
            key = (copy.object_local_id, round(copy.timestamp, 3), copy.from_state, copy.to_state)
            if key in seen:
                continue
            seen.add(key)
            rows.append(copy)
    return rows


def _relationships(partials: list[AnalysisDocument], maps: list[dict[str, str]]) -> list[RelationshipPrediction]:
    seen: set[tuple] = set()
    rows: list[RelationshipPrediction] = []
    for partial, mapping in zip(partials, maps, strict=True):
        for item in partial.relationships:
            other = _obj(mapping, item.other_local_id) if item.other_local_id else None
            copy = item.model_copy(
                update={
                    "subject_local_id": _obj(mapping, item.subject_local_id),
                    "other_local_id": other,
                }
            )
            key = (
                copy.subject_local_id,
                copy.other_local_id,
                copy.kind,
                round(copy.time_start, 3),
                round(copy.time_end, 3),
                copy.description,
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(copy)
    return rows


def _fresh(prefix: str, used: set[str]) -> str:
    number = 1
    while f"{prefix}_{number}" in used:
        number += 1
    fresh = f"{prefix}_{number}"
    used.add(fresh)
    return fresh


def _workflow(
    partials: list[AnalysisDocument], maps: list[dict[str, str]]
) -> tuple[list[WorkflowStepPrediction], list[DependencyPrediction]]:
    used: set[str] = set()
    steps: list[WorkflowStepPrediction] = []
    dependencies: list[DependencyPrediction] = []
    for partial, mapping in zip(partials, maps, strict=True):
        step_ids: dict[str, str] = {}
        for item in partial.workflow_steps:
            new_id = item.local_id if item.local_id not in used else _fresh("Step", used)
            used.add(new_id)
            step_ids[item.local_id] = new_id
            steps.append(
                item.model_copy(
                    update={
                        "local_id": new_id,
                        "required_objects": [_obj(mapping, value) for value in item.required_objects],
                    }
                )
            )
        for item in partial.dependencies:
            dependencies.append(
                item.model_copy(
                    update={
                        "from_step": step_ids.get(item.from_step, item.from_step),
                        "to_step": step_ids.get(item.to_step, item.to_step),
                    }
                )
            )
    return steps, dependencies


def _ambiguities(partials: list[AnalysisDocument], maps: list[dict[str, str]]) -> list[AmbiguityPrediction]:
    used: set[str] = set()
    rows: list[AmbiguityPrediction] = []
    for partial, mapping in zip(partials, maps, strict=True):
        for item in partial.ambiguities:
            new_id = item.local_id if item.local_id not in used else _fresh("Ambiguity", used)
            used.add(new_id)
            rows.append(
                item.model_copy(
                    update={
                        "local_id": new_id,
                        "object_local_ids": [_obj(mapping, value) for value in item.object_local_ids],
                    }
                )
            )
    return rows

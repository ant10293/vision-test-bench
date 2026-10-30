"""Bind model output to tracked instances without letting labels merge them."""

from __future__ import annotations

from app.schemas.analysis import AmbiguityPrediction, AnalysisDocument, Evidence
from app.schemas.enums import AmbiguityKind, EvidenceClass, Provenance


def object_id_map(partial: AnalysisDocument, merged: AnalysisDocument) -> dict[str, str]:
    """Map window ids onto the merged document.

    Identical labels do not merge two records that carry different instance ids.
    Records without an instance id still match by label, which is the VLM-only path.
    """
    merged_by_instance = {obj.instance_id: obj.local_id for obj in merged.objects if obj.instance_id}
    merged_labels = {obj.local_id: " ".join(obj.label.casefold().split()) for obj in merged.objects}
    labels = {
        " ".join(obj.label.casefold().split()): obj.local_id
        for obj in reversed(merged.objects)
        if not obj.instance_id
    }
    mapping: dict[str, str] = {}
    for obj in partial.objects:
        if obj.instance_id:
            mapping[obj.local_id] = merged_by_instance.get(obj.instance_id, obj.local_id)
            continue
        key = " ".join(obj.label.casefold().split())
        if merged_labels.get(obj.local_id) == key:
            mapping[obj.local_id] = obj.local_id
            continue
        matched = labels.get(key)
        if matched:
            mapping[obj.local_id] = matched
        # A reused id with a different label is a different part. Leaving it
        # unmapped stops that box from landing on the earlier part.
    return mapping


def apply_perception(document: AnalysisDocument, spatial: AnalysisDocument | None) -> AnalysisDocument:
    """Copy tracked instances onto a model document and flag unknown ids.

    Object labels are not replaced. An id the tracker did not produce stays a
    provisional proposal. Geometric overlap is not copied in as attachment.
    """
    if spatial is None or spatial.perception is None:
        return document
    perception = spatial.perception
    if not perception.used_for_identity:
        return document.model_copy(update={"perception": perception})
    known = {item.instance_id for item in perception.instances}
    objects = []
    ambiguities = list(document.ambiguities)
    for obj in document.objects:
        if obj.instance_id and obj.instance_id not in known:
            objects.append(obj.model_copy(update={"uncertain_identity": True, "identity_relation": "provisional"}))
            ambiguities.append(
                AmbiguityPrediction(
                    local_id=f"Ambiguity_untracked_{obj.local_id}",
                    kind=AmbiguityKind.UNCERTAIN_IDENTITY,
                    description=(
                        f"{obj.local_id} names instance {obj.instance_id}, which is not in the "
                        "tracked registry. It is a provisional proposal, not a confirmed object."
                    ),
                    object_local_ids=[obj.local_id],
                    evidence=Evidence(
                        classification=EvidenceClass.UNKNOWN,
                        provenance=Provenance.DETERMINISTIC,
                        note="The language model did not receive this id from the tracker.",
                    ),
                )
            )
        elif obj.instance_id:
            objects.append(obj.model_copy(update={"identity_relation": obj.identity_relation or "physical_instance"}))
        else:
            objects.append(obj.model_copy(update={"identity_relation": obj.identity_relation or "semantic_label"}))
    return document.model_copy(update={"perception": perception, "objects": objects, "ambiguities": ambiguities})


def apply_geometry(document: AnalysisDocument, spatial: AnalysisDocument | None) -> AnalysisDocument:
    """Copy held image-plane turns and depth order from the tracks onto a model document.

    Records are matched through the tracked instance id. A model object that
    does not carry a tracked id gets none. Earlier copies are replaced, so a
    later stage does not duplicate them.
    """
    if spatial is None or spatial.perception is None or not spatial.perception.used_for_identity:
        return document
    from app.perception.geometry import is_geometry_record

    instance_of = {obj.local_id: obj.instance_id for obj in spatial.objects if obj.instance_id}
    target = {obj.instance_id: obj.local_id for obj in document.objects if obj.instance_id}
    mapping = {
        local_id: target[instance_id] for local_id, instance_id in instance_of.items() if instance_id in target
    }
    transitions = [item for item in document.transitions if not is_geometry_record(item)]
    relationships = [item for item in document.relationships if not is_geometry_record(item)]
    for item in spatial.transitions:
        if is_geometry_record(item) and item.object_local_id in mapping:
            transitions.append(item.model_copy(update={"object_local_id": mapping[item.object_local_id]}))
    for item in spatial.relationships:
        if not is_geometry_record(item):
            continue
        subject = mapping.get(item.subject_local_id)
        other = mapping.get(item.other_local_id or "")
        if subject and other and subject != other:
            relationships.append(item.model_copy(update={"subject_local_id": subject, "other_local_id": other}))
    if transitions == list(document.transitions) and relationships == list(document.relationships):
        return document
    return document.model_copy(update={"transitions": transitions, "relationships": relationships})

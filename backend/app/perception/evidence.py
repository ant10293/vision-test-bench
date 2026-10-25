"""Compact tracked-object context for a language model. No embedding vectors."""

from __future__ import annotations

from app.pipeline.prompts import TRACKED_OBJECTS
from app.schemas.analysis import AnalysisDocument


def evidence_text(document: AnalysisDocument, limit: int = 24) -> str:
    perception = document.perception
    if perception is None or not perception.used_for_identity:
        return ""
    lines = [TRACKED_OBJECTS, "Tracked instances:"]
    for instance in perception.instances[:limit]:
        lines.append(
            f"- {instance.instance_id}: status {instance.status}, "
            f"visible {instance.first_seen:.2f}s–{instance.last_seen:.2f}s, "
            f"observations {instance.observation_count}, "
            f"uncertain {str(instance.uncertain).lower()}."
        )
    if len(perception.instances) > limit:
        lines.append(f"{len(perception.instances) - limit} further instances were omitted for length.")
    open_hypotheses = [
        item
        for item in perception.hypotheses
        if item.decision in {"proposed", "ambiguous"}
    ][:8]
    if open_hypotheses:
        lines.append("Unconfirmed identity hypotheses (scores are not probabilities):")
        for item in open_hypotheses:
            cosine = "n/a" if item.appearance_cosine is None else f"{item.appearance_cosine:.2f}"
            lines.append(
                f"- {item.hypothesis_id} {item.decision}: {item.subject_instance_id} "
                f"versus {item.candidate_instance_id}, appearance cosine {cosine}, "
                f"association score {item.association_score:.2f}."
            )
    lines.append(
        "Use these instance ids when you refer to a tracked object. "
        "If you describe something that is not in the list, set identity_relation to provisional "
        "and do not reuse an existing instance id."
    )
    return "\n".join(lines)

"""Associate SAM tracks with LearnLoop objects and fuse geometry.

SAM supplies boxes, masks, and track identity inside one response. It does not
supply actions, relations, or completion verdicts. A frame SAM did not return
stays unobserved. It is not recorded as absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.perception.assignment import iou
from app.providers.sam import (
    MAX_PROMPTED_OBJECTS,
    SpatialMark,
    VideoChunk,
    concept_text,
    scoped_track_id,
)
from app.providers.types import ProviderError
from app.schemas.analysis import (
    AmbiguityPrediction,
    AnalysisDocument,
    BBox,
    Evidence,
    HypothesisRecord,
    ObservationPrediction,
    PerceptionRecord,
)
from app.schemas.enums import (
    AmbiguityKind,
    Continuity,
    EvidenceClass,
    Provenance,
    Visibility,
)
from app.services.storage import data_path

SUMMARY_STAGES = {
    "action_analysis",
    "state_tracking",
    "workflow",
    "ambiguity",
    "evidence_fusion",
    "reinspection",
}
ACCEPT_IOU = 0.35
MARGIN = 0.12
CONFLICT_IOU = 0.2
TIME_WINDOW = 0.45
_TRAILING_INSTANCE = re.compile(
    r"(?:\s+(?:first|second|third|fourth|fifth|another|copy|\d+))+$",
    re.IGNORECASE,
)


@dataclass
class TrackSample:
    session_id: str
    raw_track_id: str
    timestamp: float
    bbox: BBox
    confidence: float | None
    frame_index: int
    request_id: str = ""
    bound_object_id: str = ""
    mask_ref: str = ""
    mask_data: str = ""
    mask_payload: str = ""
    mask_encoding: str = ""
    mask_height: int = 0
    mask_width: int = 0
    source_width: int = 0
    source_height: int = 0
    prompt_kind: str = ""
    candidates: tuple[str, ...] = ()


@dataclass
class TrackAssociation:
    session_id: str
    raw_track_id: str
    object_local_id: str | None
    status: str
    score: float
    margin: float | None
    rivals: list[str] = field(default_factory=list)
    note: str = ""
    replaces_box: bool = False

    @property
    def scoped_id(self) -> str:
        return scoped_track_id(self.session_id, self.raw_track_id)


@dataclass
class SamRequestPlan:
    kind: str
    prompt: str
    session_id: str
    bindings: dict[int, str]
    propagation: str | None
    labels: list[str]
    targets: list[str] = field(default_factory=list)


def plan_prompts(
    document: AnalysisDocument,
    chunk: VideoChunk,
    width: int,
    height: int,
    fps: float,
) -> list[SamRequestPlan]:
    """One concept prompt per noun. The reasoning model's box is not the prompt.

    A point on a bad rectangle makes SAM segment that spot, so the outline
    inherits the wrong size and place. The noun lets SAM find the part.
    Two objects that share a noun share one request. Concept text never
    includes a box or a point.
    """
    del width, height, fps
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for obj in document.objects:
        try:
            noun = _concept_noun(obj.label)
        except ProviderError:
            continue
        if noun not in groups:
            order.append(noun)
            groups[noun] = []
        if obj.local_id not in groups[noun]:
            groups[noun].append(obj.local_id)
    plans: list[SamRequestPlan] = []
    for noun in order[:MAX_PROMPTED_OBJECTS]:
        targets = groups[noun]
        plans.append(
            SamRequestPlan(
                kind="concept",
                prompt=noun,
                session_id=f"{chunk.session_id}s{len(plans)}",
                bindings={},
                propagation=None,
                labels=[noun],
                targets=targets,
            )
        )
    return plans


def associate_tracks(
    document: AnalysisDocument,
    samples: list[TrackSample],
    *,
    scene_boundaries: list[float] | None = None,
) -> list[TrackAssociation]:
    """Link a track to an object by overlap, prompted id, and uniqueness.

    Matching labels are ignored. Two similar parts stay separate unless their
    boxes agree. A close score keeps both candidates instead of picking one.
    """
    del scene_boundaries  # gaps are recorded in the summary; they do not force a match
    grouped: dict[tuple[str, str], list[TrackSample]] = {}
    for sample in samples:
        grouped.setdefault((sample.session_id, sample.raw_track_id), []).append(sample)
    by_session: dict[str, list[tuple[str, list[TrackSample]]]] = {}
    for (session_id, raw_id), rows in grouped.items():
        by_session.setdefault(session_id, []).append((raw_id, rows))

    associations: list[TrackAssociation] = []
    for session_id, tracks in by_session.items():
        concept = _concept_associations(document, session_id, tracks)
        if concept is not None:
            associations.extend(concept)
            continue
        claimed: set[str] = set()
        scored: list[tuple[float, float, str, str, list[str], str]] = []
        for raw_id, rows in tracks:
            binding = next((row.bound_object_id for row in rows if row.bound_object_id), "")
            ranking = _rank_objects(document, rows, binding)
            if binding and ranking and ranking[0][1] < CONFLICT_IOU:
                associations.append(
                    TrackAssociation(
                        session_id,
                        raw_id,
                        binding,
                        "conflict",
                        ranking[0][1],
                        None,
                        rivals=[binding],
                        note=(
                            "The prompted object did not overlap this track. "
                            "The track was kept as a conflict and was not forced onto the object."
                        ),
                    )
                )
                continue
            if not ranking:
                associations.append(
                    TrackAssociation(
                        session_id,
                        raw_id,
                        None,
                        "unassigned",
                        0.0,
                        None,
                        note="No spatial overlap linked this track to a known object. The label was not used.",
                    )
                )
                continue
            best_id, best_score = ranking[0]
            second = ranking[1][1] if len(ranking) > 1 else 0.0
            margin = best_score - second
            rivals = [item[0] for item in ranking[:3]]
            scored.append((best_score, margin, raw_id, best_id, rivals, binding))
        scored.sort(key=lambda item: item[0], reverse=True)
        for best_score, margin, raw_id, best_id, rivals, binding in scored:
            if best_score < ACCEPT_IOU:
                status = "ambiguous" if best_score > 0 and margin < MARGIN else "unassigned"
                associations.append(
                    TrackAssociation(
                        session_id,
                        raw_id,
                        None if status != "ambiguous" else None,
                        status,
                        best_score,
                        margin,
                        rivals=rivals,
                        note=_ambiguous_note(rivals, binding, best_score),
                    )
                )
                continue
            if margin < MARGIN and not binding:
                associations.append(
                    TrackAssociation(
                        session_id,
                        raw_id,
                        None,
                        "ambiguous",
                        best_score,
                        margin,
                        rivals=rivals,
                        note="Competing objects overlap this track. Neither was selected.",
                    )
                )
                continue
            if best_id in claimed:
                associations.append(
                    TrackAssociation(
                        session_id,
                        raw_id,
                        None,
                        "ambiguous",
                        best_score,
                        margin,
                        rivals=rivals,
                        note=(
                            f"{best_id} already has a track in this SAM response. "
                            "A second track was not merged into it."
                        ),
                    )
                )
                continue
            claimed.add(best_id)
            associations.append(
                TrackAssociation(
                    session_id,
                    raw_id,
                    best_id,
                    "associated",
                    best_score,
                    margin,
                    rivals=rivals,
                    note="Linked by spatiotemporal overlap inside one SAM response.",
                )
            )
    return associations


def fuse_geometry(
    document: AnalysisDocument,
    samples: list[TrackSample],
    associations: list[TrackAssociation],
    *,
    scene_boundaries: list[float] | None = None,
    coverage_note: str = "",
) -> AnalysisDocument:
    """Add SAM boxes beside existing evidence. Do not delete a stronger observation."""
    lookup = {(item.session_id, item.raw_track_id): item for item in associations}
    kept = [item for item in document.observations if item.source_model != "sam-3.1"]
    ambiguities = [item for item in document.ambiguities if not item.local_id.startswith("sam_")]
    added: list[ObservationPrediction] = []
    extra_ambiguities: list[AmbiguityPrediction] = []
    boundaries = list(scene_boundaries or [])
    for sample in samples:
        association = lookup.get((sample.session_id, sample.raw_track_id))
        if association is None or association.object_local_id is None:
            continue
        if association.status not in {"associated", "conflict"}:
            continue
        status, continuity, note = _placement(kept, association, sample, boundaries)
        added.append(
            ObservationPrediction(
                object_local_id=association.object_local_id,
                timestamp=sample.timestamp,
                bbox=sample.bbox,
                visibility=Visibility.VISIBLE,
                description="SAM 3.1 segmentation",
                evidence=Evidence(
                    classification=EvidenceClass.DIRECTLY_OBSERVED,
                    provenance=Provenance.CV_DETECTION,
                    note=note,
                    frame_timestamps=[sample.timestamp],
                ),
                confidence=sample.confidence,
                continuity=continuity,
                track_ref=association.scoped_id,
                association_status=status,
                source_model="sam-3.1",
                request_id=sample.request_id,
                mask_ref=sample.mask_ref,
            )
        )
        if status == "conflict":
            extra_ambiguities.append(
                AmbiguityPrediction(
                    local_id=f"sam_conflict_{association.scoped_id}_{sample.frame_index}",
                    kind=AmbiguityKind.CONFLICTING_EVIDENCE,
                    description=(
                        f"SAM and the reasoning model disagree on {association.object_local_id} "
                        f"at {sample.timestamp:.2f}s. Both boxes were kept."
                    ),
                    time_start=sample.timestamp,
                    time_end=sample.timestamp,
                    object_local_ids=[association.object_local_id],
                    evidence=Evidence(
                        classification=EvidenceClass.DIRECTLY_OBSERVED,
                        provenance=Provenance.CV_DETECTION,
                        note="Conflicting localizations were preserved.",
                    ),
                )
            )
    for association in associations:
        if association.status not in {"ambiguous", "unassigned"}:
            continue
        rows = [
            sample
            for sample in samples
            if sample.session_id == association.session_id and sample.raw_track_id == association.raw_track_id
        ]
        if not rows:
            continue
        start = min(sample.timestamp for sample in rows)
        end = max(sample.timestamp for sample in rows)
        kind = (
            AmbiguityKind.UNCERTAIN_IDENTITY
            if association.status == "ambiguous"
            else AmbiguityKind.MISSING_EVIDENCE
        )
        extra_ambiguities.append(
            AmbiguityPrediction(
                local_id=f"sam_{association.status}_{association.scoped_id}",
                kind=kind,
                description=association.note,
                time_start=start,
                time_end=end,
                object_local_ids=association.rivals[:2],
                evidence=Evidence(
                    classification=EvidenceClass.UNKNOWN,
                    provenance=Provenance.CV_DETECTION,
                    note="This track was not assigned to an object.",
                ),
            )
        )
    for start, end, local_id in _gap_intervals(samples, lookup):
        extra_ambiguities.append(
            AmbiguityPrediction(
                local_id=f"sam_gap_{local_id}_{start:.2f}",
                kind=AmbiguityKind.UNOBSERVED_INTERVAL,
                description=(
                    f"{local_id} has no SAM observation from {start:.2f}s to {end:.2f}s. "
                    "That gap is not evidence the object is absent."
                ),
                time_start=start,
                time_end=end,
                object_local_ids=[local_id],
                evidence=Evidence(
                    classification=EvidenceClass.UNKNOWN,
                    provenance=Provenance.CV_DETECTION,
                    note="Unobserved interval. Not confirmed absence.",
                ),
            )
        )
    summary = render_summary(document, samples, associations, coverage_note)
    perception = _perception(document, associations, summary)
    observations = [*kept, *added]
    observations.sort(key=lambda item: (item.timestamp, item.object_local_id, item.source_model))
    return document.model_copy(
        update={
            "observations": observations,
            "ambiguities": ambiguities + extra_ambiguities,
            "perception": perception,
        }
    )


def render_summary(
    document: AnalysisDocument,
    samples: list[TrackSample],
    associations: list[TrackAssociation],
    coverage_note: str = "",
) -> str:
    """Compact text for a later reasoning call. Masks are not included."""
    lines = [
        "Roles: the primary model owns actions, relations, and workflow judgments. "
        "SAM 3.1 owns boxes, masks, and track identity inside one response.",
    ]
    if coverage_note:
        lines.append(coverage_note)
    lookup = {(item.session_id, item.raw_track_id): item for item in associations}
    labels = {obj.local_id: obj.label for obj in document.objects}
    grouped: dict[str, list[TrackSample]] = {}
    for sample in samples:
        association = lookup.get((sample.session_id, sample.raw_track_id))
        key = association.object_local_id if association and association.object_local_id else association.scoped_id if association else sample.raw_track_id
        grouped.setdefault(key, []).append(sample)
    for key, rows in grouped.items():
        rows.sort(key=lambda item: item.timestamp)
        association = lookup.get((rows[0].session_id, rows[0].raw_track_id))
        status = association.status if association else "unassigned"
        label = labels.get(key, "")
        name = f"{key} {label}".strip()
        start = rows[0].timestamp
        end = rows[-1].timestamp
        conf = [row.confidence for row in rows if row.confidence is not None]
        conf_text = f" sam_confidence {min(conf):.2f}-{max(conf):.2f}" if conf else ""
        lines.append(
            f"{name}: track {association.scoped_id if association else rows[0].raw_track_id} "
            f"{status} {start:.2f}-{end:.2f}s samples {len(rows)}{conf_text}"
        )
    if not samples:
        lines.append("SAM returned no tracks. That is an empty detection, not confirmed absence.")
    lines.append(
        "Intervals without a sample were not observed. Do not convert them into absence, "
        "and do not invent an action from a box."
    )
    return "\n".join(lines)


def write_sam_summary(run_id: str, text: str) -> None:
    path = data_path("runs", run_id, "sam_summary.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def prompt_prior(document: AnalysisDocument) -> str:
    """JSON for a later model call, without per-frame SAM rows.

    A tracked clip can hold thousands of boxes. Those stay in the stored
    document. The call gets the reasoning-model records plus the short SAM summary.
    """
    compact = document.model_copy(
        update={
            "observations": [item for item in document.observations if item.source_model != "sam-3.1"],
            "ambiguities": [item for item in document.ambiguities if not item.local_id.startswith("sam_")],
        }
    )
    text = compact.model_dump_json()
    if len(text) <= 120_000:
        return text
    shorter = compact.model_copy(update={"observations": compact.observations[:48]})
    return (
        shorter.model_dump_json()
        + "\n\nObservation list shortened to stay inside the model context window."
    )


def sam_evidence_block(run_id: str, stage: str) -> str:
    if stage not in SUMMARY_STAGES:
        return ""
    path = data_path("runs", run_id, "sam_summary.txt")
    if not path.exists():
        return ""
    body = path.read_text()[:6000]
    if not body.strip():
        return ""
    return (
        "SAM 3.1 geometric evidence from a separate model. "
        "Use it for location, continuity, and whether a step has visual support. "
        "Do not treat it as an action or completion authority. "
        "A missing detection is unobserved, not confirmed absence. "
        "Its confidence is not calibrated against yours.\n"
        + body
    )


def merge_reconciliation(base: AnalysisDocument, proposed: AnalysisDocument) -> AnalysisDocument:
    """Keep SAM geometry. Accept refined actions, states, and workflow steps."""
    sam_ambiguities = [item for item in base.ambiguities if item.local_id.startswith("sam_")]
    proposed_ambiguities = [
        item for item in (proposed.ambiguities or []) if not item.local_id.startswith("sam_")
    ]
    return base.model_copy(
        update={
            "summary": proposed.summary or base.summary,
            "actions": proposed.actions or base.actions,
            "states": proposed.states or base.states,
            "transitions": proposed.transitions or base.transitions,
            "relationships": proposed.relationships or base.relationships,
            "workflow_steps": proposed.workflow_steps or base.workflow_steps,
            "dependencies": proposed.dependencies or base.dependencies,
            "ambiguities": sam_ambiguities + (proposed_ambiguities or [
                item for item in base.ambiguities if not item.local_id.startswith("sam_")
            ]),
        }
    )


def _concept_noun(label: str) -> str:
    text = concept_text(label)
    noun = _TRAILING_INSTANCE.sub("", text).strip()
    return noun or text


def _concept_associations(
    document: AnalysisDocument,
    session_id: str,
    tracks: list[tuple[str, list[TrackSample]]],
) -> list[TrackAssociation] | None:
    """Give each prompted object the SAM instance of its noun.

    Overlap with the reasoning model's box is not required. That box is what
    was wrong. Extra instances of the same noun stay unassigned.
    """
    if not any(row.prompt_kind == "concept" for _, rows in tracks for row in rows):
        return None
    targets: list[str] = []
    for _, rows in tracks:
        for row in rows:
            for local_id in row.candidates:
                if local_id not in targets:
                    targets.append(local_id)
    note = "SAM found this part from its noun. The reasoning model's box was not the prompt."
    if len(targets) <= 1:
        target = targets[0] if targets else None
        ranked = sorted(tracks, key=lambda item: _mean_confidence(item[1]), reverse=True)
        linked: list[TrackAssociation] = []
        for index, (raw_id, _rows) in enumerate(ranked):
            if index == 0 and target:
                linked.append(
                    TrackAssociation(
                        session_id,
                        raw_id,
                        target,
                        "associated",
                        1.0,
                        None,
                        rivals=[target],
                        note=note,
                        replaces_box=True,
                    )
                )
            else:
                linked.append(
                    TrackAssociation(
                        session_id,
                        raw_id,
                        None,
                        "unassigned",
                        0.0,
                        None,
                        note="Another instance of this noun was not merged into the prompted object.",
                    )
                )
        return linked
    pairs: list[tuple[float, str, str]] = []
    for raw_id, rows in tracks:
        center = _mean_center(rows)
        for target in targets:
            sighting = _vlm_center(document, target)
            distance = 1e9 if sighting is None else _distance(center, sighting)
            pairs.append((distance, raw_id, target))
    pairs.sort(key=lambda item: item[0])
    used_tracks: set[str] = set()
    used_targets: set[str] = set()
    linked = []
    for _gap, raw_id, target in pairs:
        if raw_id in used_tracks or target in used_targets:
            continue
        used_tracks.add(raw_id)
        used_targets.add(target)
        linked.append(
            TrackAssociation(
                session_id,
                raw_id,
                target,
                "associated",
                1.0,
                None,
                rivals=[target],
                note=note,
                replaces_box=True,
            )
        )
    for raw_id, _rows in tracks:
        if raw_id in used_tracks:
            continue
        linked.append(
            TrackAssociation(
                session_id,
                raw_id,
                None,
                "unassigned",
                0.0,
                None,
                note="Another instance of this noun was not merged into a prompted object.",
            )
        )
    return linked


def _mean_confidence(rows: list[TrackSample]) -> float:
    scores = [row.confidence for row in rows if row.confidence is not None]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _mean_center(rows: list[TrackSample]) -> tuple[float, float]:
    xs = [row.bbox.x + row.bbox.w / 2 for row in rows]
    ys = [row.bbox.y + row.bbox.h / 2 for row in rows]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _vlm_center(document: AnalysisDocument, local_id: str) -> tuple[float, float] | None:
    boxes = [
        item.bbox
        for item in document.observations
        if item.object_local_id == local_id and item.bbox is not None and item.source_model != "sam-3.1"
    ]
    if not boxes:
        return None
    xs = [box.x + box.w / 2 for box in boxes]
    ys = [box.y + box.h / 2 for box in boxes]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5


def _mark(frame: int, object_id: int, box: BBox, width: int, height: int) -> SpatialMark:
    """Prompt with the center of the sighting, not the rectangle itself."""
    x = min(max(int(round((box.x + box.w / 2) * width)), 0), max(width - 1, 0))
    y = min(max(int(round((box.y + box.h / 2) * height)), 0), max(height - 1, 0))
    return SpatialMark(
        frame_index=frame,
        object_id=object_id,
        kind="point",
        width=width,
        height=height,
        x=x,
        y=y,
    )


def _rank_objects(document: AnalysisDocument, rows: list[TrackSample], binding: str) -> list[tuple[str, float]]:
    if binding:
        score = _overlap(document, binding, rows)
        return [(binding, score)]
    scores = []
    for obj in document.objects:
        score = _overlap(document, obj.local_id, rows)
        if score > 0:
            scores.append((obj.local_id, score))
    scores.sort(key=lambda item: item[1], reverse=True)
    return scores


def _overlap(document: AnalysisDocument, local_id: str, rows: list[TrackSample]) -> float:
    hits = []
    for sample in rows:
        boxes = [
            item.bbox
            for item in document.observations
            if item.object_local_id == local_id
            and item.bbox is not None
            and item.source_model != "sam-3.1"
            and abs(item.timestamp - sample.timestamp) <= TIME_WINDOW
        ]
        if not boxes:
            continue
        hits.append(max(iou(_tuple(sample.bbox), _tuple(box)) for box in boxes))
    if not hits:
        return 0.0
    return sum(hits) / len(hits)


def _placement(existing, association: TrackAssociation, sample: TrackSample, boundaries: list[float]):
    status = association.status
    continuity = Continuity.CONTINUOUS
    note = association.note
    if association.replaces_box:
        if _crosses(sample.timestamp, boundaries):
            continuity = Continuity.UNCERTAIN
            note += " A scene boundary is nearby, so this sighting was not treated as continuous."
        return status, continuity, note.strip()
    for item in existing:
        if item.object_local_id != association.object_local_id or item.bbox is None:
            continue
        if abs(item.timestamp - sample.timestamp) > TIME_WINDOW:
            continue
        if item.evidence.classification != EvidenceClass.DIRECTLY_OBSERVED:
            continue
        if item.evidence.provenance == Provenance.CV_DETECTION:
            continue
        overlap = iou(_tuple(item.bbox), _tuple(sample.bbox))
        if overlap < CONFLICT_IOU:
            status = "conflict"
            continuity = Continuity.UNCERTAIN
            note = (
                "SAM box conflicts with a directly observed box. The observed box was kept "
                "and this box was stored beside it."
            )
            break
    if _crosses(sample.timestamp, boundaries):
        continuity = Continuity.UNCERTAIN
        note += " A scene boundary is nearby, so this sighting was not treated as continuous."
    return status, continuity, note.strip()


def _crosses(timestamp: float, boundaries: list[float]) -> bool:
    return any(abs(timestamp - boundary) <= TIME_WINDOW for boundary in boundaries)


def _gap_intervals(samples: list[TrackSample], lookup: dict) -> list[tuple[float, float, str]]:
    grouped: dict[tuple[str, str], list[TrackSample]] = {}
    for sample in samples:
        grouped.setdefault((sample.session_id, sample.raw_track_id), []).append(sample)
    gaps = []
    for key, rows in grouped.items():
        association = lookup.get(key)
        if association is None or not association.object_local_id or association.status != "associated":
            continue
        ordered = sorted(rows, key=lambda item: item.frame_index)
        for left, right in zip(ordered, ordered[1:]):
            if right.frame_index - left.frame_index <= 1:
                continue
            if right.timestamp - left.timestamp < 0.2:
                continue
            gaps.append((left.timestamp, right.timestamp, association.object_local_id))
    return gaps


def _perception(document: AnalysisDocument, associations: list[TrackAssociation], summary: str) -> PerceptionRecord:
    current = document.perception
    hypotheses = list(current.hypotheses) if current else []
    for association in associations:
        if association.status not in {"ambiguous", "conflict"}:
            continue
        candidate = association.rivals[0] if association.rivals else (association.object_local_id or "")
        runner = association.rivals[1] if len(association.rivals) > 1 else None
        hypotheses.append(
            HypothesisRecord(
                hypothesis_id=f"sam-{association.scoped_id}",
                timestamp=0,
                subject_instance_id=association.scoped_id,
                candidate_instance_id=candidate or association.scoped_id,
                runner_up_instance_id=runner,
                association_score=association.score,
                margin=association.margin,
                decision="ambiguous",
                note=association.note,
            )
        )
    diagnostics = dict(current.diagnostics) if current else {}
    diagnostics["sam_tracks"] = len(associations)
    diagnostics["sam_associated"] = sum(item.status == "associated" for item in associations)
    diagnostics["sam_ambiguous"] = sum(item.status == "ambiguous" for item in associations)
    diagnostics["sam_unassigned"] = sum(item.status == "unassigned" for item in associations)
    note = summary if current is None or not current.notes else current.notes + "\n" + summary
    if current is None:
        return PerceptionRecord(
            strategy="sam_fusion",
            detector_id="sam-3.1",
            segmenter_id="sam-3.1",
            scores_are_probabilities=False,
            diagnostics=diagnostics,
            hypotheses=hypotheses,
            notes=note,
        )
    return current.model_copy(
        update={
            "segmenter_id": "sam-3.1",
            "diagnostics": diagnostics,
            "hypotheses": hypotheses,
            "notes": note,
            "scores_are_probabilities": False,
        }
    )


def _ambiguous_note(rivals: list[str], binding: str, score: float) -> str:
    if binding and score < ACCEPT_IOU:
        return (
            f"The prompted object {binding} did not overlap the returned track "
            f"(overlap {score:.2f}). It was not forced."
        )
    if rivals:
        return "Overlap was too weak or too close to assign this track. Labels were not compared."
    return "No spatial overlap linked this track to a known object."


def _tuple(box: BBox) -> tuple[float, float, float, float]:
    return (box.x, box.y, box.w, box.h)

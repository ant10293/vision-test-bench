"""Canonical, versioned analysis document.

Evidence classification and provenance are independent. A human can directly
observe something, and a model can infer something. Neither field implies the other.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app import SCHEMA_VERSION
from app.schemas.enums import (
    ActionOutcome,
    AmbiguityKind,
    Continuity,
    DependencyCategory,
    EvidenceClass,
    Provenance,
    RelationKind,
    Visibility,
)


class Evidence(BaseModel):
    classification: EvidenceClass
    provenance: Provenance
    note: str = ""
    frame_timestamps: list[float] = Field(default_factory=list)
    time_start: float | None = None
    time_end: float | None = None


class BBox(BaseModel):
    """Normalized image coordinates in displayed video space. Origin is top-left."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @field_validator("x", "y", "w", "h")
    @classmethod
    def finite(cls, value: float) -> float:
        if value != value:
            raise ValueError("bbox values must be finite")
        return value


class CameraAssessment(BaseModel):
    viewpoint: str
    task_constraint: str
    research_mode: str
    global_motion: float = 0
    scene_change_count: int = 0
    coordinate_frame: Literal["image", "scene_relative_image", "world"] = "image"
    world_coordinates_supported: bool = False
    camera_motion_compensated: bool = False
    compensation_model: str = "none"
    characterization_source: Literal["user", "auto", "override"] = "user"
    notes: str = ""


class Coverage(BaseModel):
    modality: Literal["native_video", "sampled_frames", "mixed"]
    sampling_preset: str
    nominal_fps: float | None = None
    frames_extracted: int = 0
    frames_submitted: int = 0
    frame_timestamps: list[float] = Field(default_factory=list)
    selection_reasons: dict[str, int] = Field(default_factory=dict)
    duration_sec: float = 0
    native_video_submitted: bool = False
    temporal_coverage_note: str = ""


class ObjectPrediction(BaseModel):
    local_id: str
    label: str
    description: str = ""
    visual_characteristics: str = ""
    initial_state: str = ""
    final_state: str = ""
    uncertain_identity: bool = False
    manipulated: bool = False
    confidence: float | None = None
    accepted: bool = True


class ObservationPrediction(BaseModel):
    object_local_id: str
    timestamp: float
    bbox: BBox | None = None
    visibility: Visibility = Visibility.VISIBLE
    description: str = ""
    evidence: Evidence
    sharpness: float | None = None
    occlusion: float | None = None
    continuity: Continuity = Continuity.CONTINUOUS
    coordinate_frame: Literal["image", "scene_relative_image", "world"] = "image"
    camera_compensated: bool = False
    track_ref: str | None = None
    accepted: bool = True


class StatePrediction(BaseModel):
    object_local_id: str
    time_start: float
    time_end: float
    state_label: str
    description: str = ""
    evidence: Evidence
    accepted: bool = True


class TransitionPrediction(BaseModel):
    object_local_id: str
    timestamp: float
    from_state: str
    to_state: str
    description: str = ""
    evidence: Evidence
    certainty: float | None = None
    accepted: bool = True


class RelationshipPrediction(BaseModel):
    subject_local_id: str
    other_local_id: str | None = None
    kind: RelationKind
    time_start: float
    time_end: float
    description: str = ""
    evidence: Evidence
    accepted: bool = True


class ActionPrediction(BaseModel):
    local_id: str
    action_type: str
    description: str = ""
    start: float
    end: float
    actor: str | None = None
    object_local_ids: list[str] = Field(default_factory=list)
    initial_state: str = ""
    final_state: str = ""
    outcome: ActionOutcome = ActionOutcome.UNKNOWN
    uncertainty: float = Field(default=0.5, ge=0, le=1)
    evidence: Evidence
    accepted: bool = True


class WorkflowStepPrediction(BaseModel):
    local_id: str
    name: str
    required_objects: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    expected_action: str = ""
    completion_condition: str = ""
    observed_result: str = ""
    action_local_ids: list[str] = Field(default_factory=list)
    time_start: float | None = None
    time_end: float | None = None
    uncertainty: float = Field(default=0.5, ge=0, le=1)
    evidence: Evidence
    parallel_group: str | None = None
    accepted: bool = True


class DependencyPrediction(BaseModel):
    from_step: str
    to_step: str
    category: DependencyCategory
    rationale: str = ""
    evidence: Evidence
    accepted: bool = True


class AmbiguityPrediction(BaseModel):
    local_id: str
    kind: AmbiguityKind
    description: str
    time_start: float | None = None
    time_end: float | None = None
    object_local_ids: list[str] = Field(default_factory=list)
    action_local_ids: list[str] = Field(default_factory=list)
    alternative: str = ""
    evidence: Evidence
    accepted: bool = True


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    entity_type: str = ""
    entity_id: str = ""


class AnalysisDocument(BaseModel):
    schema_version: str = SCHEMA_VERSION
    summary: str = ""
    fixture: bool = False
    fixture_label: str = ""
    camera: CameraAssessment | None = None
    coverage: Coverage | None = None
    objects: list[ObjectPrediction] = Field(default_factory=list)
    observations: list[ObservationPrediction] = Field(default_factory=list)
    states: list[StatePrediction] = Field(default_factory=list)
    transitions: list[TransitionPrediction] = Field(default_factory=list)
    relationships: list[RelationshipPrediction] = Field(default_factory=list)
    actions: list[ActionPrediction] = Field(default_factory=list)
    workflow_steps: list[WorkflowStepPrediction] = Field(default_factory=list)
    dependencies: list[DependencyPrediction] = Field(default_factory=list)
    ambiguities: list[AmbiguityPrediction] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    reinspection_notes: list[str] = Field(default_factory=list)

"""Versioned scene description. Units are meters. Coordinates are Z-up."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.simulation import SCENE_SCHEMA_VERSION

Vec3 = tuple[float, float, float]
RelationName = Literal[
    "on",
    "inside",
    "inserted_into",
    "attached_to",
    "aligned_with",
    "held_by",
    "supported_by",
    "in_contact_with",
]
CameraMode = Literal["fixed", "constrained_head", "unconstrained_head"]
PieceKind = Literal["box", "cylinder"]


def _finite(values: tuple[float, ...]) -> tuple[float, ...]:
    if any(not math.isfinite(item) for item in values):
        raise ValueError("values must be finite")
    return values


class Piece(BaseModel):
    model_config = {"extra": "forbid"}

    name: str
    kind: PieceKind
    size: tuple[float, ...]
    local_position: Vec3 = (0.0, 0.0, 0.0)
    local_rotation_deg: Vec3 = (0.0, 0.0, 0.0)

    @field_validator("local_position", "local_rotation_deg")
    @classmethod
    def finite_vec(cls, value: Vec3) -> Vec3:
        return _finite(value)  # type: ignore[return-value]

    @field_validator("size")
    @classmethod
    def positive_size(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        _finite(value)
        if not value or any(item <= 0 for item in value):
            raise ValueError("piece size must be positive")
        return value


class Material(BaseModel):
    model_config = {"extra": "forbid"}

    color: Vec3
    roughness: float = Field(default=0.5, ge=0, le=1)
    metallic: float = Field(default=0.0, ge=0, le=1)

    @field_validator("color")
    @classmethod
    def color_range(cls, value: Vec3) -> Vec3:
        _finite(value)
        if any(item < 0 or item > 1 for item in value):
            raise ValueError("color channels are 0..1")
        return value


class SceneObject(BaseModel):
    """One tracked instance. Pieces share that instance id in the mask."""

    model_config = {"extra": "forbid"}

    instance_id: str
    label: str
    description: str = ""
    pieces: list[Piece]
    material: Material
    position: Vec3
    rotation_deg: Vec3 = (0.0, 0.0, 0.0)
    omitted: bool = False

    @field_validator("position", "rotation_deg")
    @classmethod
    def finite_vec(cls, value: Vec3) -> Vec3:
        return _finite(value)  # type: ignore[return-value]


class Constraint(BaseModel):
    """A geometric assembly check. Overlapping boxes are not a relationship."""

    model_config = {"extra": "forbid"}

    id: str
    kind: Literal["axial_insertion", "coaxial_seat", "unresolved"]
    subject_id: str
    host_id: str
    relation: RelationName
    subject_axis: Vec3 = (0.0, 0.0, 1.0)
    host_axis: Vec3 = (0.0, 0.0, 1.0)
    subject_point: Vec3 = (0.0, 0.0, 0.0)
    host_point: Vec3 = (0.0, 0.0, 0.0)
    max_angle_deg: float = 8.0
    max_radial_m: float = 0.006
    min_penetration_m: float = 0.0
    max_penetration_m: float = 0.04
    min_axial_m: float = -1.0
    max_axial_m: float = 1.0


class Operation(BaseModel):
    """Scripted kinematic motion. It does not by itself complete an assembly."""

    model_config = {"extra": "forbid"}

    id: str
    object_id: str
    kind: Literal["translate", "rotate", "align", "insert", "place", "attach", "detach", "remove"]
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    position: Vec3
    rotation_deg: Vec3 = (0.0, 0.0, 0.0)
    checks: list[str] = Field(default_factory=list)

    @field_validator("position", "rotation_deg")
    @classmethod
    def finite_vec(cls, value: Vec3) -> Vec3:
        return _finite(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def ordered(self) -> Operation:
        if self.end <= self.start:
            raise ValueError(f"{self.id} ends before it starts")
        return self


class CameraKey(BaseModel):
    model_config = {"extra": "forbid"}

    t: float = Field(ge=0)
    eye: Vec3
    target: Vec3
    roll_deg: float = 0.0

    @field_validator("eye", "target")
    @classmethod
    def finite_vec(cls, value: Vec3) -> Vec3:
        return _finite(value)  # type: ignore[return-value]


class CameraRig(BaseModel):
    model_config = {"extra": "forbid"}

    mode: CameraMode
    fov_x_deg: float = Field(default=42.0, gt=1, lt=179)
    keys: list[CameraKey]


class Light(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    position: Vec3
    energy: float = Field(gt=0)
    color: Vec3 = (1.0, 1.0, 1.0)

    @field_validator("position", "color")
    @classmethod
    def finite_vec(cls, value: Vec3) -> Vec3:
        return _finite(value)  # type: ignore[return-value]


class ProcedureStep(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    name: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    required: bool = True
    operation_ids: list[str] = Field(default_factory=list)
    constraint_ids: list[str] = Field(default_factory=list)
    after: list[str] = Field(default_factory=list)
    order_category: Literal["necessary_physical", "independent"] = "independent"


class Expectation(BaseModel):
    """What this variant was built to do. The evaluator is still the authority."""

    model_config = {"extra": "forbid"}

    final_constraints: dict[str, bool]
    order_violated: bool = False
    summary: str


class Environment(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = "studio"
    background: Vec3 = (0.16, 0.17, 0.18)

    @field_validator("background")
    @classmethod
    def finite_vec(cls, value: Vec3) -> Vec3:
        return _finite(value)  # type: ignore[return-value]


class Scene(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: int = SCENE_SCHEMA_VERSION
    scene_id: str
    revision: int = 1
    name: str
    description: str = ""
    seed: int
    coordinate_system: Literal["blender_z_up_meters"] = "blender_z_up_meters"
    width: int = Field(ge=160, le=1280)
    height: int = Field(ge=120, le=720)
    fps: int = Field(ge=2, le=24)
    duration: float = Field(gt=0, le=12)
    kinematic: Literal[True] = True
    rigid_body: Literal[False] = False
    environment: Environment = Field(default_factory=Environment)
    objects: list[SceneObject]
    constraints: list[Constraint]
    operations: list[Operation]
    camera: CameraRig
    lights: list[Light]
    procedure: list[ProcedureStep]
    variant: str
    camera_mode: CameraMode
    pair_key: str = ""
    intervention: dict = Field(default_factory=dict)
    expectation: Expectation
    randomize: bool = False

    @model_validator(mode="after")
    def references(self) -> Scene:
        if self.schema_version != SCENE_SCHEMA_VERSION:
            raise ValueError(f"unsupported scene schema {self.schema_version}")
        ids = [item.instance_id for item in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("instance ids must be unique")
        known = set(ids)
        constraint_ids = [item.id for item in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("constraint ids must be unique")
        operation_ids = [item.id for item in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation ids must be unique")
        for item in self.constraints:
            if item.subject_id not in known or item.host_id not in known:
                raise ValueError(f"constraint {item.id} names an unknown object")
        for item in self.operations:
            if item.object_id not in known:
                raise ValueError(f"operation {item.id} names an unknown object")
            if item.end > self.duration + 1e-6:
                raise ValueError(f"operation {item.id} extends past the scene duration")
            unknown = [check for check in item.checks if check not in constraint_ids]
            if unknown:
                raise ValueError(f"operation {item.id} checks unknown constraints {unknown}")
        by_object: dict[str, list[Operation]] = {}
        for item in self.operations:
            by_object.setdefault(item.object_id, []).append(item)
        for object_id, motions in by_object.items():
            ordered = sorted(motions, key=lambda motion: motion.start)
            for earlier, later in zip(ordered, ordered[1:], strict=False):
                if later.start < earlier.end - 1e-8:
                    raise ValueError(f"{object_id} has overlapping operations")
        if len(self.camera.keys) < 1:
            raise ValueError("the camera needs a key")
        step_ids = {step.id for step in self.procedure}
        if len(step_ids) != len(self.procedure):
            raise ValueError("procedure step ids must be unique")
        for step in self.procedure:
            if step.end > self.duration + 1e-6:
                raise ValueError(f"step {step.id} extends past the scene duration")
            for prior in step.after:
                if prior not in step_ids:
                    raise ValueError(f"step {step.id} follows an unknown step")
        return self

    def frame_count(self) -> int:
        return max(2, int(round(self.duration * self.fps)))

    def sample_times(self) -> list[float]:
        count = self.frame_count()
        return [index / self.fps for index in range(count)]

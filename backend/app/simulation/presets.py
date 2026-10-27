"""Mechanical assembly scenes. Motion is kinematic. Constraints decide the outcome."""

from __future__ import annotations

import random

from app.simulation.schema import (
    CameraKey,
    CameraRig,
    Constraint,
    Environment,
    Expectation,
    Light,
    Material,
    Operation,
    Piece,
    ProcedureStep,
    Scene,
    SceneObject,
)

AUTHORED_DURATION = 6.0
VARIANTS = (
    "correct",
    "missing_washer",
    "occluded_washer",
    "incorrect_placement",
    "corrected_mistake",
    "identity_swap",
    "order_violation",
    "harmless_reorder",
)
CAMERA_MODES = ("fixed", "constrained_head", "unconstrained_head")
RESEARCH_MODES = {
    "fixed": "fixed_camera",
    "constrained_head": "constrained_head_mounted",
    "unconstrained_head": "unconstrained_head_mounted",
}

PLATE_COLOR = (0.42, 0.46, 0.52)
BOLT_COLOR = (0.74, 0.58, 0.22)
WASHER_COLOR = (0.78, 0.79, 0.82)
NUT_COLOR = (0.55, 0.36, 0.18)
COVER_COLOR = (0.22, 0.24, 0.28)


def _seconds(authored: float, last: float) -> float:
    return authored / AUTHORED_DURATION * last


def _box(name: str, size: tuple[float, float, float], position: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Piece:
    return Piece(name=name, kind="box", size=size, local_position=position)


def _cylinder(name: str, radius: float, depth: float, position: tuple[float, float, float]) -> Piece:
    return Piece(name=name, kind="cylinder", size=(radius, depth), local_position=position)


def _object(
    instance_id: str,
    label: str,
    pieces: list[Piece],
    color: tuple[float, float, float],
    position: tuple[float, float, float],
    description: str = "",
    omitted: bool = False,
    metallic: float = 0.0,
) -> SceneObject:
    return SceneObject(
        instance_id=instance_id,
        label=label,
        description=description,
        pieces=pieces,
        material=Material(color=color, roughness=0.45, metallic=metallic),
        position=position,
        omitted=omitted,
    )


def _camera(mode: str, last: float) -> CameraRig:
    def key(authored: float, eye: tuple[float, float, float], target: tuple[float, float, float], roll: float = 0.0) -> CameraKey:
        return CameraKey(t=_seconds(authored, last), eye=eye, target=target, roll_deg=roll)

    if mode == "fixed":
        keys = [key(0.0, (0.0, -0.62, 0.34), (0.0, 0.0, 0.04))]
    elif mode == "constrained_head":
        keys = [
            key(0.0, (0.05, -0.60, 0.33), (0.0, 0.0, 0.04), 3.0),
            key(3.0, (-0.09, -0.48, 0.24), (0.0, 0.0, 0.03), -5.0),
            key(6.0, (0.07, -0.56, 0.31), (0.01, 0.0, 0.04), 4.0),
        ]
    elif mode == "unconstrained_head":
        keys = [
            key(0.0, (0.0, -0.62, 0.34), (0.0, 0.0, 0.04)),
            key(2.4, (0.48, -0.28, 0.40), (0.55, 0.16, 0.04), 10.0),
            key(6.0, (0.0, -0.62, 0.34), (0.0, 0.0, 0.04)),
        ]
    else:
        raise ValueError(f"unknown camera mode {mode}")
    return CameraRig(mode=mode, fov_x_deg=42.0, keys=keys)  # type: ignore[arg-type]


def build_assembly(
    variant: str = "correct",
    camera_mode: str = "fixed",
    seed: int = 1,
    width: int = 320,
    height: int = 240,
    fps: int = 8,
    duration: float = 6.0,
    randomize: bool = False,
    scene_id: str = "assembly",
    pair_key: str = "",
) -> Scene:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant}")
    if camera_mode not in CAMERA_MODES:
        raise ValueError(f"unknown camera mode {camera_mode}")
    last = (max(2, int(round(duration * fps))) - 1) / fps
    scale = 1.0
    if randomize:
        scale = 0.96 + random.Random(seed).random() * 0.08

    def at(authored: float) -> float:
        return _seconds(authored, last)

    def pos(value: tuple[float, float, float]) -> tuple[float, float, float]:
        return tuple(item * scale for item in value)  # type: ignore[return-value]

    bolt_start = pos((-0.09, 0.045, 0.07))
    washer_start = pos((0.09, 0.05, 0.035))
    washer_wrong = pos((0.075, 0.045, 0.03))
    nut_start = pos((0.09, -0.05, 0.04))
    bolt_in = pos((0.0, 0.0, 0.008))
    washer_on = pos((0.0, 0.0, 0.022))
    nut_on = pos((0.0, 0.0, 0.031))
    cover_park = pos((0.34, 0.22, 0.04))
    cover_block = pos((0.0, -0.18, 0.06))

    washer_omitted = variant == "missing_washer"
    include_twin = variant == "identity_swap"
    include_cover = variant == "occluded_washer"

    objects = [
        _object("table", "table", [_box("top", pos((0.9, 0.7, 0.04)))], (0.34, 0.29, 0.24), pos((0.0, 0.0, -0.02))),
        _object(
            "plate",
            "plate",
            [_box("body", pos((0.16, 0.12, 0.02)))],
            PLATE_COLOR,
            pos((0.0, 0.0, 0.01)),
            "Flat plate with a hole along its local vertical axis.",
        ),
        _object(
            "bolt",
            "bolt",
            [
                _cylinder("shaft", 0.004 * scale, 0.046 * scale, pos((0.0, 0.0, 0.023))),
                _cylinder("head", 0.009 * scale, 0.007 * scale, pos((0.0, 0.0, 0.0495))),
            ],
            BOLT_COLOR,
            bolt_start,
            "Fastener. The instance origin is the tip of the shaft.",
            metallic=0.6,
        ),
        _object(
            "washer",
            "washer",
            [_cylinder("ring", 0.012 * scale, 0.002 * scale, (0.0, 0.0, 0.0))],
            WASHER_COLOR,
            washer_start,
            "Flat ring. Omitted variants are physically absent, not hidden.",
            omitted=washer_omitted,
            metallic=0.35,
        ),
        _object(
            "nut",
            "nut",
            [_cylinder("body", 0.008 * scale, 0.006 * scale, (0.0, 0.0, 0.0))],
            NUT_COLOR,
            nut_start,
            "Second fastener on the bolt.",
            metallic=0.45,
        ),
        _object(
            "side_block",
            "block",
            [_box("body", pos((0.045, 0.045, 0.04)))],
            (0.30, 0.48, 0.38),
            pos((0.55, 0.16, 0.02)),
            "Object in a second work area.",
        ),
    ]
    if include_twin:
        objects.append(
            _object(
                "washer_b",
                "washer",
                [_cylinder("ring", 0.012 * scale, 0.002 * scale, (0.0, 0.0, 0.0))],
                WASHER_COLOR,
                pos((0.0, 0.02, 0.04)),
                "Second washer with the same appearance and a different instance id.",
                metallic=0.35,
            )
        )
    if include_cover:
        objects.append(
            _object(
                "cover",
                "cover",
                [_box("board", pos((0.20, 0.016, 0.16)))],
                COVER_COLOR,
                cover_park,
                "Board that can hide the assembly from the camera.",
            )
        )

    limits = {
        "max_radial_m": 0.005 * scale,
        "min_penetration_m": 0.006 * scale,
        "max_penetration_m": 0.03 * scale,
    }
    seat = {"max_radial_m": 0.006 * scale, "min_axial_m": 0.008 * scale, "max_axial_m": 0.04 * scale}
    constraints = [
        Constraint(
            id="bolt_in_plate",
            kind="axial_insertion",
            subject_id="bolt",
            host_id="plate",
            relation="inserted_into",
            host_point=(0.0, 0.0, 0.01 * scale),
            **limits,
        ),
        Constraint(
            id="washer_on_bolt",
            kind="coaxial_seat",
            subject_id="washer",
            host_id="bolt",
            relation="on",
            **seat,
        ),
        Constraint(
            id="nut_on_bolt",
            kind="coaxial_seat",
            subject_id="nut",
            host_id="bolt",
            relation="attached_to",
            max_radial_m=0.006 * scale,
            min_axial_m=0.014 * scale,
            max_axial_m=0.05 * scale,
        ),
    ]

    bolt_align = Operation(
        id="bolt_align",
        object_id="bolt",
        kind="align",
        start=at(0.4),
        end=at(1.5),
        position=pos((0.0, 0.0, 0.08)),
    )
    bolt_insert = Operation(
        id="bolt_insert",
        object_id="bolt",
        kind="insert",
        start=at(1.5),
        end=at(2.5),
        position=bolt_in,
        checks=["bolt_in_plate"],
    )
    washer_place = Operation(
        id="washer_place",
        object_id="washer",
        kind="place",
        start=at(2.6),
        end=at(3.7),
        position=washer_on,
        checks=["washer_on_bolt"],
    )
    nut_place = Operation(
        id="nut_place",
        object_id="nut",
        kind="attach",
        start=at(3.8),
        end=at(5.0),
        position=nut_on,
        checks=["nut_on_bolt"],
    )
    operations = [bolt_align, bolt_insert, washer_place, nut_place]
    changed: list[str] = []
    order_violated = False
    summary = "The plate, bolt, washer, and nut reach the seated configuration."

    if variant == "missing_washer":
        operations = [bolt_align, bolt_insert, nut_place]
        changed = ["washer.omitted"]
        summary = "The washer is absent from the scene. The bolt and nut still seat."
    elif variant == "incorrect_placement":
        operations = [
            bolt_align,
            bolt_insert,
            washer_place.model_copy(update={"position": washer_wrong}),
            nut_place,
        ]
        changed = ["washer.final_position"]
        summary = "The washer stays beside the bolt. The seat constraint fails."
    elif variant == "corrected_mistake":
        operations = [
            bolt_align,
            bolt_insert,
            Operation(
                id="washer_misplace",
                object_id="washer",
                kind="place",
                start=at(2.5),
                end=at(3.3),
                position=washer_wrong,
                checks=["washer_on_bolt"],
            ),
            washer_place.model_copy(update={"start": at(3.5), "end": at(4.4)}),
            nut_place.model_copy(update={"start": at(4.5), "end": at(5.4)}),
        ]
        changed = ["washer.temporary_error"]
        summary = "The washer is placed aside, then moved onto the bolt before the end."
    elif variant == "order_violation":
        operations = [
            bolt_align,
            bolt_insert,
            nut_place.model_copy(update={"start": at(2.5), "end": at(3.4)}),
            washer_place.model_copy(update={"start": at(3.6), "end": at(4.8)}),
        ]
        order_violated = True
        changed = ["nut.before_washer"]
        summary = "The nut seats before the washer. The final seats can still hold."
    elif variant == "identity_swap":
        operations = operations + [
            Operation(
                id="washer_b_aside",
                object_id="washer_b",
                kind="translate",
                start=at(2.6),
                end=at(3.7),
                position=pos((0.08, -0.04, 0.03)),
            )
        ]
        changed = ["second_washer"]
        summary = "Two washers of the same appearance exchange roles. Instance ids stay distinct."
    elif variant == "occluded_washer":
        operations = operations + [
            Operation(
                id="cover_close",
                object_id="cover",
                kind="translate",
                start=at(4.6),
                end=at(5.4),
                position=cover_block,
            )
        ]
        changed = ["cover.blocks_view"]
        summary = "The washer is seated, then a board hides the assembly from the camera."
    elif variant == "harmless_reorder":
        operations = operations + [
            Operation(
                id="block_slide",
                object_id="side_block",
                kind="translate",
                start=at(0.3),
                end=at(1.1),
                position=pos((0.48, 0.02, 0.02)),
            )
        ]
        changed = ["side_block.independent_motion"]
        summary = "An unrelated block moves. The assembly order is unchanged."

    if randomize:
        objects = [_jitter_start(item, seed) for item in objects]
        _jitter_color(objects, seed)

    steps = _steps(variant, at)
    final_constraints = {
        "bolt_in_plate": True,
        "washer_on_bolt": variant != "missing_washer" and variant != "incorrect_placement",
        "nut_on_bolt": True,
    }
    key = pair_key or f"assembly:{seed}:{camera_mode}:{width}x{height}:{fps}:{duration}:{int(randomize)}"
    light_energy = 180.0
    if randomize:
        light_energy = 140.0 + random.Random(f"{seed}:light").random() * 80.0
    scene = Scene(
        scene_id=scene_id,
        name=f"Assembly {variant}",
        description=summary,
        seed=seed,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        environment=Environment(),
        objects=objects,
        constraints=constraints,
        operations=operations,
        camera=_camera(camera_mode, last),
        lights=[
            Light(id="key", position=pos((0.35, -0.4, 0.7)), energy=light_energy),
            Light(id="fill", position=pos((-0.4, -0.2, 0.5)), energy=light_energy * 0.35, color=(0.85, 0.9, 1.0)),
        ],
        procedure=steps,
        variant=variant,
        camera_mode=camera_mode,  # type: ignore[arg-type]
        pair_key=key,
        intervention={"pair_key": key, "variant": variant, "changed_from_correct": changed, "scale": scale},
        expectation=Expectation(
            final_constraints=final_constraints,
            order_violated=order_violated,
            summary=summary,
        ),
        randomize=randomize,
    )
    return scene


def _steps(variant: str, at) -> list[ProcedureStep]:
    washer_after = ["insert_bolt"]
    nut_after = ["seat_washer"]
    nut_category = "necessary_physical"
    if variant == "order_violation":
        nut_after = ["seat_washer"]
    washer_step_start, washer_step_end = 2.6, 3.7
    nut_step_start, nut_step_end = 3.8, 5.0
    if variant == "order_violation":
        nut_step_start, nut_step_end = 2.5, 3.4
        washer_step_start, washer_step_end = 3.6, 4.8
    if variant == "corrected_mistake":
        washer_step_start, washer_step_end = 3.5, 4.4
        nut_step_start, nut_step_end = 4.5, 5.4
    steps = [
        ProcedureStep(id="align_bolt", name="Align the bolt", start=at(0.4), end=at(1.5), required=True, operation_ids=["bolt_align"]),
        ProcedureStep(
            id="insert_bolt",
            name="Insert the bolt",
            start=at(1.5),
            end=at(2.5),
            required=True,
            operation_ids=["bolt_insert"],
            constraint_ids=["bolt_in_plate"],
            after=["align_bolt"],
            order_category="necessary_physical",
        ),
        ProcedureStep(
            id="seat_washer",
            name="Seat the washer",
            start=at(washer_step_start),
            end=at(washer_step_end),
            required=True,
            operation_ids=[] if variant == "missing_washer" else ["washer_place"],
            constraint_ids=["washer_on_bolt"],
            after=washer_after,
            order_category="necessary_physical",
        ),
        ProcedureStep(
            id="seat_nut",
            name="Seat the nut",
            start=at(nut_step_start),
            end=at(nut_step_end),
            required=True,
            operation_ids=["nut_place"],
            constraint_ids=["nut_on_bolt"],
            after=nut_after,
            order_category=nut_category,  # type: ignore[arg-type]
        ),
    ]
    if variant == "harmless_reorder":
        steps.append(
            ProcedureStep(
                id="slide_block",
                name="Slide the other block",
                start=at(0.3),
                end=at(1.1),
                required=False,
                operation_ids=["block_slide"],
                order_category="independent",
            )
        )
    return steps


def _jitter_start(obj: SceneObject, seed: int) -> SceneObject:
    if obj.instance_id in {"plate", "table"}:
        return obj
    rng = random.Random(f"{seed}:{obj.instance_id}:start")
    nudged = (
        obj.position[0] + (rng.random() - 0.5) * 0.008,
        obj.position[1] + (rng.random() - 0.5) * 0.008,
        obj.position[2],
    )
    return obj.model_copy(update={"position": nudged})


def _jitter_color(objects: list[SceneObject], seed: int) -> None:
    for obj in objects:
        rng = random.Random(f"{seed}:{obj.instance_id}:color")
        # Washer twins share a color so the identity case stays hard.
        if obj.instance_id == "washer_b":
            source = next(item for item in objects if item.instance_id == "washer")
            obj.material = source.material
            continue
        color = tuple(min(1.0, max(0.0, channel + (rng.random() - 0.5) * 0.06)) for channel in obj.material.color)
        obj.material = obj.material.model_copy(update={"color": color})

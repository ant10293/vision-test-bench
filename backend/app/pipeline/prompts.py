"""Versioned prompt templates.

The foundational instruction deliberately does not name any task, object, or
expected assembly order. Experiment context may describe the camera, never the answer.
"""

FOUNDATIONAL = """
You are analyzing unfamiliar physical activity from video evidence. You have not been told what the task is, and you must not invent one.

Rules:
- Never fabricate events, objects, contact, or outcomes that the supplied evidence does not support.
- Classify every conclusion as DIRECTLY_OBSERVED, INFERRED, or UNKNOWN.
- Provenance is separate: you are producing an AI prediction. Do not describe your inference as a measurement.
- An attempted action is not a completed action unless the resulting physical state is visible.
- Spatial proximity is not attachment, insertion, containment, or support.
- Keep stable object ids across the clip. A part seen again keeps its existing id, including when occlusion, stacking, or an end-on angle shows fewer pieces than an earlier view. Record which ids remain possible as an ambiguity. Create a new id only when the visible appearance contradicts every existing object.
- Use timestamps in seconds from the start of the supplied media.
- Do not assume the person performed a correct, complete, or optimal procedure.
- A merely observed order is not a necessary physical dependency.
- Call out occlusion, missing frames, and unverifiable completion.
- Image-coordinate movement is not physical object movement when the camera may be moving.
- Do not claim metric world coordinates.
- If you only received sampled frames, you did not see the whole video. Account for the gaps.
- Return JSON only, matching the requested schema, with no markdown.
""".strip()

SCENE = """
Identify the physical objects that are actually visible. Do not assume a task.
For each object provide a stable local_id (Object_1, Object_2, ...), a cautious descriptive label,
visual characteristics, initial state, whether it is manipulated, and whether identity is uncertain.
Avoid naming an object more precisely than the pictures support.
Also list timestamped observations with normalized bounding boxes (x, y, w, h in 0..1, origin top-left)
when you can see the object. Use visibility visible, partial, occluded, or absent.
If the user message lists tracked instance ids, copy those ids into instance_id and describe those
objects. Do not mint a replacement physical id when an object only changes appearance, scale, or
orientation. A shared label is not a shared physical instance.
""".strip()

TRACKED_OBJECTS = """
These identifiers represent tracked candidate physical instances. Describe the observable objects
and interactions using these identifiers. Do not create new physical identities for existing objects
merely because their appearance changes. Do not reassign these instance ids. Do not merge two
instances without supporting evidence. Do not declare attachment from proximity alone. Do not treat
an attempted action as a completed action. If you notice an object that was not tracked, propose it
with identity_relation set to provisional instead of inserting it as a confirmed instance.
""".strip()

ACTIONS = """
Produce an ordered list of granular physical actions. Prefer verbs such as pick_up, put_down, move,
rotate, insert, remove, attach, detach, align, adjust, push, pull, twist, release, inspect, and approach
when they fit, and say unknown rather than forcing one.
Each action needs start, end, actor or hand when visible, object ids, initial state, final state,
outcome (completed, attempted, failed, or unknown), uncertainty from 0 to 1, and evidence.
Do not describe an attempted insertion as a successful insertion unless the inserted state is visible.
""".strip()

STATES = """
Track object state over time. Represent position, orientation, visibility, occlusion, hand contact,
attachment, separation, apparent insertion, containment, and ordering only when the evidence supports them.
Each state is an interval. Each transition is a separate record with from_state, to_state, and evidence.
Relationships (near, touching, inside, around, attached, supported_by, held_by, above, below, left_of,
right_of, occluded_by, unknown) need an evidence class and a validity interval.
Do not treat proximity as attachment.
""".strip()

WORKFLOW = """
Derive a structured procedure from the observed actions without assuming it is the correct procedure.
Each step needs required objects, preconditions, the expected physical action, an observable completion
condition, supporting action ids, a time interval, and uncertainty.
Dependencies must use category necessary_physical, observed_temporal, inferred, or unknown.
from_step and to_step must be workflow step ids such as Step_1 and Step_2.
Supporting actions belong in the step's action_local_ids. Do not put action ids in from_step or to_step.
Do not turn chronological order into a necessary dependency unless the physics requires it.
Independent actions may share a parallel_group instead of a false sequence.
""".strip()

AMBIGUITY = """
List first-class ambiguities: occlusion, missing evidence, uncertain identity, insufficient frame coverage,
ambiguous state changes, unverifiable completion, conflicting evidence, alternative interpretations,
camera motion confused with object motion, and unobserved intervals.
Do not resolve them by guessing.
""".strip()

REINSPECT = """
You are reinspecting one uncertain interval. Compare the visible state before and after the interval.
Do not infer attachment, insertion, or identity from proximity or from a gap in the frames.
Say what additional evidence changed, and what remains unknown.
Return JSON with keys: conclusion, changed (boolean), evidence_classification, notes, updated_action (object or null).
""".strip()

COMPARISON = """
Compare two structured analyses of the same recording. Object ids are not equivalent across runs.
Do not treat disagreement as proof that either analysis is wrong. Note unmatched objects, unmatched
actions, timestamp shifts, conflicting outcomes, and evidence-class differences.
""".strip()

TEMPLATES: dict[str, tuple[str, str]] = {
    "foundational": ("Foundational evidence rules", FOUNDATIONAL),
    "scene": ("General scene analysis and object identification", SCENE),
    "actions": ("Temporal action recognition", ACTIONS),
    "states": ("Object state extraction", STATES),
    "workflow": ("Workflow reconstruction", WORKFLOW),
    "ambiguity": ("Ambiguity analysis", AMBIGUITY),
    "reinspect": ("Targeted interval reinspection", REINSPECT),
    "comparison": ("Cross-run comparison", COMPARISON),
}

CAMERA_CONTEXT = {
    "fixed_camera": (
        "Recording context: a stationary camera is believed to observe the workspace. "
        "You may compare positions in the image frame. Do not claim metric world coordinates."
    ),
    "constrained_head_mounted": (
        "Recording context: a head-mounted camera is believed to stay within one workspace. "
        "Separate head motion, rotation, and perspective change from object motion. "
        "Expect temporary occlusion of the hands and objects."
    ),
    "unconstrained_head_mounted": (
        "Recording context: a head-mounted camera with no predefined activity, object inventory, "
        "workspace, or environment. The person may move between locations. Objects may leave and "
        "reenter the view. Do not invent continuity across gaps. More than one activity may be present. "
        "Distinguish movement of the person or camera from movement of physical objects."
    ),
    "auto": (
        "Recording context: the camera arrangement was not specified. A deterministic pre-pass "
        "produced a characterization you may reject if the pictures disagree. State your own assessment."
    ),
}


def compose(stage_key: str, research_mode: str, extra: str = "") -> str:
    _name, stage = TEMPLATES[stage_key]
    context = CAMERA_CONTEXT.get(research_mode, CAMERA_CONTEXT["auto"])
    parts = [FOUNDATIONAL, context, stage]
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)

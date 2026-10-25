# Research methodology

The question is whether a model that was not trained on the task can recover the procedure from the recording, and whether a later check can tell a correct performance from an attempt.

## What is not given to the model

The default prompts forbid inventing events, equating an attempt with success, treating proximity as attachment, assuming the demonstrated order is necessary, and claiming world coordinates. They do not name a hardware task. A test locks that: the template text does not contain bolt, washer, nut, or hexagonal. The fixture analyses do use those names, because they are the authored answer for the synthetic clip, and the interface badges them as fixtures.

## What a run must record

Checksum, model id, provider, modality, sampling preset, the timestamps that were extracted and the ones that were submitted, prompt version and the snapshot text, parameters, provider request ids when the API returns them, start and finish times, SDK version, application version, git revision, raw response, validation issues, token counts, and an estimated cost. Sparse frames are never described as a full viewing. The coverage note says how many frames were used and that the rest of the video was not sent.

## Three recordings

The same document schema is used for a fixed camera, a head-mounted camera in one workspace, and a head-mounted camera that changes places. Reliability is compared per mode. Auto mode may suggest a mode from global motion and scene changes; the researcher can override it.

## Comparison and scoring

Two models disagreeing is not accuracy. Self-reported confidence is not accuracy. Object ids are local to a run. Assisted matching by label is labeled as assisted. Ground-truth scores use interval overlap with a configurable IoU, one prediction to one reference. Boundary error is reported beside precision and recall. Each miss links to a time range.

## What the deterministic baseline is for

It shows the ceiling of "something moved" without a semantic claim. Use it to see which parts of a VLM story are not forced by the motion itself.

## Limits of the current geometry

Camera compensation is a partial affine estimated between samples. It is not visual SLAM and it does not produce a stable world frame. A scene change keeps archived tracks and starts new ids. Appearance can propose that an archived id returned. That proposal is not a confirmation, and the unobserved interval stays explicit.

## Identity experiments

The same recording can be run under five stored presets: `A_legacy_motion`, `B_temporal`, `C_appearance`, `D_appearance_segmented`, and `vlm_only`. A is the motion baseline. B adds detection proposals and temporal association without an encoder. C adds DINOv2 ViT-S/14 and a multi-view gallery. D also runs GrabCut during tracking. `vlm_only` leaves identity to the model and does not inject tracked ids. Useful comparisons are rotation, scale, lighting, a hand in front of the object, two similar objects exchanging places, and an object that leaves and comes back. This document does not claim that one preset is more accurate. Measure that against instance-level ground truth. Label overlap is not that measurement.

A language model that receives tracked ids is told to describe those ids, to leave a new physical id as a provisional proposal, and not to treat proximity as attachment or an attempt as completion. A validator warning is recorded when a high-certainty transition has unknown or inferred evidence, and when an attached, inside, or held relationship is marked directly observed from proximity. Warnings do not raise certainty and do not rewrite the original conclusion. Reinspection, when the experiment asked for it, appends a proposal with `applied: false`.

## Several recordings of one task

A task may span days and may mix camera modes. Ordering from container timestamps is partial when a clip has no reliable time, a tie, or conflicting tags. Visual context can suggest a relative order and must be labeled as a hypothesis. Similar appearance is not instance identity, including after a large change in surface or form. A process that happens between recordings (drying, curing, cooling, fermentation, or anything else the endpoint text mentions) is a hypothesis beside the observed change, not an event the footage showed.

Project scores against a human ground truth cover pairwise clip order, session overlap, identity links, reidentification where the endpoint states differ, transition evidence class, unresolved-interval count, and procedural-dependency precision and recall. Matcher scores are returned in a block marked `is_accuracy: false`. Adding a clip creates a new project revision and reports what changed. The previous revision and the cached clip runs stay.

## Speech beside the visual record

A transcript is optional and is not a visual observation. A spoken claim does not complete a step. Comparison of a visual-only run with a transcript-assisted run lists differences in the analysis document, latency, and cost. It reports ground-truth metrics only when a human dataset exists for the visual run. Otherwise it does not report an accuracy score.

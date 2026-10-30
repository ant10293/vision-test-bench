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

Three image measurements sit on top of the tracks. None of them is a distance or a pose.

- Apparent size is box area over the median of the first three full views of the same id. A box that is partial, occluded, or within 2% of the frame edge stores nothing. It grows when a part comes toward the lens and also when a head-mounted camera moves closer, so it is displayed and never judged.
- Image-plane turn is the rotation of the features inside the box since its first full view, positive clockwise. Each sample is chained from the last one and then corrected against the first view when the two agree within 10 degrees. On a head-mounted camera the camera's own roll between the same frames is removed. The roll is measured only when at least 15 features, and at least a quarter of those found, agree on it. A frame that fails that check gets no angle, and more than four such frames in a row end the trace for every id seen before them. A turn about the part's own axis, as when a fastener is tightened, or a flip onto another face, is not measured. A turn of at least 20 degrees that stays within 8 degrees for three samples is written as an `image_plane_initial` to `image_plane_turned` transition, and a return under 20 degrees that holds is written back. On a fixed camera the transition is directly observed. On a head-mounted camera it is inferred, because a textured hand that fills most of the frame can be read as camera roll and make a still part look turned. An inferred turn is shown on the object panel but cannot pass or fail a check.
- Depth order runs only on a fixed camera, only where two visible boxes overlap, and only when MiDaS small loads (it needs PyTorch and timm). Each box's median relative depth, leaving out the shared area, is compared after scaling the frame's depth to its 2nd-98th percentile range. A lead of at least 0.15 of that range becomes an `in_front_of` interval. It is not contact, support, or attachment, and it does not identify a role. A depth order inside a demonstration step becomes a check on that step; an execution with no matching order cannot verify it rather than failing it. When a part has turned 20 degrees or more, its track note says its appearance can differ from its first view.
- The object boxes in an analysis come from a model or a person and often do not line up with any track, so the same scale, turn, and depth-order measures are also run on each object's own boxes over the sampled frames. The results are stored beside the run and shown as a label under each box, with a "front" badge for a clear depth order. Because overlapping object boxes are usually marked partial, partial boxes enter depth order there; a hand over the uncovered part can still make a box read nearer. Sampled frames are often a second apart, so a part that moves a lot between them gets no turn value. These labels are display only and never become verification records. When the model is unavailable the stage records that and no other depth source is used.

A learned turn is a required completion check only on a rotate, align, twist, or turn step, and the check reads turn records alone. A step with no measured turn stays CANNOT_VERIFY. Depth order is never required, is not a role signature, and does not count as a learned view for a missing-component failure, because a head-mounted execution can never produce it.

## Identity experiments

The same recording can be run under five stored presets: `A_legacy_motion`, `B_temporal`, `C_appearance`, `D_appearance_segmented`, and `vlm_only`. A is the motion baseline. B adds detection proposals and temporal association without an encoder. C adds DINOv2 ViT-S/14 and a multi-view gallery. D also runs GrabCut during tracking. `vlm_only` leaves identity to the model and does not inject tracked ids. Useful comparisons are rotation, scale, lighting, a hand in front of the object, two similar objects exchanging places, and an object that leaves and comes back. This document does not claim that one preset is more accurate. Measure that against instance-level ground truth. Label overlap is not that measurement.

A language model that receives tracked ids is told to describe those ids, to leave a new physical id as a provisional proposal, and not to treat proximity as attachment or an attempt as completion. A validator warning is recorded when a high-certainty transition has unknown or inferred evidence, and when an attached, inside, or held relationship is marked directly observed from proximity. Warnings do not raise certainty and do not rewrite the original conclusion. Reinspection, when the experiment asked for it, appends a proposal with `applied: false`.

## Several recordings of one task

A task may span days and may mix camera modes. Ordering from container timestamps is partial when a clip has no reliable time, a tie, or conflicting tags. Visual context can suggest a relative order and must be labeled as a hypothesis. Similar appearance is not instance identity, including after a large change in surface or form. A process that happens between recordings (drying, curing, cooling, fermentation, or anything else the endpoint text mentions) is a hypothesis beside the observed change, not an event the footage showed.

Project scores against a human ground truth cover pairwise clip order, session overlap, identity links, reidentification where the endpoint states differ, transition evidence class, unresolved-interval count, and procedural-dependency precision and recall. Matcher scores are returned in a block marked `is_accuracy: false`. Adding a clip creates a new project revision and reports what changed. The previous revision and the cached clip runs stay.

## Speech beside the visual record

A transcript is optional and is not a visual observation. A spoken claim does not complete a step. Comparison of a visual-only run with a transcript-assisted run lists differences in the analysis document, latency, and cost. It reports ground-truth metrics only when a human dataset exists for the visual run. Otherwise it does not report an accuracy score.

## Learned procedure versus a new execution

The demonstration analysis is turned into completion conditions that can be checked on a different recording. Instance ids are not required to match. A role is a functional slot. Two objects with the same label and similar appearance stay as competing hypotheses when their relations do not separate them.

Verdicts are PASS, FAIL, CANNOT_VERIFY, and NOT_APPLICABLE. Execution state is separate: not attempted, attempted, completed, corrected, or unknown. An attempted action does not pass a step. A required predicate fails only when a visible state contradicts it. Absence of a detection is not absence of a part. A missing-component failure needs the expected relation to be absent, the subject unresolved or absent in the final window, the anchor visible and directly observed, and a learned co-occurring relation directly observed in that window. An occluded or inferred view stays CANNOT_VERIFY.

Order is an error only when a `necessary_physical` dependency is violated and not later recovered. A different pace, a pause, or a reorder of independent steps is not an error. An action outside the procedure is recorded as unexpected and is not a failure by itself. A contradiction that is later replaced by the expected relation remains in the history as a temporary error, and the final verdict can be PASS.

Overall PASS requires every applicable required step to pass. One uncorrected required failure is FAIL. A required step that cannot be verified, with no failure, is INCONCLUSIVE. A run that never finished is INCOMPLETE. A verification that has not been judged is NOT_EVALUATED. The displayed score is verified required steps divided by verified, failed, and unresolved required steps. Unresolved steps are not successes.

Strategies that can be compared against the same human labels are model proposal only, model proposal plus alignment, tracked evidence, and the hybrid reading with optional reinspection. Reinspection plans a bounded question and frame list. It does not call a provider. A later finding changes the verdict only when its evidence class is `DIRECTLY_OBSERVED` and the view is visible. Accuracy is computed only against human ground truth.

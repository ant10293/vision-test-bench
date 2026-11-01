# Research methodology

The question is whether a model that was not trained on the task can recover the procedure from the recording, and whether a later check can tell a correct performance from an attempt.

## What is not given to the model

The default prompts forbid inventing events, equating an attempt with success, treating proximity as attachment, assuming the demonstrated order is necessary, and claiming world coordinates. They do not name a hardware task. A test locks that: the template text does not contain bolt, washer, nut, or hexagonal. The fixture analyses do use those names, because they are the authored answer for the synthetic clip, and the interface badges them as fixtures.

## What a run must record

Checksum, model id, provider, modality, sampling preset, the timestamps that were extracted and the ones that were submitted, prompt version and the snapshot text, parameters, provider request ids when the API returns them, start and finish times, SDK version, application version, git revision, raw response, validation issues, token counts, and an estimated cost. Sparse frames are never described as a full viewing. The coverage note says how many frames were used and that the rest of the video was not sent.

Coverage also records the requested interval, the time basis (`presentation` when frame times came from the file, `container` when estimated), the largest unexamined gap, every gap longer than the occlusion window, the change peaks that were and were not selected, hard and soft scene boundaries, the tracking floor and dense spans, tracking continuity, segmentation chunks and the chunks skipped with their reason, media transforms such as a subclip offset or a rotation, the semantic mode, and the number of semantic calls. Temporal coverage (how much of the interval was examined) and temporal resolution (how densely) are reported separately. A gap is an interval that was not examined. It is not evidence that nothing happened.

## Combined and grounded runs

The same recording can be run in both semantic orders. Combined asks one call for objects, boxes, and the procedure, then resolves geometry. Grounded asks for objects and boxes first, resolves geometry from the tracker and segmentation, then asks one interpretation call that reads the resolved instances. Grounded costs one more semantic call and records it. Compare the two with the same frames, provider, and model; the difference is the effect of conditioning interpretation on measured geometry. `semantic_raw.json` keeps the scene call's parsed output before any repair, so the effect of repairs can be measured separately.

## Three recordings

The same document schema is used for a fixed camera, a head-mounted camera in one workspace, and a head-mounted camera that changes places. Reliability is compared per mode. Auto mode may suggest a mode from global motion and scene changes; the researcher can override it.

## Comparison and scoring

Two models disagreeing is not accuracy. Self-reported confidence is not accuracy. Object ids are local to a run. Assisted matching by label is labeled as assisted. Ground-truth scores use interval overlap with a configurable IoU, one prediction to one reference. Boundary error is reported beside precision and recall. Each miss links to a time range.

## What the deterministic baseline is for

It shows the ceiling of "something moved" without a semantic claim. Use it to see which parts of a VLM story are not forced by the motion itself. Its motion and region proposals stay provisional rows. They are never promoted to canonical geometry, so the baseline does not get more accurate boxes from this design; it stops presenting blobs as detections.

## Benchmark

`backend/tests/benchmark_harness.py` scores a run's document against a ground-truth document in the existing shape, with instance ids on observations. It reports box recall and mean IoU, IDF1, identity switches, false merges, occlusion recovery, tracking coverage, short-event recall, grounding correctness, workflow precision and recall, and the recorded latency and call counts. It reuses `identity_eval.py` and `metrics.py` and scores the display set only.

```bash
cd backend && .venv/bin/python -m tests.benchmark_harness ../data/benchmark
```

Each case is a folder with `truth.json`, `predicted.json`, and `meta.json`. Only a case whose meta says `"source": "human"` is real-world evidence. The checked-in cases are synthetic and generated media and are labeled as such. Their scores check that the harness and the pipeline agree with themselves. They are not accuracy. No human annotation is in the tree today, so this refactor reports no measured accuracy gain. The ten conditions worth annotating are rotation, scale, lighting, occlusion by a hand, two similar objects exchanging places, an object that leaves and returns, a hard cut, a long static stretch before activity, a short event, and a rotated phone recording.

## Limits of the current geometry

Camera compensation is a partial affine estimated between samples. It is not visual SLAM and it does not produce a stable world frame. A hard cut keeps archived tracks and starts new ids. Appearance or a strong temporal match can propose that an archived id returned. That proposal is not a confirmation, and the unobserved interval stays explicit.

Provider video tokenizers can resample a subclip internally. The bench controls the file it sends, not the provider's internal sampling. A stationary object stays invisible to a motion-only detector until another backend, such as segmentation or a manual seed, sees it. Adaptive tracking density can miss a short event in a stretch that looked static; the floor and the gaps are reported so that miss is visible.

These hold in tests: no time-ordered cap drops the rest of the interval; no identity comes from a shared label or the nearest leftover; no optical-flow point is stored as a detection; no SAM session id becomes a global instance id; no model response overwrites a canonical row; no missing detection becomes confirmed absence; no box overlap confirms attachment or a finished step; a combined run adds no unreported call and a grounded run reports its second; an older document still validates and displays; a processing-chunk boundary is not a scene cut; a proposal-only fallback is not described as a detection or as SAM.

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

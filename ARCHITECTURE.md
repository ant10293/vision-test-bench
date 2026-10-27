# Architecture

One canonical analysis document feeds every screen. Relational rows are a materialization of the active revision so the inspector, object library, state explorer, workflow graph, comparison lab, and evaluation page cannot drift into separate models.

## Revisions

Each run keeps layers:

- raw model responses in `model_responses`
- a parsed revision, updated while a run is in progress
- a validated revision, created when validation finishes
- human revisions, created by edits

Edits copy the active document, change it, and rematerialize. They do not write over the validated JSON.

## Pipeline

Stages, in order: metadata, preprocess, sample frames, spatial tracking, scene analysis, Blender scene, object extraction, action analysis, state tracking, segmentation, workflow, ambiguity, validation, reinspection, transcription, audio context, artifacts. Blender scene is skipped unless the experiment left scene creation on. It runs after scene analysis and places a shape for each object in that analysis. It does not replace the selected video. Frame counts for that render appear on this stage.

Transcription and audio context are skipped immediately when the experiment leaves them off, before Whisper is imported. A failure in either stage is recorded and the visual run still finishes. See `AUDIO.md`.

A single-pass experiment makes one model call during scene analysis and records the later stages as slices of that response. A multi-stage experiment calls the model again per stage. Reinspection is skipped unless the experiment asked for it.

Progress is the count of finished stages and the frame or object counters stored on each stage. It is not a synthetic percentage.

Jobs live in SQLite. One in-process worker claims a pending job. On process start, jobs left `running` are reset to pending and their running stages are reset, so a restart resumes after the last completed stage. Completed stages are skipped. Retrying a failed stage resets that stage and the ones after it.

## Camera modes

Viewpoint and task constraint are separate fields. The three research modes are presets:

| Mode | Viewpoint | Task constraint |
| --- | --- | --- |
| Fixed camera | fixed | constrained |
| Constrained head-mounted | head-mounted | constrained |
| Unconstrained head-mounted | head-mounted | unconstrained |
| Auto | chosen from motion and scene changes | matching the choice |

The user can override auto. Image motion is not reported as object motion in the world. `world_coordinates_supported` stays false. The head-mounted baseline estimates a partial affine camera motion (Lucas–Kanade plus `estimateAffinePartial2D`) and tracks residual regions in the image. Fixed-camera tracking can use a background subtractor. Unconstrained recordings are split into scene chunks. A scene change archives the tracks it already has. It does not delete them, and it does not continue an id across the cut by itself.

## Perception

Computer vision assigns instance ids. A vision-language model may describe those ids. It does not replace them because a label matches. Tracking continuity is not an action verdict, and a box overlap is not attachment.

A new run stores one perception preset on `config_json.perception`:

| Preset | What it runs |
| --- | --- |
| `A_legacy_motion` | The repaired motion baseline. Greedy overlap inside a scene. Across a scene cut the new id is kept; a color correlation of at least 0.92 is annotated `reassociated`, and a weaker correlation is annotated `uncertain`. |
| `B_temporal` | Motion and MSER region proposals, then one-to-one association from predicted position, overlap, size, motion, and time. No appearance encoder. |
| `C_appearance` | The same tracker plus DINOv2 ViT-S/14 when that encoder can be loaded. |
| `D_appearance_segmented` | Preset C with GrabCut masks on some tracking frames. |
| `vlm_only` | Motion tracks are drawn and are not supplied as physical ids. |

An older run with no perception block is read as `A_legacy_motion`, so a retry does not silently become an appearance experiment. The default for a newly created run is `B_temporal`.

Tracking frames are a separate ffmpeg sample from the frames sent to a model. The VLM budget stays `max_frames`. Association scores and cosine values are stored as uncalibrated numbers. `scores_are_probabilities` is false. A short gap with a small margin keeps the id and marks it ambiguous. A re-identification that clears the cosine gate and the margin reuses an id as `proposed`, and the crop is stored as a candidate reference. A weak or close appearance score keeps a new id and does not enter the trusted gallery. A person can confirm, reject, merge, or split on a new human revision. Confirming marks the matching candidate reference trusted in that revision. It does not reload vectors into a later tracking pass.

Detectors are `motion`, `proposals`, and `manual`. An unknown detector name fails. A click seed is an added manual region, not a substitute algorithm. Embeddings are cached as `.npy` files under the run data directory, keyed by encoder id, version, preprocessing version, crop, and mask. They are not written into the analysis JSON.

Open-world discovery without a downloaded detector is region proposals. Those regions are not class names. Unknown objects are valid instances. DINOv2 weights download only when an appearance preset encodes a crop. If torch or the weights are missing, the encoder status is `unavailable`, appearance scores stay absent, and temporal association continues. Histogram features are a separate explicit descriptor used by the legacy color check and by tests. They are not substituted for DINOv2.

## Providers

Adapters implement one interface and register capabilities. The experiment form refuses a modality or parameter the capability record does not allow. It does not substitute another model. Adding a provider means a new adapter, a catalog entry, and a registry line.

## Evidence

`DIRECTLY_OBSERVED`, `INFERRED`, and `UNKNOWN` are separate from provenance (`AI_PREDICTION`, `CV_DETECTION`, `DETERMINISTIC`, `HUMAN_ANNOTATION`, `HUMAN_CORRECTION`). An observation ("the region is visible at 6.4s") is not stored as an interpretation ("the part was installed correctly").

## Task projects

A task is a project with several videos. Reconciliation reads the latest completed run for each clip and writes a `ProjectRevision`. It does not enqueue a new analysis job. Capture order uses timezone-aware container timestamps. Context order is stored as a hypothesis with `fact: false` and does not move a locked manual order. Cross-clip identity links stay `proposed` until a person confirms, merges, splits, or rejects them. An inter-session transition keeps the visible before/after text separate from a hypothesis about the gap. The gap is not marked directly observed.

Session-level actions remain on each clip's analysis. The project workflow adds session markers, unobserved intervals, parallel ties, and inferred procedural edges that stay unaccepted.

## Execution verification

A learned workflow is a verification specification, not a second analysis document. Learning reads one completed analysis revision and writes a versioned template: object roles, completion predicates, and dependencies. Observed steps stay distinct from required steps. A single demonstration does not mark every action or every observed order as mandatory. Only a `necessary_physical` dependency constrains order. Editing a template inserts a new version. A verification stores the template id and version it used.

An execution is the existing analysis of another clip. Verification does not resample frames, detect objects, or call a provider. If that run is still in progress, the verification stays `waiting` until the analysis job finishes. Role mapping uses labels, descriptions, relation signatures, states, and box area from the document. Embeddings are not in the analysis JSON, so they are not invented for the match. Competing hypotheses stay unresolved instead of being merged by name.

Alignment maps execution actions onto template steps by action type, roles, and state change. Absolute time is not a score. Predicate checks then read tracked relations and states in the final window. A step passes on an observable completion condition. An action label alone cannot pass. A model proposal is recorded and is not accepted as PASS or FAIL unless the chosen strategy allows it and the proposal's evidence class is `DIRECTLY_OBSERVED`. The hybrid strategy still keeps a deterministic `CANNOT_VERIFY` when the tracked evidence is insufficient.

`perception/verify.py` still audits unsupported claims inside one analysis. It is not the cross-recording verdict.

## Scene simulation

`backend/app/simulation` evaluates one kinematic scene, then Blender renders that state. Instance masks and boxes come from the id pass of those meshes. The ingested video is a normal `VideoAsset`. Analysis requests do not receive the scene file. Scoring happens after the run. Simulation jobs are their own table. The image-space scene graph on a real run keeps unobserved tracks and does not invent a metric pose. Details are in `SIMULATION.md`.

## Where files live

`data/originals` immutable uploads, `data/proxies`, `data/frames`, `data/runs/{id}` working JSON and crops, `data/runs/{id}/embeddings` cached appearance vectors, `data/transcripts/{checksum}` cached local transcripts, `data/simulations/{id}` rendered datasets, `data/learnloop.db`. API keys, if saved in the app, are Fernet-encrypted under `data/.credential_key` (mode 0600) or read from the environment. Exports omit them. Simulation exports omit `logs/`.

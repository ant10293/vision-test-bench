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

Stages, in order: metadata, preprocess, sample frames, spatial tracking, scene analysis, object extraction, action analysis, state tracking, segmentation, evidence fusion, workflow, ambiguity, validation, reinspection, transcription, audio context, artifacts.

Dedicated perception owns geometry and physical identity. A vision-language model owns semantics: labels, actions, states, relations, and workflow. Evidence fusion is the only writer of canonical geometry and never calls a model.

Transcription and audio context are skipped immediately when the experiment leaves them off, before Whisper is imported. A failure in either stage is recorded and the visual run still finishes. See `AUDIO.md`.

A single-pass experiment makes one model call during scene analysis and records the later stages as slices of that response. A multi-stage experiment calls the model again per stage. Reinspection is skipped unless the experiment asked for it.

### Semantic order

`config_json.semantic_mode` is `combined` (the default, and what a run without the field means) or `grounded`.

| | Combined | Grounded |
| --- | --- | --- |
| Scene analysis | One full call: objects, boxes, actions, states, workflow | One discovery call: objects and boxes only |
| Action analysis, state tracking, ambiguity | Separate calls in multi-stage mode, slices otherwise | Skipped and recorded as skipped |
| Evidence fusion | Resolve only, zero calls | Resolve only, zero calls |
| Workflow | A call in multi-stage mode | One interpretation call that reads resolved geometry |
| Semantic calls, single pass | 1 | 2 |

Combined interprets before segmentation, so its narrative is not conditioned on masks. That keeps the cost it had before. Grounded interprets after geometry at one extra semantic call. `coverage.semantic_calls` counts distinct semantic stages that reached a provider. Every request, including windows, merges, and locate calls, is still its own `model_requests` row with its stage. A combined run adds no call beyond its scene call; fusion's checkpoint records `model_calls: 0`.

### One clock and the whole interval

Stored times are presentation seconds from the first video frame, the clock `ffmpeg -ss` seeks on and the player shows. A subclip starts at its own zero. Its offset is saved in `data/runs/{id}/subclip.json` and every time a model returns on the subclip clock is shifted back before it is stored. Boxes are normalized to the displayed frame, after rotation. Media sent to segmentation is re-encoded upright when the file carries a rotation, so a segmentation box and a tracker box mean the same place.

A frame budget is spread across the requested interval. No stage keeps the first N frames of a time-ordered list. Semantic sampling takes an even floor of `budget // 2 + 1` frames, then one change peak per floor segment per round, then fills the widest gaps. Tracking runs at 2 FPS across the whole interval and spends the rest of `maximum_frames` at the configured rate in windows around motion and scene changes. Frames are decoded about 30 seconds at a time. One tracker runs across those chunks, so a chunk edge is not a scene cut. Coverage records the floor, dense spans, largest gap, gaps longer than the occlusion window, and `continuity` (`supported`, `partial`, or `insufficient`). A floor too sparse for the occlusion window is reported as insufficient. It is not hidden behind a continuous-looking track.

A scene boundary is `hard` only for a sustained appearance break the earlier view does not return from. A lighting change, a covered lens, a brief occlusion, and unstable motion are `soft`. Only a hard cut archives tracks. A camera occlusion longer than about a second can still read as hard.

### Evidence resolution

Raw rows from the model, the tracker, and segmentation are all kept. `AnalysisDocument.observations` is the display set: every overlay, crop, metric, prompt, and verification predicate reads it. `AnalysisDocument.raw_observations` holds the rows the display set does not show, for the perception panel.

Each row carries `geometry_origin` (`detected`, `optical_flow`, `vlm_estimated`, `unobserved`) and four supports that are never folded into one score: detection (`proposal`, `detected`, `mask`, `none`), association (`associated`, `referenced`, `weak`, `ambiguous`, `unmatched`, `conflict`), temporal (`single`, `repeated`, `flow_only`), and semantic (`unlabeled`, `provisional`, `labeled`). Motion and MSER regions are proposals. A manual seed is a detection. A SAM row with a mask is a mask. An optical-flow hold is `none`; it updates position and is not counted as a detection hit.

Per object per analyzed time, a tracker row is eligible for canonical when it is a real detection with repeated temporal support. A SAM row is eligible when its association is `associated`. Eligible rows that overlap by IoU 0.5 or more resolve to one canonical row with an `evidence_ref`. Rows that disagree become a conflict: neither is canonical, both are kept, and a `resolve_conflict_*` ambiguity says so. A model estimate within 0.45 s of a canonical row is hidden. Without a canonical row the display set shows the model estimate, then the best non-conflict measurement as a provisional row. A proposal-only track therefore stays provisional and is never described as a detection or as SAM. Resolution is idempotent. A gap in tracking adds no absence row.

### Association

Semantic objects bind to tracked instances by one-to-one assignment on temporal box IoU (accept 0.35, margin 0.12, within 0.45 s). Two or more shared times above the floor is `associated`. One shared time is `weak`. A rival within the margin is `ambiguous`. Neither weak nor ambiguous sets an instance id. A model that names a tracked id it was shown keeps it as `referenced` unless repeated shared times contradict it or two objects claim it. A track that never coexists with the chosen one is a continuation candidate: a strong match to it becomes a `proposed` re-identification hypothesis and is not merged. A label, a confidence, or the nearest leftover center is never evidence. A label change does not change an instance id.

SAM association is per tracking session. A session id is never copied into `instance_id`. A concept prompt only narrows which objects a track may bind to. A SAM track that disagrees with a bound tracker instance is a conflict.

### Retired repairs

Earlier repairs stay for model-only runs and are fenced off measured geometry with `fence_repair`, which hands a repair only the model estimates at times their object has no canonical row.

| Repair | Now |
| --- | --- |
| Shape-specific boxing text | Removed. One general localization sentence remains. |
| `reconcile_identity` | Model estimates only, at times without a canonical row |
| `drop_contained_objects`, `drop_split_objects` | Never drop an object bound to an instance |
| `locate_missing` | Skips objects that have canonical rows |
| Native-video re-ground | Only objects that still lack canonical geometry |
| Later-stage and window boxes | Stored as raw rows after fusion; they cannot replace a canonical row |
| SAM confidence and nearest-center shortcuts | Removed in favor of temporal one-to-one association |
| `apply_assembly` | Reads measured rows only. Joins are `INFERRED`, keep the model's outcome, and never mark a step completed. With no measured rows it makes no claim. |

### Historical documents

Schema version stays `1.0.0`. Every new field defaults to empty, zero, or the legacy behavior. A row with an empty `observation_role` is part of the display set, so an older document displays as it was stored. Stored revisions are not rewritten. A role on any row means evidence fusion has run; model responses are tagged `vlm_estimated` when parsed and receive a role only from the resolver.

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

The user can override auto. Image motion is not reported as object motion in the world. `world_coordinates_supported` stays false. The head-mounted baseline estimates a partial affine camera motion (Lucas–Kanade plus `estimateAffinePartial2D`) and tracks residual regions in the image. Fixed-camera tracking can use a background subtractor. Unconstrained recordings are split into scene chunks at hard cuts. A hard cut archives the tracks it already has. It does not delete them, and it does not continue an id across the cut by itself.

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

## Where files live

`data/originals` immutable uploads, `data/proxies`, `data/frames`, `data/runs/{id}` working JSON and crops, `data/runs/{id}/semantic_raw.json` the scene call before any repair, `data/runs/{id}/subclip.mp4` and `subclip.json` when a native-video model receives part of a recording, `data/runs/{id}/embeddings` cached appearance vectors, `data/transcripts/{checksum}` cached local transcripts, `data/learnloop.db`. API keys, if saved in the app, are Fernet-encrypted under `data/.credential_key` (mode 0600) or read from the environment. Exports omit them.

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

Stages, in order: metadata, preprocess, sample frames, spatial tracking, scene analysis, object extraction, action analysis, state tracking, segmentation, workflow, ambiguity, validation, reinspection, transcription, audio context, artifacts.

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

The user can override auto. Image motion is not reported as object motion in the world. `world_coordinates_supported` stays false. The head-mounted baseline estimates a partial affine camera motion (Lucas–Kanade plus `estimateAffinePartial2D`) and tracks residual blobs in the image. Fixed-camera tracking uses a background subtractor. Unconstrained recordings are split into scene chunks. A track continues across a chunk only when appearance correlation is at least 0.92, and even then the gap is marked unobserved. Weaker matches stay `uncertain` and get a new id.

## Providers

Adapters implement one interface and register capabilities. The experiment form refuses a modality or parameter the capability record does not allow. It does not substitute another model. Adding a provider means a new adapter, a catalog entry, and a registry line.

## Evidence

`DIRECTLY_OBSERVED`, `INFERRED`, and `UNKNOWN` are separate from provenance (`AI_PREDICTION`, `CV_DETECTION`, `DETERMINISTIC`, `HUMAN_ANNOTATION`, `HUMAN_CORRECTION`). An observation ("the region is visible at 6.4s") is not stored as an interpretation ("the part was installed correctly").

## Task projects

A task is a project with several videos. Reconciliation reads the latest completed run for each clip and writes a `ProjectRevision`. It does not enqueue a new analysis job. Capture order uses timezone-aware container timestamps. Context order is stored as a hypothesis with `fact: false` and does not move a locked manual order. Cross-clip identity links stay `proposed` until a person confirms, merges, splits, or rejects them. An inter-session transition keeps the visible before/after text separate from a hypothesis about the gap. The gap is not marked directly observed.

Session-level actions remain on each clip's analysis. The project workflow adds session markers, unobserved intervals, parallel ties, and inferred procedural edges that stay unaccepted.

## Where files live

`data/originals` immutable uploads, `data/proxies`, `data/frames`, `data/runs/{id}` working JSON and crops, `data/transcripts/{checksum}` cached local transcripts, `data/learnloop.db`. API keys, if saved in the app, are Fernet-encrypted under `data/.credential_key` (mode 0600) or read from the environment. Exports omit them.

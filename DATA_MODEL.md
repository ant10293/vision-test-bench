# Data model

Schema version `1.0.0` for a single-clip analysis document. A task project is the same `Project` with `is_task` set, plus recording sessions and a versioned project reading. Clip analyses stay on `AnalysisRun`.

## Entities

Project, RecordingSession, VideoAsset, FrameAsset, AnalysisRun, AnalysisRevision, PipelineStageRun, Job, PromptVersion, ProviderSecret, ProviderStatus, ManualModel, ModelRequest, ModelResponse, DetectedObject, ObjectObservation, ObjectState, ObjectStateTransition, ObjectRelationship, ObjectImage, SegmentationMask, Action, Workflow, WorkflowStep, WorkflowDependency, Ambiguity, Annotation, GroundTruthDataset, EvaluationResult, Comparison, Artifact, SpatialTrackPoint, SceneChunk, CanonicalObject, IdentityLink, InterSessionTransition, ProjectRevision, OrderHypothesis, ProjectGroundTruth.

A video is stored once. Many runs point at it. Frames may belong to the upload preview (`run_id` null) or to a run's sampling plan.

## Evidence and provenance

Every conclusion carries an evidence class:

- `DIRECTLY_OBSERVED` — a stored frame or interval supports the statement
- `INFERRED` — the statement goes beyond what that frame shows
- `UNKNOWN` — the recording does not settle it

Provenance is independent: AI prediction, computer-vision detection, deterministic processing, human annotation, or human correction. A fixture is human annotation and is flagged `fixture: true` on the document and the run.

## Observations and interpretations

An observation is a box, a visibility, and a description at a timestamp. An action outcome (`completed`, `attempted`, `failed`, `unknown`) is an interpretation and must not be copied from the verb. Proximity is a relationship kind, not attachment.

## Coordinates

Boxes are normalized to the display frame after ffmpeg autorotation. `coordinate_frame` is `image` for the baseline. `world_coordinates_supported` is false unless a future calibrator sets it. The scene snapshot at time t returns states whose intervals cover t. It returns the nearest observation only inside 0.75 seconds and labels a larger gap instead of inventing a box.

## Export

`GET /api/runs/{id}/export` writes a zip: manifest (checksum, model, prompt snapshot, coverage, app version), analysis JSON, requests, raw responses, object images, and the original video when it is under 80 MB. Secrets are not included. Import accepts schema `1.0.0` and reuses a video with the same checksum. Other schema versions are rejected until a migration exists.

## Ground truth

Promoting a revision copies that document into `GroundTruthDataset`. Evaluation stores true positives, false positives, and false negatives for actions matched one-to-one by interval overlap, plus object matches labeled `assisted_label` unless the researcher set them. Errors point at timestamps.

## Task projects

A task is a project that holds many clips. Each clip keeps its own file, capture timestamp, file-modification time, and upload time. Capture time comes from QuickTime or container tags when those tags include a timezone. A naive or conflicting tag is stored and is not turned into a UTC order. Clips without a reliable time stay unordered.

`CanonicalObject` and `IdentityLink` propose cross-clip identity. A proposal stays uncertain until a person confirms, merges, splits, or rejects it. Two similar objects in one clip are not given one identity. `InterSessionTransition` stores the observed before/after text apart from any hypothesis about the unfilmed interval. An unfilmed interval is `INFERRED` or `UNKNOWN`, not `DIRECTLY_OBSERVED`.

`ProjectRevision` versions the project reading. Adding a clip writes a new revision and leaves earlier clip runs in place. `GET /api/tasks/{id}/export` writes `manifest.json` (`schema_version` 1.0.0, `task_extension` 1.1.0) and `task.json`. Secrets are not included. Project metrics compare that reading with human ground truth and report matcher confidence in a separate block that is not accuracy.

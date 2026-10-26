# Data model

Schema version `1.0.0` for a single-clip analysis document. A task project is the same `Project` with `is_task` set, plus recording sessions and a versioned project reading. Clip analyses stay on `AnalysisRun`.

## Entities

Project, RecordingSession, VideoAsset, FrameAsset, AnalysisRun, AnalysisRevision, PipelineStageRun, Job, PromptVersion, ProviderSecret, ProviderStatus, ManualModel, ModelRequest, ModelResponse, DetectedObject, ObjectObservation, ObjectState, ObjectStateTransition, ObjectRelationship, ObjectImage, SegmentationMask, Action, Workflow, WorkflowStep, WorkflowDependency, Ambiguity, Annotation, GroundTruthDataset, EvaluationResult, Comparison, Artifact, SpatialTrackPoint, SceneChunk, CanonicalObject, IdentityLink, InterSessionTransition, ProjectRevision, OrderHypothesis, ProjectGroundTruth, TranscriptSegment, AudioAlignment.

A video is stored once. Many runs point at it. Frames may belong to the upload preview (`run_id` null) or to a run's sampling plan.

## Evidence and provenance

Every conclusion carries an evidence class:

- `DIRECTLY_OBSERVED` — a stored frame or interval supports the statement
- `INFERRED` — the statement goes beyond what that frame shows
- `UNKNOWN` — the recording does not settle it

Provenance is independent: AI prediction, computer-vision detection, deterministic processing, human annotation, or human correction. A fixture is human annotation and is flagged `fixture: true` on the document and the run.

## Observations and interpretations

An observation is a box, a visibility, and a description at a timestamp. An action outcome (`completed`, `attempted`, `failed`, `unknown`) is an interpretation and must not be copied from the verb. Proximity is a relationship kind, not attachment.

`TranscriptSegment` stores clip-relative speech. `text_model` is the recognizer output and stays when a person edits `text`. `AudioAlignment` links a segment to action, object, and step ids from that same clip, with a classification, an explanation, and `speech_is_not_completion`. Those rows are not visual observations. A corrected alignment keeps `local_classification`.

## Coordinates

Boxes are normalized to the display frame after ffmpeg autorotation. `coordinate_frame` is `image` for the baseline. `world_coordinates_supported` is false unless a future calibrator sets it. The scene snapshot at time t returns states whose intervals cover t. It returns the nearest observation only inside 0.75 seconds and labels a larger gap instead of inventing a box.

## Export

Perception is an optional object on the same analysis document: instances, hypotheses, appearance-reference metadata, diagnostics, and manual identity corrections. It does not contain embedding vectors. Schema version stays `1.0.0` because the fields are optional and older documents still load. Historical track rows keep an empty `track_status` rather than being relabeled `tracked`.

`SpatialTrackPoint` gained `track_status`, `association_score`, and `detector_id` in Alembic revision `a1c9e4d27b10`. SQLite databases created by `create_all` also receive those columns from `schema_upgrade`. Embedding files live beside the run, not in the document or the inspector payload. The bundle sends at most 4000 compact track points and says when the list was truncated.

`GET /api/runs/{id}/export` writes a zip: manifest (checksum, model, prompt snapshot, coverage, app version), analysis JSON, transcripts JSON, alignments JSON, requests, raw responses, object images, and the original video when it is under 80 MB. Secrets are not included. Transcript rows are not part of the analysis document, so the document schema stays `1.0.0`. Import accepts schema `1.0.0` and reuses a video with the same checksum. Other schema versions are rejected until a migration exists.

## Ground truth

Promoting a revision copies that document into `GroundTruthDataset`. Evaluation stores true positives, false positives, and false negatives for actions matched one-to-one by interval overlap, plus object matches labeled `assisted_label` unless the researcher set them. Errors point at timestamps.

Identity metrics are a separate block. They are computed only when both documents have instance ids or track refs on observations. Semantic labels alone leave `evaluated: false`. Observation pairs match by time and box. Predicted ids are mapped one-to-one onto ground-truth ids by overlap. IDF1 counts a true positive only after that mapping. The block also counts identity switches, false merges, coverage, re-identification after a gap, abstention, and how unresolved hypotheses were later marked. Hypothesis resolution is not accuracy. A false merge's timestamp is the first matched observation of that predicted id, so the inspector can seek it.

## Task projects

A task is a project that holds many clips. Each clip keeps its own file, capture timestamp, file-modification time, and upload time. Capture time comes from QuickTime or container tags when those tags include a timezone. A naive or conflicting tag is stored and is not turned into a UTC order. Clips without a reliable time stay unordered.

`CanonicalObject` and `IdentityLink` propose cross-clip identity. A proposal stays uncertain until a person confirms, merges, splits, or rejects it. Two similar objects in one clip are not given one identity. `InterSessionTransition` stores the observed before/after text apart from any hypothesis about the unfilmed interval. An unfilmed interval is `INFERRED` or `UNKNOWN`, not `DIRECTLY_OBSERVED`.

`ProjectRevision` versions the project reading. Adding a clip writes a new revision and leaves earlier clip runs in place. `GET /api/tasks/{id}/export` writes `manifest.json` (`schema_version` 1.0.0, `task_extension` 1.1.0) and `task.json`. Secrets are not included. Project metrics compare that reading with human ground truth and report matcher confidence in a separate block that is not accuracy.

## Workflow verification

Alembic revision `b8d4e2a61c30` merges `a1c9e4d27b10` and `f7a2c6e91b04` and adds:

- `learned_workflow_templates` — immutable template versions (`family_id`, `version`, `parent_id`, `document_json`, `acceptance_status`, source run, revision, and video).
- `verification_runs` — template id and version, execution run, revision, and video, status, overall result, config, the current report, and `machine_report_json`. The machine report is written once and is not replaced by a human edit.
- `step_verifications` — verdict, execution state, error category, matched action and object ids, explanation, uncertainty, evidence, and history.
- `verification_evidence` — video id, timestamp, evidence class, provenance, side, related object ids, and observation text. Paths are not stored.
- `verification_role_mappings` — assigned ids, score, competing hypotheses, and note. A human assignment does not rewrite tracks.
- `verification_reinspections` — question, frame timestamps, status, previous verdict, and output.
- `verification_ground_truth` — human labels for a verification. Evaluation compares the report with those labels. Model agreement is not stored as accuracy.

The original analysis documents stay unchanged. Export schema `verification_report_1` cites template and revision ids and omits filesystem paths.

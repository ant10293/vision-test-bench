# Implementation progress

Active phase: perception stays on the canonical analysis document. Live provider calls and a live DINOv2 forward pass remain unverified.

## Completed

- Monorepo, FastAPI, SQLite, Alembic initial revision, Vite React app, one `scripts/dev.sh`.
- Video ingest: checksum, immutable original, ffprobe, proxy when needed, thumbnail, preview frames from presentation timestamps, contact sheet.
- Sampling presets, adaptive sampling from flow, frame difference, and scene change, with a stored reason per frame.
- Canonical document, validation, revisions, materialization, crops from real frames.
- Three research modes with a shared schema and a pluggable spatial baseline. World coordinates are not claimed.
- Fixture provider for the three synthetic recordings and a deterministic motion baseline.
- Provider adapters for OpenAI, Anthropic, Gemini, Meta Muse, SAM 3.1 parsing, and an OpenAI-compatible endpoint. Unsupported modalities fail closed.
- Durable jobs, restart recovery, stage retry, cancel.
- Inspector, object library, state explorer, workflow graph, step mode, pipeline monitor, prompts, providers, comparison, ground truth, evaluation, export and import.
- Task projects: multiple clips and sessions, capture timestamps kept apart from file modification and upload time, partial metadata order, context hypotheses, manual lock, cross-clip identity with confirm/merge/split/reject, inter-session observed change separate from hypothesis, versioned reconciliation that does not rerun clip analyses, hierarchical timeline, object history, project metrics, and a task export.
- Backend tests (31) and frontend unit tests. Ruff is clean on `app` and `tests`.
- Optional local transcription and audio-visual classification. Both switches default off. See `AUDIO.md`. The automated tests use a stand-in for Whisper. A live model download and a live paid comparison of transcript text were not run.
- Perception on the existing document: track lifecycle, one-to-one association, separate tracking frames, presets A/B/C/D and VLM-only, optional DINOv2 with an explicit unavailable state, GrabCut during tracking, instance ids in the model prompt, identity metrics that require instance annotations, and inspector overlays. Embeddings stay out of the analysis JSON. Alembic revision `a1c9e4d27b10` adds track status, association score, and detector id.
- Browser pass on the seeded lab: dashboard counts, inspector with attempted versus completed actions, frame step, object crops from the footage, a human label revision, and an 8-node workflow graph. All three fixture modes completed with `world_coordinates_supported` false. The unconstrained fixture keeps a second red block as an uncertain identity.
- Learned workflow verification on cached analyses. A demonstration revision becomes a versioned template with completion predicates. A later execution is role-mapped, aligned, and judged from tracked relations and states. Verdicts are PASS, FAIL, CANNOT_VERIFY, and NOT_APPLICABLE. Overall results are PASS, FAIL, INCONCLUSIVE, INCOMPLETE, and NOT_EVALUATED. Human step and role corrections keep the first machine report. Alembic revision `b8d4e2a61c30`. The task page has a Verification section with the template, a side-by-side step seek, and timeline markers. No provider was called. Embeddings are not in the analysis document, so role matching does not use them. Reinspection records a plan and does not send it.
- Browser pass on project "Verification fixture": learn from the demonstration, accept version 2, verify the execution, overall Fail, the ring step Fail with missing component, the other required steps Pass. The failure marker showed demonstration 0:10 and execution 0:45. Confirming the verdict kept Fail and showed the earlier machine verdict. The attached playback file is 8 seconds, so the player clamped those later timestamps.
- Scene simulation. A kinematic assembly scene is evaluated in Python and placed in Blender 5.2 Eevee. RGB, instance masks, boxes, and visibility come from that placement. Variants cover a missing washer, occlusion, a bad placement, a corrected miss, order, two similar washers, and a moving camera. The video is ingested as a normal asset. The deterministic tracker runs without the scene file, and the score is a separate step. Alembic revision `c5e1a9b44d18`. Scene Lab is `/scene-lab`. See `SIMULATION.md`. Depth is not exported. No field head-mounted recording was processed. The unconstrained perception check uses the lab's synthetic fixture.

## Pending

- A live validation call and a live inference call for each paid provider. No key was used, so those paths are unverified.
- SAM raster masks when `meta-sam-parser` is installed, against a real response. The grammar parser is unit-tested on a sample string.
- Visual SLAM and calibrated world coordinates. The interfaces exist; the baseline does not pretend to have them. The scene graph keeps image-space tracks across gaps and leaves pose null.
- Metric depth, mesh catalogs, and image decals for simulated objects.
- SAM video tracking. The configured adapter reports `video_tracking: false`.
- Reloading a human-confirmed appearance gallery into a later tracking pass. Confirm updates the revision. It does not copy cached vectors back into the tracker.
- A live DINOv2 encode on this machine. The loader and the unavailable path are implemented. Weights were not downloaded for the tests.
- A second annotator. The ground-truth table has an annotator field; agreement is not computed yet.
- Playwright: the spec is in `frontend/e2e/lab.spec.ts`. It was not executed in this session because port 8000 was already serving the lab and Chromium was not installed.
- Live faster-whisper transcription on a real recording, and a live provider call that compares transcript text with frames. The local classifier and the skip paths are tested.
- Sending a reinspection plan to a vision provider. The plan, frame list, and finding gate exist. `provider_invoked` stays false.
- Appearance embeddings in role matching. The analysis document does not carry them, and the matcher does not invent a cosine score.

## Known bugs

- If the API is down on the first dashboard fetch, the page used to stay on "Loading". It now shows the error.
- Vite moves to the next port when 5173 is taken. In this session the UI came up on 5174.

## Integration limitations

- OpenAI and Anthropic have no native-video path because the current public APIs do not accept video files for these models.
- Meta file upload for videos larger than 8 MB uses the files API. If the purpose string is rejected, the error is returned and the run does not switch modality.
- GrabCut cutouts are computer-vision estimates initialized from boxes, not SAM.

## Next

- When credentials exist, validate one provider and record the result here. Do not mark it verified before that.
- `npx playwright install chromium` and `npx playwright test` from `frontend` with port 8000 free.

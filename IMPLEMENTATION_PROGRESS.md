# Implementation progress

Active phase: multi-session tasks on the same canonical model. Live provider calls remain unverified.

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
- Backend tests (23) and frontend unit tests. Ruff is clean on `app` and `tests`.
- Browser pass on the seeded lab: dashboard counts, inspector with attempted versus completed actions, frame step, object crops from the footage, a human label revision, and an 8-node workflow graph. All three fixture modes completed with `world_coordinates_supported` false. The unconstrained fixture keeps a second red block as an uncertain identity.

## Pending

- A live validation call and a live inference call for each paid provider. No key was used, so those paths are unverified.
- SAM raster masks when `meta-sam-parser` is installed, against a real response. The grammar parser is unit-tested on a sample string.
- Visual SLAM and calibrated world coordinates. The interfaces exist; the baseline does not pretend to have them.
- A second annotator. The ground-truth table has an annotator field; agreement is not computed yet.
- Playwright: the spec is in `frontend/e2e/lab.spec.ts`. It was not executed in this session because port 8000 was already serving the lab and Chromium was not installed.

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

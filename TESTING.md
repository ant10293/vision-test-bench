# Testing

## Backend

```bash
cd backend && .venv/bin/python -m pytest tests -q
cd backend && .venv/bin/ruff check app tests
```

Covered without network:

- presentation-timestamp snapping and uniform sampling
- prompt text does not name the hardware task
- fixture documents for all three modes validate, including attempted versus completed outcomes
- SAM special-token box parsing
- OpenAI and Anthropic refuse native video
- Muse catalog rejects temperature and does not list `none` as a reasoning effort
- interval matching still pairs actions when the lists are in different orders
- upload, probe, frame extraction, fixture provider, object crops, human revision, and zip export
- a running job is requeued after a restart
- temporal association on synthetic boxes: motion, scale, rotation, a short occlusion, a long gap that keeps a new id, two objects that pass, and a scene change that archives the earlier track
- one-to-one assignment, embedding cache keys, L2 normalization, and a missing encoder that raises instead of substituting a histogram
- trusted appearance references are not written from a tentative re-identification
- window merge keeps two objects with the same label and different instance ids
- identity metrics count a switch and a false merge, and they withhold a score when only labels exist
- confirming a hypothesis promotes the matching candidate reference and leaves the others as candidates
- learned-workflow fixtures: a correct slower execution, a visible missing component, an occluded component that stays unverified, harmless reorder, a required order violation, an incorrect component, a corrected mistake, similar objects that stay ambiguous, an unsupported model PASS that is rejected, and a template edit that leaves the earlier verification on the original version

`tests/verification_fixtures.py` is the only place those assembly names appear. `app/verification` is scanned so the engine does not name a specific part. No paid provider is called. Reinspection status in these tests is `not_sent`.

Scene tests that do not need Blender cover the schema, insertion constraints, counterfactual pairs, visibility verdicts, dataset integrity, and the image-space scene graph. `test_simulation_render.py` and `test_simulation_api.py` render with the installed Blender and skip, with a reason, when `find_blender()` is `None`. They are not replaced with a mock. The API test ingests the clip, runs the deterministic tracker, and checks that the run config does not contain the scene.

Paid provider tests are not in this suite. Do not describe a live integration as tested until a validation or generation call has succeeded with a real key.

## Frontend

```bash
cd frontend && npx tsc --noEmit && npx vitest run
```

Unit tests cover observation selection at the playhead, refusal to hold a box across a long gap, nearest-frame lookup, one track sample nearest the playhead, verification verdict colors that keep pass, fail, uncertain, and corrected distinct, and the Blender-to-three.js display conversion.

## Browser

```bash
cd frontend && npx playwright install chromium && npx playwright test
```

The Playwright run boots the API with seeding enabled, waits until a fixture analysis completes, opens it, renames an object, opens the workflow graph, and downloads the export zip.

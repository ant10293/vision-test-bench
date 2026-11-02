# Testing

## Backend

```bash
cd backend && .venv/bin/python -m pytest tests -q
cd backend && .venv/bin/ruff check app tests
```

The timeline tests and the pipeline tests encode clips with the local `ffmpeg`. The alignment tests skip when `ffmpeg` is not on the path. Nothing in the suite downloads model weights or calls a paid provider. Where a provider is needed, a fake returns an authored document and every request is still recorded as a `model_requests` row.

Covered without network:

- presentation-timestamp snapping and uniform sampling
- prompt text does not name the hardware task, and the scene prompt does not mention rings, facets, or a head or top
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

Perception architecture:

- `test_sampling.py`: activity after 20 seconds is sampled and the end is kept, a busy opening cannot take the whole budget, a truncated tail gets a closing sample, a capped tracking plan keeps a 2 FPS floor, a budget too sparse for the occlusion window is reported as insufficient, and a sustained new view is a hard cut while a brief occlusion and a lighting change are soft. These are synthetic signals.
- `test_timeline.py`: generated clips whose frames carry their own index. Extracted frames carry their source time within one frame, a subclip keeps its bounds and maps back to source time, capped tracking crosses four processing chunks to the end without a hard cut, and a rotated file reaches segmentation in the same displayed frame the tracker sees.
- `test_grounding.py`: two identical labels stay two instances, a model estimate never overwrites a canonical row, one shared time is weak and binds nothing, a label change keeps the instance, a cross-cut match is a hypothesis, a proposal-only track stays provisional, a flow hold is not a detection, a gap adds no absence row, disagreeing measurements become a conflict, resolution is idempotent, and an older document still displays. A pipeline case runs combined and grounded with a fake provider: combined makes requests only in scene analysis and fusion records zero calls; grounded makes requests in scene analysis and workflow and records two semantic calls. A second case runs two providers with the same response and checks that geometry and instances are identical.
- `test_heuristics.py`: each fenced repair. Two canonical nested instances both remain, a model-only size glitch is still repaired, a canonical row is never edited, two touching identical instances stay two, a canonical track causes no locate call, canonical rows skip the re-ground call, a later-stage box does not replace a canonical row, model-only boxes make no assembly claim, and sustained contact stays inferred and does not complete.
- `test_benchmark.py`: the harness on synthetic cases. These scores check the harness. They are not accuracy.

- `test_spatial_prompts.py`, `test_recovery_selection.py`, `test_recovery_execution.py`: prompt tokens and frame mapping (with real ffmpeg clips, including variable frame rate and rotation), seed trust, recovery targets, validation outcomes, resolver protection, and execution safety. SAM is a fake. These synthetic sequences check behavior and are not accuracy. See `RECOVERY.md`.

`tests/verification_fixtures.py` is the only place those assembly names appear. `app/verification` is scanned so the engine does not name a specific part. No paid provider is called. Reinspection status in these tests is `not_sent`.

Paid provider tests are not in this suite. Do not describe a live integration as tested until a validation or generation call has succeeded with a real key.

## Benchmark

```bash
cd backend && .venv/bin/python -m tests.benchmark_harness ../data/benchmark
```

Scores every case folder under the path. The report lists which cases are human-annotated. With none, it says that nothing in it measures real-world accuracy. See `RESEARCH_METHODOLOGY.md`.

## Frontend

```bash
cd frontend && npx tsc --noEmit && npx vitest run
```

Unit tests cover observation selection at the playhead, refusal to hold a box across a long gap, nearest-frame lookup, one track sample nearest the playhead, verification verdict colors that keep pass, fail, uncertain, and corrected distinct, canonical rows on the solid measured layer with provisional tracker rows dashed, raw rows listed near the playhead and never drawn, nested diagnostics flattened for the perception panel, the coverage line, and the grounded setup label.

## Browser

```bash
cd frontend && npx playwright install chromium && npx playwright test
```

The Playwright run boots the API with seeding enabled, waits until a fixture analysis completes, opens it, renames an object, opens the workflow graph, and downloads the export zip.

## Meta output contracts

`backend/tests/test_meta_contracts.py` covers schema correctness, runaway output, realistic answers, and the discovery, interpretation, single-pass, multi-pass, and multi-window paths with a fake provider. It needs no API key.

The live check spends real tokens and only runs when asked:

```bash
cd backend && .venv/bin/python scripts/live_meta_contract.py --yes --model muse-wonder-1.0 --video ../data/devlab/proxies/069a8b2c9715.mp4
```

It reports whether Meta accepted each schema, whether the response completed, whether it validated, its token usage, and whether repeated keys appeared. A failure is evidence about Meta's decoder. It has no non-strict fallback.

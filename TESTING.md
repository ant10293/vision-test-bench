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

Paid provider tests are not in this suite. Do not describe a live integration as tested until a validation or generation call has succeeded with a real key.

## Frontend

```bash
cd frontend && npx tsc --noEmit && npx vitest run
```

Unit tests cover observation selection at the playhead, refusal to hold a box across a long gap, and nearest-frame lookup.

## Browser

```bash
cd frontend && npx playwright install chromium && npx playwright test
```

The Playwright run boots the API with seeding enabled, waits until a fixture analysis completes, opens it, renames an object, opens the workflow graph, and downloads the export zip.

# Development

## Dependencies

- Python 3.12. The repository is developed against `/usr/local/bin/python3.12` because current OpenCV wheels are reliable there. The system `python3` may be newer.
- Node.js 20 or newer and npm.
- ffmpeg and ffprobe on `PATH`.

## Install

```bash
/usr/local/bin/python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -e "./backend[dev]"
npm install --prefix frontend
```

Optional SAM mask raster decoding: `backend/.venv/bin/pip install -e "./backend[sam]"`. Box parsing does not need that package.

## Run

`./scripts/dev.sh` starts the API on port 8000 with the worker inside the process, and Vite on port 5173. Vite proxies `/api`.

`LEARNLOOP_AUTO_SEED=1` renders the three fixture clips into `data/` the first time the database is empty. Set it to `0` to start blank. `LEARNLOOP_DATA_DIR` and `LEARNLOOP_DATABASE_URL` override locations.

## Database

Startup calls `Base.metadata.create_all`. Alembic is configured for the same metadata:

```bash
cd backend && .venv/bin/alembic upgrade head
```

Use one of those on an empty database, not both. `create_all` is what the app does.

## Tests

```bash
cd backend && .venv/bin/ruff check app tests && .venv/bin/python -m pytest tests -q
cd frontend && npx tsc --noEmit && npx vitest run
cd frontend && npx playwright test
```

Playwright starts its own API with a separate `data/e2e` database and `LEARNLOOP_AUTO_SEED=1`. Install a browser once with `npx playwright install chromium`.

## Layout

- `backend/app/video` probe, sampling, spatial baseline
- `backend/app/providers` adapters
- `backend/app/pipeline` prompts, parsing, validation, runner
- `backend/app/services` ingest, materialize, corrections, export
- `backend/app/demo` synthetic recordings and authored analyses
- `frontend/src/pages` the research screens
- `frontend/src/components` player, timeline, workflow graph

Files prefer a few hundred lines. `scenarios.py` and `runner.py` are longer because splitting them would separate a fixture from the document it must match, or a stage from the job that runs it.

# LearnLoop Research Lab

Local research environment for asking whether a general-purpose vision-language model can reconstruct an unfamiliar physical procedure from video, and whether that reconstruction is observable enough to verify.

The lab stores the original video, the frames that were actually submitted, the model's raw response, a validated document, and any human correction as separate layers. Fixture analyses are labeled as authored references. They are not model output.

## Run it

System tools: Python 3.12, Node 20+, ffmpeg and ffprobe. Scene Lab also needs Blender on `PATH` or `LEARNLOOP_BLENDER_BIN`. Headless Eevee is enough. Rendering still works without a GPU.

```bash
/usr/local/bin/python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -e "./backend[dev]"
npm install --prefix frontend
cp .env.example .env
./scripts/dev.sh
```

Open http://127.0.0.1:5173. The API is http://127.0.0.1:8000. On a fresh data directory the lab renders three fixture recordings, queues an analysis for each research mode, and the worker completes them. No API key is required for that path.

## What you can do without a key

- Upload MP4, MOV, or WebM. The original file is copied and never overwritten.
- Inspect timestamped frames, a contact sheet, and a browser proxy when the source codec needs one.
- Run the **fixture** provider on a generated fixture (checksum-locked) or the **deterministic** motion baseline on any video.
- Inspect objects, actions, state, workflow, ambiguities, and coverage.
- Correct a label. The raw document stays; the correction is a new human revision.
- Compare two runs, score a run against a saved ground-truth revision, and export a zip with no credentials in it.
- Open Scene Lab, render a mechanical-assembly variant, and score a deterministic analysis against the hidden scene. See `SIMULATION.md`.

## What needs a key

OpenAI, Anthropic, Gemini, Meta Muse, and an optional OpenAI-compatible endpoint. Configure keys on the Providers page or through environment variables. OpenAI can instead use a ChatGPT plan through the local Codex CLI, the same way Anthropic can use Claude Code and Cursor can use the agent CLI. A provider is shown as connected only after a real validation request succeeds. Live inference was not executed in this workspace. See `PROVIDERS.md`.

## Documents

- `ARCHITECTURE.md` — pipeline, jobs, three camera modes
- `DEVELOPMENT.md` — setup, ports, tests
- `PROVIDERS.md` — adapters and capability limits
- `DATA_MODEL.md` — entities, evidence, revisions
- `RESEARCH_METHODOLOGY.md` — what the lab is allowed to claim
- `TESTING.md` — how to run tests
- `IMPLEMENTATION_PROGRESS.md` — current status
- `SIMULATION.md` — kinematic scenes, Blender renders, and how scoring stays off the inference path

# Providers

Capabilities live in `backend/app/providers/catalog.py`. The experiment form reads them. If a model id is typed in by hand, the catalog returns a conservative record: native video only for Gemini and non-SAM Meta models, temperature off for Meta, image windows of 16 except Anthropic's 20.

Prices in the catalog are approximate list prices for planning. They are not invoices. Unknown models are unpriced.

## Local

- `fixture` / `authored-fixture` returns the authored document only when the video checksum is in `data/fixture_index.json`. Any other file is refused.
- `deterministic` / `motion-baseline` emits motion regions and `motion` intervals. It does not name objects or decide that an action succeeded.
- `manual_json` is the import path. It is not a model call.

## OpenAI

Responses API, timestamped `input_image` frames, structured output via `json_schema` with `strict: false` so the research schema is not silently rewritten. Native video is refused. Public documentation does not accept video files on this API. A successful API validation is `models.list`. Live calls were not run here.

The ChatGPT plan, including Plus, is a separate transport. It calls the local `codex` executable with `exec`, a read-only sandbox, and one `--image` per frame. `--yolo` and write sandboxes are not passed. The prompt is sent on stdin. Structured output uses `--output-schema`. Temperature is rejected if set. `OPENAI_API_KEY` is removed from the child environment so a key cannot move the run onto API billing. Validation is `codex login status`, and only a ChatGPT login counts. `codex login` keeps the session in the CLI. Plan usage is recorded unpriced. Live calls were not run here.

## Anthropic

Image blocks only. Native video is refused. Structured output is a forced `record_analysis` tool. Sequences longer than the image cap are windowed and then merged with a text call. Validation is `models.list`. Live calls were not run here.

## Cursor

The local `agent` CLI, in ask mode, with the workspace limited to the frames for that call. `--force` is not passed. Native video is refused. Temperature is rejected if set. A linked Cursor account is the default transport and is recorded as unpriced plan usage. The API key transport uses `CURSOR_API_KEY` or the key stored on the Providers page, and the key is placed in the child environment rather than on the command line. Validation is `agent status`. Model discovery is `agent --list-models`. Live calls were not run here.

## Gemini

`google-genai`. Sampled frames are inline image parts. Native video uploads through the Files API and polls until the file is `ACTIVE`. Both paths are recorded on the run so a native submission is not described as a frame sample. Validation is `models.list`. Live calls were not run here.

## Meta

`https://api.meta.ai/v1` using an OpenAI-compatible client. Every analysis call that sends a schema uses `strict: true`, and nothing falls back to unconstrained output. Meta constrains output to the schema it receives, and `strict` only makes the server check that schema, so the schema itself has to be closed. `app/schemas/generation.py` declares what a model may write, and `app/pipeline/contracts.py` compiles and checks one contract per call: `learnloop_discovery`, `learnloop_interpretation`, `learnloop_single_pass`, and one for each multi-pass stage. A response must finish and match its contract exactly. Unexpected keys, missing lists, malformed JSON, and incomplete answers fail the run with the stage and provider request id, and they do not auto-retry because a retry spends the same budget. An explicit empty list is a valid answer. The permissive parser in `parsing.py` remains for other providers, stored responses, and imports. Each `model_requests` row records the schema name, hash, version, strict flag, and output limit, and each `model_responses.raw_json` records status, incomplete reason, and `output_check`. `backend/scripts/live_meta_contract.py` is an explicitly invoked, paid check of these contracts. The standard test suite never calls Meta. Muse Spark models are the analysis models. `sam-3.1` is refused as an analysis model and is used only by the segmentation stage. Temperature is rejected if set, rather than dropped. Reasoning effort is sent only when the catalog lists it. Small videos can be data URLs; larger ones are uploaded with the files API. If that upload purpose is rejected, the error is shown and the modality is not changed.

SAM responses are parsed from the documented special-token grammar (frame index, box, mask payload). A SAM track is linked to a LearnLoop object only when box overlap supports it. Otherwise the track stays unassigned and `identity_uncertain`. Raster decoding uses `meta-sam-parser` when installed and otherwise stores the payload without inventing a raster. The adapter's recorded capabilities are image concept prompts, no box prompts, no point prompts, and `video_tracking: false`. The current call sends one image and a text concept. It does not submit a video, and the image adapter is not treated as a video tracker. Live SAM calls were not run here.

Local perception does not use a provider key. `motion` is background subtraction or compensated frame difference. `proposals` adds MSER regions. `manual` waits for a seed. GrabCut is the local segmenter. `histogram_v1` is a 24-dimensional color descriptor, not a recognition model. DINOv2 ViT-S/14 (`dinov2_vits14`, 384-d CLS, ImageNet normalization, L2-normalized, preprocess `bgr-mask-224-imagenet-v1`) loads through `torch.hub` only when an appearance preset encodes a crop. Device order is MPS, then CUDA, then CPU. If torch or the weights are unavailable, the stage records `encoder_status: unavailable` and does not switch to the histogram. This environment has not executed a DINOv2 forward pass.

This integration is optional. The fixture and deterministic providers, and the other vendors, do not depend on it.

## OpenAI-compatible

Same frame client as OpenAI, with native video forced off and the base URL taken from the provider page or `OPENAI_COMPATIBLE_BASE_URL`.

## Credentials

Environment variables: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`, `MODEL_API_KEY` or `META_MODEL_API_KEY`, `OPENAI_COMPATIBLE_API_KEY`, `CURSOR_API_KEY`. The Providers page can store a key in the encrypted local table instead. Responses, logs, and exports contain a hint at most (last four characters), never the key. Cursor account linking does not store a key; `agent login` keeps the session in the CLI. ChatGPT plan linking does not store a key; `codex login` keeps the session in the CLI.

`LEARNLOOP_LIVE_PROVIDERS=1` is the switch for future opt-in tests. The default suite does not call a paid API.

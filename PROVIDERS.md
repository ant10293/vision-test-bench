# Providers

Capabilities live in `backend/app/providers/catalog.py`. The experiment form reads them. If a model id is typed in by hand, the catalog returns a conservative record: native video only for Gemini and non-SAM Meta models, temperature off for Meta, image windows of 16 except Anthropic's 20.

Prices in the catalog are approximate list prices for planning. They are not invoices. Unknown models are unpriced.

## Local

- `fixture` / `authored-fixture` returns the authored document only when the video checksum is in `data/fixture_index.json`. Any other file is refused.
- `deterministic` / `motion-baseline` emits motion regions and `motion` intervals. It does not name objects or decide that an action succeeded.
- `manual_json` is the import path. It is not a model call.

## OpenAI

Responses API, timestamped `input_image` frames, structured output via `json_schema` with `strict: false` so the research schema is not silently rewritten. Native video is refused. Public documentation does not accept video files on this API. A successful validation is `models.list`. Live calls were not run here.

## Anthropic

Image blocks only. Native video is refused. Structured output is a forced `record_analysis` tool. Sequences longer than the image cap are windowed and then merged with a text call. Validation is `models.list`. Live calls were not run here.

## Gemini

`google-genai`. Sampled frames are inline image parts. Native video uploads through the Files API and polls until the file is `ACTIVE`. Both paths are recorded on the run so a native submission is not described as a frame sample. Validation is `models.list`. Live calls were not run here.

## Meta

`https://api.meta.ai/v1` using an OpenAI-compatible client. Muse Spark models are the analysis models. `sam-3.1` is refused as an analysis model and is used only by the segmentation stage. Temperature is rejected if set, rather than dropped. Reasoning effort is sent only when the catalog lists it. Small videos can be data URLs; larger ones are uploaded with the files API. If that upload purpose is rejected, the error is shown and the modality is not changed.

SAM responses are parsed from the documented special-token grammar (frame index, box, mask payload). A SAM track is linked to a LearnLoop object only when box overlap supports it. Otherwise the track stays unassigned and `identity_uncertain`. Raster decoding uses `meta-sam-parser` when installed and otherwise stores the payload. Live SAM calls were not run here.

This integration is optional. The fixture and deterministic providers, and the other vendors, do not depend on it.

## OpenAI-compatible

Same frame client as OpenAI, with native video forced off and the base URL taken from the provider page or `OPENAI_COMPATIBLE_BASE_URL`.

## Credentials

Environment variables: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`, `MODEL_API_KEY` or `META_MODEL_API_KEY`, `OPENAI_COMPATIBLE_API_KEY`. The Providers page can store a key in the encrypted local table instead. Responses, logs, and exports contain a hint at most (last four characters), never the key.

`LEARNLOOP_LIVE_PROVIDERS=1` is the switch for future opt-in tests. The default suite does not call a paid API.

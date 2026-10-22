# Audio analysis

Local transcription is optional. A run with both audio switches off does not load Whisper, does not read the audio track, and follows the same visual stages as before. A recording with no audio track still completes visual analysis.

## Switches

On the experiment form, under Audio analysis:

- **Enable local transcription** is off by default. When it is on, the `transcription` stage probes the file with ffprobe and, only if an audio stream exists, runs faster-whisper on that file. The transcript is stored with clip-relative start and end times.
- **Use transcript for workflow analysis** stays disabled until transcription is on. It runs a local comparison of the transcript with the visual record for that same clip. Spoken claims do not change action outcomes and do not satisfy a step's completion condition.

Fixture, deterministic, and manual JSON providers keep the transcript on this machine. Any other selected provider can receive **transcript text** plus a few sampled frames for the comparison. The audio file is not uploaded. The form says so before the run starts, and the stage checkpoint records whether text was sent.

## Models

Install the engine once:

```bash
backend/.venv/bin/pip install -e "./backend[audio]"
```

Supported sizes are tiny, base, small, medium, and large-v3. The default is base. Weights download into the Hugging Face cache on first use of that size (`~/.cache/huggingface/hub`, Systran faster-whisper repos). They are not stored in the repository. The audio tab lists which sizes are already downloaded and whether CUDA was detected. This machine uses CPU int8 when CUDA is absent. Apple Silicon is not treated as a CUDA device.

Language defaults to automatic detection. A language code overrides it. Silence filtering is on by default and is retried without the filter if voice activity detection fails. Word timestamps are off by default. The alignment window defaults to 4 seconds. Speech may fall before, during, or after the visible action inside that window. A match outside the window stays uncertain rather than snapping to the nearest frame.

Cancel is checked before the model loads, before decoding starts, and between segments. The blocking decode call itself is not interrupted mid-forward.

Transcripts are cached under `data/transcripts/{checksum}/` by model, language, silence filter, and word timestamps. A retry reuses that file. A corrected segment keeps its edited text; a later transcription updates `text_model` and leaves the edited text in place.

## What speech is allowed to mean

Each segment is classified as visually supported, contextual, contradictory, irrelevant, or uncertain. The local classifier is deterministic and marks every link `INFERRED`. A numeric claim is contradictory only when the named thing appears in this clip's visual record and the count differs. The explanation says the camera may have missed the action. Irrelevant speech stays in the raw transcript and is not linked to a step. Occluded or weakly matched speech stays uncertain.

Each clip is transcribed on its own. A statement is compared only with events from that clip, so the same relative timestamp in another recording is not a match. Gaps between recordings stay gaps.

The transcript is untrusted task data. The comparison prompt tells the model to ignore instructions inside the speech.

## Inspecting a run

Open the run's Audio tab.

- The timeline lists segments. Choosing a time seeks the inspector.
- Filter by classification.
- Save a text correction or a classification correction. The original model text and the local classification remain stored.
- Frame times on an alignment seek the inspector. Linked action, object, and step ids are the visual record for that clip.
- Start a visual-only copy to rerun the same settings with both audio switches off. Compare that run with the transcript-assisted run. The report lists document differences, latency, and cost. It does not invent an accuracy score. Ground-truth metrics appear only when the visual run has a human ground-truth dataset.

Export adds `transcripts.json` and `alignments.json` beside the analysis document. The analysis document schema stays 1.0.0. Transcript rows are not visual observations.

## What was verified

Backend tests cover supported narration, a reason the pictures do not establish, unrelated speech, a count that disagrees with the visual record, speech before and after the action, speech during occlusion, an empty transcript, a file with no audio track, a second clip that must not inherit the first clip's times, and a run with audio switched off. Those tests use a stand-in for Whisper so they do not download weights. The visual action outcomes in the fixture stay the authored outcomes.

`faster-whisper` 1.2.1 imports in this environment. No Whisper weight file was downloaded, and no recording was transcribed with the live model. A live call that sends transcript text to a paid model was not run. If the package is missing on another machine, the transcription stage records the install error and the visual run still completes.

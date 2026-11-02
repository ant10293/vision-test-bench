# Targeted object recovery and SAM prompt strategies

Recovery repairs missing or unreliable boxes in a finished analysis with SAM 3.1 alone. It never calls Muse Wonder or any language model, never resets a stage, and never adds a job. It works on the run's **Recovery** tab and under `/api/runs/{id}/recovery` and `/api/recovery`.

## Flow

1. **Find targets.** `perception/recovery_targets.py` reads stored rows, tracks, ambiguities, and box decisions. A recorded absence or full occlusion is listed but not suggested. A gap with no explanation stays `unresolved`. Absence is never asserted.
2. **Choose seeds.** `perception/seeds.py` classifies every stored box. Each seed keeps its checks and explanation.
   - `human_verified`: a person drew it or confirmed it.
   - `independently_supported`: a measured producer agrees with a different producer on the same frame (IoU 0.5). Tracker, concept-SAM, and VLM count as different producers, and at least one of the two must be measured.
   - `candidate`: one producer only. It needs a person's confirmation.
   - `rejected`: failed a check such as occluded, shared with another object, or removed by a pipeline step.

   Model confidence is not used. A track grown from a location prompt can never vouch for a box, so SAM cannot trust itself. Among usable seeds, the nearest one wins, and trust only breaks ties within a one-second band.
3. **Plan.** A dry run computes clips, prompts, directions, billed frames, and limit violations, and contacts nothing.
4. **Replay or run live.** See the modes below.
5. **Validate.** `perception/recovery_validate.py` gives each returned box an outcome, listed below.
6. **Review.** Compare layers on the frame, then accept or reject each proposal.

| Outcome | Meaning |
| --- | --- |
| `supported` | A measurement agrees, two passes agree, or the row is within 2 s of a trusted seed with no flags. |
| `provisional` | Plausible but unconfirmed. Never canonical. |
| `conflicting` | Disagrees with a measurement or the other pass, jumped, shares a box with another object, or grew over another part. |
| `rejected` | Unassociated track, wrong source size, or malformed mask. |
| `unobserved` | SAM returned nothing. This is a gap, not an absence, and no box is added. |

A SAM score never raises or lowers an outcome.

## Using the tab

1. Step to a frame with the slider, the arrows, or an object's lane. Amber on a lane marks a suspected problem.
2. Click a correct box to make it a seed, or choose an object and drag to draw one. Add as many as you need, across objects and frames. Clicking a box counts as a person confirming it.
3. Set how far new seeds track. **Check seeds** is free and shows the request size.
4. **Run SAM** sends the checked plan. Seeds whose clips overlap and that share a direction go out in one request when that bills no more than separate ones.
5. Results appear on the lanes and the frame. **Accept as a new revision** applies every pending result at once.

The **X** on a suspected problem dismisses it for this run, and **Show dismissed** restores it. Dismissing changes no boxes and does not make a box trustworthy. A dismissal matches the object and exact time range, so a changed range is flagged again. The detector's reasons are stored with it, so repeated false alarms can be counted later.

Changing a seed or limit requires checking again. Limits and earlier runs are in the collapsed panel.

## Frame and coordinate mapping

- `MediaMap.frame_times[k]` is the source presentation time of frame `k` in the submitted file. Times come from the file's presentation timestamps, never `timestamp * fps`. A video whose times are only estimated is refused.
- A seed must land on one frame: within half a frame and 0.02 s, or 1.5 frames for a box a person placed on a paused video. Otherwise the seed fails with a reason.
- Boxes are normalized to the displayed frame. `box_mark` converts them to inclusive pixel corners, and `render_spatial_prompt` serializes every token.
- `prepare_clip` re-encodes the interval upright at the displayed size, with no resize or crop. It then checks the frame count, every frame's spacing, the displayed size, and the first frame's picture against the source. Any mismatch stops the request before upload.
- SAM's frame markers map back through `frame_times`, so response order does not matter and a marker past the clip is dropped.

## Execution modes

| Mode | Remote calls | What it does |
| --- | --- | --- |
| Dry run | None | Builds the plan and estimate. |
| Replay | None | Re-parses stored responses and re-runs mapping, association, validation, and comparison. A missing response stays missing. |
| Live | SAM only | Sends the plan after `confirm: true` and the matching `plan_hash`. |

Stored responses live in `data/runs/{run}/recovery/responses/{key}.json`. The key covers the source checksum, frame times, media size, clip encoding version, prompt, direction, model, and request parameters. A changed input cannot reuse a response.

Live limits default to 2 requests, 300 frames per request, 600 billed frames, $1.00, and a 300 s timeout. They are checked before every call. A request that reached the provider and did not finish is marked `submitted`, because it may be billed. It is never resent unless you approve that key. Each error says whether anything was submitted. A restart marks a running recovery `interrupted`, and it does not resume.

### Cost

Meta documents SAM video as billed per frame with a 50-frame minimum per request. Tracking runs from the earliest prompt frame. This repository has no published rate, so cost stays unknown until you set `LEARNLOOP_SAM_PRICE_PER_FRAME_USD`. Until then the cost budget cannot be checked, and only the request and frame limits apply. Recorded costs are planned frames at that rate. The invoice is authoritative.

Existing pipeline SAM calls are token-priced in code and have no token rate, so they record $0. `cost_ceiling_usd` therefore never stops SAM spending. This was left as it is. Do not read a run's `cost_usd` as including SAM.

## Accepting

Accepting goes through the existing `revise()` path and creates a new `human` revision. Earlier revisions stay byte-identical. Actions, states, relationships, and workflow are not touched. `merge_proposal` adds rows beside existing evidence, and `resolve_document` decides the display set:

- A row agreeing with an existing canonical box on the same frame is dropped.
- A row disagreeing with one is stored as a raw conflict, and the canonical box stays.
- Provisional rows need an explicit choice and never become canonical.
- Remaining gaps become `unobserved_interval` ambiguities.
- Applying the same rows twice adds nothing.

Accepting is refused if the active revision changed since the experiment was created. Rejected proposals and earlier replays stay stored.

## Normal pipeline strategies

`sam_prompt_strategy` is set in the experiment form when SAM video tracking is selected. A run without the setting uses `concept`.

- `concept`: existing behavior.
- `trusted_spatial`: follows only usable seeds on each chunk. Other objects stay unobserved.
- `hybrid`: verified boxes first, then a separate noun request for objects without a seed.

Seeds are computed from evidence available when segmentation starts, and earlier SAM rows are excluded. The segmentation stage checkpoint records `prompt_strategy`, `prompt_kinds_sent`, and each object's outcome.

## The pre-segmentation box question

Evidence from stored runs of one recording (`data/devlab`, read only, no API calls):

- `segmentation_mode` is read only by the segmentation stage. The experiment form changes nothing else when SAM is selected.
- The configs of the compared `sam_video` and `boxes_only` runs differ only in `segmentation_mode`, `segmentation_model_id`, and `git_revision`.
- Tracker output (`spatial.json`) has the same geometry hash across `sam_video` and `boxes_only` runs with the same perception settings.
- `semantic_raw.json` differs on every run, including repeated runs with identical settings. Across the 14 recent Muse Wonder runs with stored output, object counts ranged from 6 to 7, and box counts ranged from 112 to 158 apart from one run with 6. Object ids also changed which part they named.

Conclusion: the boxes that coincided with SAM are best explained by Muse Wonder's run-to-run variation, not by SAM. This rests on only one completed and one canceled `sam_video` run under the current code, so it is strong but not proven.

Two related fixes and aids came out of this:

- A stale SAM summary could reach `action_analysis` and `state_tracking` on a retry, because both run before segmentation. It no longer does.
- The `parsed` revision is rewritten in place during a run, so no stage snapshots existed. `pipeline/lineage.py` now appends a geometry fingerprint per stage to `geometry_lineage.jsonl`, readable at `GET /api/runs/{id}/geometry-lineage`. `test_selecting_sam_does_not_change_geometry_before_segmentation` enforces the invariant with mocked upstream answers.

## Known limits

- No real SAM spatial response has been tested. Box and point prompting follows Meta's documented grammar and has not been run against the live API. Accuracy is unmeasured.
- Validation is geometric. Background drift is only caught when it causes a jump, a scale step, or a disagreement. Masks are checked by header and not decoded.
- Recovered masks stay in the stored response and are not materialized as overlays. `mask_ref` points to the response, frame, and track.
- `supported` near a seed means continuity from a trusted seed, not independent proof. The 2 s horizon and other thresholds are engineering defaults in `ValidationPolicy`.
- A normal-pipeline seed only prompts its own chunk. SAM ids do not cross chunks, so `trusted_spatial` leaves later chunks unobserved for that object.
- `MIN_BILLED_FRAMES` and the tracked-frame count are estimates from Meta's documentation.
- Re-running the pipeline writes from `working.json`, which does not contain accepted recoveries.
- The Alembic history already had two heads before this work. Use `alembic upgrade heads`. The app itself creates tables at startup.

## Track loss and reacquisition

Returning a box does not mean SAM is still on the right object. Validation walks outward from each seed through explicit states and keeps two identities apart:

- **Physical instance:** the real object. It keeps its identity while hidden. No box is invented for it.
- **Visual segment:** one continuous run of localizations from one SAM request. SAM ids exist only inside a request, so a segment never names an object by its id.

Row and segment states are `tracked`, `degraded`, `lost`, `reacquisition_candidate`, `reacquired`, and `ambiguous`. A failure also records `lost`, `occluded`, or `out_of_view`. Those last two are hypotheses from geometry and never findings.

A segment ends on a sustained jump measured in time-normalized motion, growth over a known neighbor, growth with a poor mask, repeated contradiction by measurements, a pass that does not start on its seed, a jump across a hard cut, or SAM no longer returning the object. An anomaly with a return beside the last good box within `outlier_frames` is a single outlier and does not end the segment. A SAM score is reported and never changes an outcome.

Boxes after a loss stay on the proposal, in no segment. Accepting keeps them as visible conflicts that never become canonical. A later segment starts only from an initialization event: a new pass from a trusted seed, or a measurement that agrees. It inherits neither the old failure nor the old support. A segment after a hard cut stays unverified until a measurement agrees.

A segment is linked to the physical object only by a confirmed seed, an independently supported seed, or agreeing measurements. A competing object leaves the link ambiguous. Agreement between passes lifts a row only when they began from separate seeds.

### Reacquisition plans

`GET /api/runs/{run}/recovery/reacquisition` returns segments, failures, unresolved spans, statistics, and a plan for each failure. It contacts no provider. Strategies are tried in this order: an independent observation, a human seed, a stored candidate a person confirms, a concept pass, then a manually drawn seed. A prompted SAM track is never offered as a seed, and a seed from a failed attempt is not offered again. Nothing starts automatically.

Each new pass is its own experiment with its own dry run, limits, and explicit approval. A concept pass binds no object, so its boxes are candidates a person has to identify.

### Reprocessing without spending credits

Open a run on the Recovery page and choose **Replay this run's stored responses (free)**. This repeats parsing, validation, segmentation, and planning from stored responses, and creates a new generation of proposals. Responses and cache keys do not change. To try other thresholds:

```bash
curl -X POST localhost:8000/api/recovery/experiments/EXPERIMENT_ID/replay \
  -H 'content-type: application/json' -d '{"validation": {"jump_factor": 8}}'
```

### One small approved experiment

1. Replay the run, open **Track history**, and go to a frame listed under **Frames to check** where the object is visible again.
2. Choose **Draw a new seed**, then drag a box around the object.
3. Keep the frame limit low under **Limits**, then choose **Check 1 seed**. This is free and shows the billed frames.
4. Read the plan, then choose **Run SAM**. Only this step can be billed. SAM bills at least 50 frames per request.
5. Review the new segment in **Track history**, then accept or reject it. Accepting creates a new revision.

## Finding a lost object again

Tracking and detection are separate problems. After a loss, the Track history panel runs this order. Each step uses stored data until a person approves a request:

1. Search everything stored past the loss: observations, concept passes, unprompted SAM tracks, and tracks the pipeline could not assign. This is free.
2. Judge each candidate as `verified_reappearance`, `probable_reappearance`, `ambiguous_candidate`, or `rejected_candidate`. An empty search reports `no_detection`.
3. If nothing was found, plan one bounded concept search. It has a noun phrase, never a location token, and it needs its own approval.
4. From a verified or confirmed box, plan a new spatial request with a new SAM id. It is a separate segment of the same physical object, and no box is made up for the gap. Its results go through the usual validation, proposal, and acceptance.

**What verifies a candidate:** a person, a separate producer's measurement on the same frame, or a part that is the only one with its label and the only detection on that frame, with a matching appearance from DINOv2 weights already on this machine and no hard cut since the loss. Everything else stops for a person. A nearby box, a stable box, a SAM score, or a color histogram never verifies. A different appearance alone does not reject a box, because a turned part can look different.

**Limits:** one concept search and two reinitialization attempts per loss, a 5 second search window, and a 6 second clip. Going past those needs an explicit new approval (`approve_new_attempt` or `expand_search`).

**Multi-frame prompts:** a target with `seed_ids`, or a pipeline run with `sam_multi_seed`, can prompt one object on several frames under one SAM id. Only trusted seeds count, and a seed must span a gap of 3 seconds or show a new view. Tracking starts from the earliest prompt, so a later prompt cannot start a backward pass. That needs its own request. Sending two prompts does not guarantee that SAM bridges a disappearance, so it is an option alongside starting a new track.

The pipeline can use multi-frame prompts, but it does not search again after a loss by itself. That search belongs to Targeted Recovery, so a paid call always has its own approval.

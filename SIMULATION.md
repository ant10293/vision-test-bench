# Scene simulation

A simulation dataset is one kinematic scene evaluated at every frame. Blender places those matrices and renders RGB and an instance-id image from the same meshes. Boxes, visibility, and occlusion come from that id image. Assembly events and constraint results come from the Python evaluator. They are not guessed from the pixels afterwards.

This is not a rigid-body simulation. `rigid_body` is false on every scene and every evaluated state.

## Coordinates

Length is meters. The world is Blender's Z-up, right-handed frame. An object matrix is row-major and maps the object's local frame into that world. The camera looks along its local −Z, with local +X to the right and local +Y up. The camera-to-world matrix uses those axes as its columns.

Image origin is the top-left corner. Pixel `(i, j)` covers `[i, i+1) × [j, j+1)`. Normalized boxes are top-left `(x, y, w, h)` in `0..1`. Intrinsics use a centered principal point and `fy = fx`.

Depth is not exported. Eevee can produce a depth pass, and an unchecked depth image would be fake ground truth, so frames record `depth: null` and the manifest says `depth_convention: not_exported`. Camera-space optical-axis depth, if added later, is −Z in the camera frame and is not Euclidean distance.

The Scene Lab viewer converts Blender `(x, y, z)` to three.js `(x, z, −y)` for display. That conversion is not a second scene and is not written back as a pose.

## Scene

`backend/app/simulation/schema.py` is schema version 1. Objects have stable instance ids. Pieces of one object share that id. Constraints are `axial_insertion` or `coaxial_seat`, or `unresolved` when the geometry is not checked. Overlapping boxes are not a relationship. An omitted object is physically absent.

`evaluate_scene(scene, timestamp)` returns object matrices, the camera matrix, which constraints hold, the active step, and the active operations. An operation ending at a pose does not by itself mean the assembly succeeded. The constraint does.

The clock is `frame_count = max(2, round(duration * fps))` and the last frame is at `(frame_count - 1) / fps`. Authored motion is written on a 6 second timeline and scaled onto that last-frame time. The final pose is held through the last frame.

## Assembly preset

`build_assembly` in `presets.py` builds a plate, bolt, washer, and nut. Variants:

| Variant | Physical result |
| --- | --- |
| `correct` | Bolt, washer, and nut seated |
| `missing_washer` | Washer omitted. Bolt and nut can still seat |
| `occluded_washer` | Same final assembly as correct, with a cover in front of the washer |
| `incorrect_placement` | Washer present and not seated |
| `corrected_mistake` | Washer is misplaced, then seated. The miss stays in the event list |
| `order_violation` | Nut seats before the washer. Both can be seated at the end |
| `identity_swap` | Two washers share a color and a label and keep different ids |
| `harmless_reorder` | An unrelated block moves. The assembly does not |

Camera modes are `fixed`, `constrained_head`, and `unconstrained_head`. A matched pair uses the same seed, camera, lighting, and placement. Only the named intervention changes. The same seed reproduces the scene.

Colors, sizes, and start positions can be jittered with `randomize=True`. Checked end poses and constraint angles are not jittered into a different mechanical result.

## Rendering

`render_dataset` runs `blender --background --python` with a job JSON. The subprocess is not a shell. Stdout goes to `logs/blender.log`. Cancel writes `logs/cancel` and signals the process group. The timeout is `LEARNLOOP_BLENDER_BIN` / `settings.simulation_timeout_sec` (default 900 seconds). The executable is `settings.blender_bin`, then `BLENDER_BIN`, then `PATH`, then the macOS app bundle.

The installed engine used for development is Blender 5.2 Eevee (`BLENDER_EEVEE`). The script asks the file which engines exist. It does not assume Cycles or an object-index compositor socket. Blender 5.2's compositor does not expose `IndexOB`. The id pass is an unlit emission material, view transform Raw, filter size 0, and one sample, with lights hidden. Decode accepts a Chebyshev distance of 4 because edge pixels are off by about 1. Id colors are 32 apart, so that tolerance cannot swap ids. Background black is not an id.

The PNG sequence is what ffmpeg encodes to `videos/execution.mp4`.

Preview quality is 1 sample. Standard is 8. The API accepts 160–1280 by 120–720, 2–24 fps, and 1–12 seconds. The Scene Lab defaults to 320×240, 8 fps, 6 seconds, preview.

## Dataset

Files live under `data/simulations/{id}/`:

```text
scene.json
manifest.json
videos/execution.mp4
frames/rgb/000000.png
frames/instance/000000.png
annotations/frames.json
annotations/objects.json
annotations/events.json
annotations/relationships.json
annotations/workflow.json
annotations/observability.json
logs/
```

Manifest paths are relative to that directory. `validate_dataset` rejects absolute paths, missing frames, a timestamp that is not the scene clock, a non-finite transform, an unknown id, a mask whose size differs from the RGB frame, and an event outside the recording. A failed render is stored as failed. It is not published as a completed dataset.

Visibility is `fully_visible`, `partially_occluded`, `fully_occluded`, `outside_fov`, or `absent`. Absent means the object was omitted from the scene. Zero pixels behind another object is `fully_occluded`. Zero pixels whose projected box misses the image is `outside_fov`. The visible fraction is mask pixels over the clipped projected box, not an exact surface area.

`justified_verdict` is what the picture supports: `PASS`, `FAIL`, `CANNOT_VERIFY`, or `NOT_APPLICABLE`. A missing washer in view can justify `FAIL`. The same absence behind a cover is `CANNOT_VERIFY`. That verdict is ground truth about the evidence. It is not a model score.

## Leakage

`POST /api/simulations/{id}/analyze` builds a normal run from the ingested video. The payload is the video id, research mode, provider, model, sampling, and perception preset. It rejects `provider_id=fixture`. Perception, prompts, and providers do not import `app.simulation`. Scoring reads the dataset from disk after the run exists and refuses a config that contains `ground_truth`, `scene.json`, `simulator`, or `inserted_into`.

Camera matrices in the dataset are simulator truth. The analysis document still has `world_coordinates_supported: false`. Partial affine compensation is not scored as a 6-DoF pose. If a prediction has no boxes, no instance ids, or no masks, that metric is `NOT_EVALUATED` rather than zero.

## Real footage

`perception/scene_graph.py` is the image-space scene for an ordinary run. It keeps a track across a long gap, marks that interval unobserved, and does not call the object removed. Pose stays null. `metric_scale_known` and `world_coordinates_supported` stay false. A link between segments is unresolved when appearance evidence is missing or the margin is under 0.2. A shared label does not merge identities.

There is no visual SLAM in this repository. A gap, a viewpoint change, or a lost affine estimate does not become a metric camera pose.

## Jobs and API

Simulation jobs are `SimulationJob` rows, not analysis jobs. Alembic revision `c5e1a9b44d18`. The worker claims an analysis job first, then a simulation job, and requeues a running simulation job on startup. Routes are under `/api/simulations`. Scene Lab is `/scene-lab`.

An experiment always analyzes the video chosen on the form. Blender scene runs after scene analysis and is the only place that render's frame count is shown. Skip Blender scene creation to leave that stage skipped. The stage places a shape for each object in that analysis, using that object's boxes, and puts the object's first crop on the face toward the camera. It stores the render as a dataset. Motion tracks are not objects. Depth is a fixed image plane, not a measured pose. It does not replace the selected video, and the run config stores only whether the stage was skipped.

Deleting a dataset removes `data/simulations/{id}` and leaves the ingested video in the library.

## Without the UI

```bash
backend/.venv/bin/python - <<'PY'
from pathlib import Path
from app.simulation.presets import build_assembly
from app.simulation.render import render_dataset

scene = build_assembly("missing_washer", "fixed", seed=1, width=320, height=240, fps=8, duration=4)
render_dataset(scene, Path("data/simulations/manual-missing"), dataset_id="manual-missing", samples=1)
PY
```

Or `POST /api/simulations` and let the worker call `execute_simulation`.

## What is not implemented

- Metric depth export.
- Cryptomatte and object-index passes. They were checked on Blender 5.2 and not used.
- Image decals and glTF/OBJ catalog import.
- Rigid-body contact.
- A field recording of a person moving between workspaces. The unconstrained test uses the lab's synthetic fixture through `analyze_sequence`, plus a Blender camera that moves. That is not a claim that unconstrained head-mounted footage is solved.
- Mask IoU. Predictions do not currently carry instance masks, so mask overlap stays `NOT_EVALUATED`.

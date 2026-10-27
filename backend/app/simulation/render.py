"""Run Blender on an evaluated scene and publish the dataset files."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from app.config import settings
from app.simulation.dataset import (
    DatasetError,
    validate_dataset,
    write_manifest,
    write_observability,
    write_scene_files,
)
from app.simulation.evaluate import evaluate_scene
from app.simulation.schema import Scene

SCRIPT = Path(__file__).resolve().parent / "blender" / "render_scene.py"
MAC_BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")


class BlenderMissing(FileNotFoundError):
    pass


class RenderCancelled(RuntimeError):
    pass


class RenderFailed(RuntimeError):
    pass


def find_blender() -> str | None:
    configured = (settings.blender_bin or os.environ.get("BLENDER_BIN") or "").strip()
    candidates = [configured] if configured else []
    candidates.append("blender")
    if MAC_BLENDER.exists():
        candidates.append(str(MAC_BLENDER))
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return str(path)
        found = _which(candidate)
        if found:
            return found
    return None


def _which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def require_blender() -> str:
    found = find_blender()
    if found:
        return found
    raise BlenderMissing(
        "Blender was not found. Install it and put it on PATH, or set LEARNLOOP_BLENDER_BIN "
        "or BLENDER_BIN to the blender executable."
    )


def build_job(
    scene: Scene,
    root: Path,
    samples: int,
    colors: dict[str, list[int]],
    textures: dict[str, Path] | None = None,
) -> dict:
    frames = []
    for index, timestamp in enumerate(scene.sample_times()):
        state = evaluate_scene(scene, timestamp)
        frames.append(
            {
                "index": index,
                "timestamp": timestamp,
                "camera_matrix": state["camera"]["matrix"],
                "fov_x_deg": scene.camera.fov_x_deg,
                "intrinsics": state["camera"]["intrinsics"],
                "objects": [
                    {
                        "instance_id": item["instance_id"],
                        "matrix": item["matrix"],
                        "present": item["present"],
                    }
                    for item in state["objects"]
                ],
            }
        )
    return {
        "output_dir": str(root),
        "width": scene.width,
        "height": scene.height,
        "fps": scene.fps,
        "samples": samples,
        "background": list(scene.environment.background),
        "lights": [light.model_dump() for light in scene.lights],
        "instances": [
            {
                "instance_id": obj.instance_id,
                "color": list(obj.material.color),
                "roughness": obj.material.roughness,
                "metallic": obj.material.metallic,
                "id_color": colors[obj.instance_id],
                "pieces": [piece.model_dump() for piece in obj.pieces],
                **(
                    {"texture": str(textures[obj.instance_id])}
                    if textures and textures.get(obj.instance_id)
                    else {}
                ),
            }
            for obj in scene.objects
        ],
        "frames": frames,
        "progress_path": str(root / "logs" / "progress.json"),
        "cancel_path": str(root / "logs" / "cancel"),
    }


def render_dataset(
    scene: Scene,
    root: Path,
    dataset_id: str,
    samples: int = 1,
    timeout_sec: float = 900,
    on_progress=None,
    should_cancel=None,
    textures: dict[str, Path] | None = None,
) -> dict:
    """Evaluate the scene, render those poses, and refuse to publish an invalid dataset."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "videos").mkdir(parents=True, exist_ok=True)
    colors = write_scene_files(scene, root)
    job_path = root / "logs" / "job.json"
    job_path.write_text(json.dumps(build_job(scene, root, samples, colors, textures)))
    binary = require_blender()
    log_path = root / "logs" / "blender.log"
    log_handle = log_path.open("w")
    process = subprocess.Popen(
        [binary, "--background", "--python", str(SCRIPT), "--", str(job_path)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    started = time.monotonic()
    try:
        while process.poll() is None:
            if should_cancel and should_cancel():
                _stop(process)
                raise RenderCancelled("render cancelled")
            if time.monotonic() - started > timeout_sec:
                _stop(process)
                raise RenderFailed(f"Blender exceeded {timeout_sec:.0f}s")
            _report(root, on_progress)
            time.sleep(0.4)
    finally:
        log_handle.close()
    if process.returncode == 2:
        raise RenderCancelled("render cancelled")
    if process.returncode != 0:
        output = log_path.read_text()[-1500:] if log_path.exists() else ""
        raise RenderFailed(output or f"Blender exited {process.returncode}")
    _encode(root / "frames" / "rgb", root / "videos" / "execution.mp4", scene.fps)
    frames_meta = json.loads((root / "annotations" / "frames.json").read_text())
    write_observability(scene, root)
    manifest = write_manifest(
        root,
        scene,
        frames_meta.get("blender") or "",
        frames_meta.get("engine") or "",
        dataset_id,
    )
    validate_dataset(root)
    return manifest


def _report(root: Path, on_progress) -> None:
    path = root / "logs" / "progress.json"
    if on_progress is None or not path.exists():
        return
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return
    on_progress(str(payload.get("stage") or "render"), int(payload.get("frame") or 0), int(payload.get("total") or 0))


def _stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)


def _encode(frame_dir: Path, dest: Path, fps: int) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-start_number",
        "0",
        "-i",
        str(frame_dir / "%06d.png"),
        "-an",
        "-c:v",
        "libx264",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not dest.exists():
        raise DatasetError((completed.stderr or "ffmpeg failed")[-800:])

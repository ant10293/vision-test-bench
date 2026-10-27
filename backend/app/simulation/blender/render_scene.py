"""Headless renderer. Poses in the job are already evaluated. This script places them.

Primitive setup follows the same idea as automated-content-builder
(src/infrastructure/animation/blender/mesh_utils.py): Blender primitives and a
principled material, started with ``blender --background --python``. Narrative
characters, asset catalogs, and shot schemas are not used.

Instance colors are an emission pass of the same meshes at the same matrices.
Boxes come from those pixels. The script does not interpolate a second animation.

  blender --background --python render_scene.py -- /path/job.json
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
import mathutils
import numpy as np


def _job_path() -> Path:
    if "--" not in sys.argv:
        raise SystemExit("pass the job json after --")
    args = sys.argv[sys.argv.index("--") + 1 :]
    if not args:
        raise SystemExit("missing job json path")
    return Path(args[0])


def _engine(scene) -> str:
    available = {item.identifier for item in scene.render.bl_rna.properties["engine"].enum_items}
    for name in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        if name in available:
            scene.render.engine = name
            return name
    raise SystemExit(f"no Eevee engine in {sorted(available)}")


def _look(scene, name: str) -> None:
    try:
        scene.view_settings.look = name
    except TypeError:
        return


def _world(scene, color, strength: float) -> None:
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is None:
        return
    background.inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
    background.inputs["Strength"].default_value = strength


def _material(name: str, color, roughness: float, metallic: float, emission: float, texture: str = ""):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.use_backface_culling = False
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        raise SystemExit("Principled BSDF is missing")
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = roughness
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = metallic
    if texture:
        image = bpy.data.images.load(texture, check_existing=True)
        try:
            image.colorspace_settings.name = "sRGB"
        except TypeError:
            pass
        try:
            image.alpha_mode = "STRAIGHT"
        except TypeError:
            pass
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        node.interpolation = "Linear"
        # Emit the photograph. Lighting a light crop blows the pale areas out
        # to white and leaves only the dark edges. Alpha drops the table around it.
        glow = material.node_tree.nodes.new("ShaderNodeEmission")
        glow.inputs["Strength"].default_value = 1.0
        clear = material.node_tree.nodes.new("ShaderNodeBsdfTransparent")
        mix = material.node_tree.nodes.new("ShaderNodeMixShader")
        tree = material.node_tree
        tree.links.new(node.outputs["Color"], glow.inputs["Color"])
        tree.links.new(node.outputs["Alpha"], mix.inputs["Fac"])
        tree.links.new(clear.outputs["BSDF"], mix.inputs[1])
        tree.links.new(glow.outputs["Emission"], mix.inputs[2])
        output = tree.nodes.get("Material Output")
        if output is None:
            raise SystemExit("Material Output is missing")
        tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "BLENDED"
        return material
    if emission > 0 and "Emission Color" in bsdf.inputs:
        bsdf.inputs["Emission Color"].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
        bsdf.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    return material


def _project_uv(obj) -> None:
    """Map the camera-facing extent onto the photo. +Z is the top of the image."""
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    layer = mesh.uv_layers.active
    xs = [vertex.co.x for vertex in mesh.vertices]
    zs = [vertex.co.z for vertex in mesh.vertices]
    span_x = max(max(xs) - min(xs), 1e-6)
    span_z = max(max(zs) - min(zs), 1e-6)
    min_x = min(xs)
    min_z = min(zs)
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            co = mesh.vertices[mesh.loops[loop_index].vertex_index].co
            layer.data[loop_index].uv = ((co.x - min_x) / span_x, (co.z - min_z) / span_z)


def _mesh(kind: str, size: list[float], name: str, textured: bool = False):
    if kind == "box":
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
        obj = bpy.context.active_object
        obj.scale = (size[0], size[1], size[2])
        bpy.ops.object.transform_apply(scale=True)
        if textured:
            _project_uv(obj)
        obj.name = name
        return obj
    if kind == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(radius=size[0], depth=size[1], vertices=24, location=(0, 0, 0))
        obj = bpy.context.active_object
    else:
        raise SystemExit(f"unsupported piece {kind}")
    obj.name = name
    bpy.ops.object.transform_apply(scale=True)
    return obj


def _matrix(values: list[float]) -> mathutils.Matrix:
    rows = tuple(tuple(values[index : index + 4]) for index in range(0, 16, 4))
    return mathutils.Matrix(rows)


def _decode(image_path: Path, color_map: dict[str, tuple[int, int, int]]) -> tuple[np.ndarray, dict[str, int]]:
    image = bpy.data.images.load(str(image_path), check_existing=False)
    width, height = image.size
    pixels = np.array(image.pixels[:], dtype=np.float32).reshape(height, width, 4)
    pixels = pixels[::-1, :, :3]
    rgb = np.rint(pixels * 255.0).astype(np.int16)
    labels = np.zeros((height, width), dtype=np.int16)
    counts: dict[str, int] = {}
    ordered = list(color_map.items())
    for slot, (instance_id, color) in enumerate(ordered, start=1):
        target = np.array(color, dtype=np.int16)
        distance = np.max(np.abs(rgb - target), axis=2)
        hit = distance <= 4
        labels[hit] = slot
        counts[instance_id] = int(hit.sum())
    bpy.data.images.remove(image)
    return labels, counts


def _box(labels: np.ndarray, slot: int) -> dict | None:
    ys, xs = np.nonzero(labels == slot)
    if len(xs) == 0:
        return None
    height, width = labels.shape
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return {
        "x": x0 / width,
        "y": y0 / height,
        "w": (x1 - x0 + 1) / width,
        "h": (y1 - y0 + 1) / height,
    }


def _amodal(scene, camera, meshes) -> tuple[dict | None, bool, int]:
    from bpy_extras.object_utils import world_to_camera_view

    points = []
    for mesh in meshes:
        for corner in mesh.bound_box:
            world = mesh.matrix_world @ mathutils.Vector(corner)
            projected = world_to_camera_view(scene, camera, world)
            if projected.z <= 0:
                continue
            points.append((projected.x, 1.0 - projected.y))
    if not points:
        return None, False, 0
    xs = [item[0] for item in points]
    ys = [item[1] for item in points]
    box = {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}
    intersects = box["w"] > 0 and box["h"] > 0 and box["x"] < 1 and box["y"] < 1 and box["x"] + box["w"] > 0 and box["y"] + box["h"] > 0
    clipped_w = min(1.0, box["x"] + box["w"]) - max(0.0, box["x"])
    clipped_h = min(1.0, box["y"] + box["h"]) - max(0.0, box["y"])
    pixels = 0
    if clipped_w > 0 and clipped_h > 0:
        pixels = int(clipped_w * scene.render.resolution_x * clipped_h * scene.render.resolution_y)
    return box, intersects, pixels


def _visibility(present: bool, visible: int, intersects: bool, projected: int) -> tuple[str, float | None]:
    if not present:
        return "absent", 0.0
    if visible <= 0 and not intersects:
        return "outside_fov", 0.0
    if visible <= 0:
        return "fully_occluded", 0.0
    if projected <= 0:
        return "visible", None
    fraction = visible / projected
    if fraction >= 0.85:
        return "fully_visible", fraction
    return "partially_occluded", fraction


def main() -> None:
    job = json.loads(_job_path().read_text())
    output = Path(job["output_dir"])
    rgb_dir = output / "frames" / "rgb"
    id_dir = output / "frames" / "instance"
    rgb_dir.mkdir(parents=True, exist_ok=True)
    id_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    engine = _engine(scene)
    scene.render.resolution_x = int(job["width"])
    scene.render.resolution_y = int(job["height"])
    scene.render.resolution_percentage = 100
    scene.render.fps = int(job["fps"])
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 15
    samples = int(job.get("samples") or 1)
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = samples
        if hasattr(scene.eevee, "use_raytracing"):
            scene.eevee.use_raytracing = False

    roots = {}
    meshes = {}
    beauty = {}
    identity = {}
    for instance in job["instances"]:
        root = bpy.data.objects.new(instance["instance_id"], None)
        scene.collection.objects.link(root)
        roots[instance["instance_id"]] = root
        beauty[instance["instance_id"]] = _material(
            f"{instance['instance_id']}_beauty",
            instance["color"],
            float(instance.get("roughness") or 0.5),
            float(instance.get("metallic") or 0.0),
            0.0,
            texture=str(instance.get("texture") or ""),
        )
        id_color = [channel / 255.0 for channel in instance["id_color"]]
        identity[instance["instance_id"]] = _material(
            f"{instance['instance_id']}_id",
            id_color,
            1.0,
            0.0,
            1.0,
        )
        meshes[instance["instance_id"]] = []
        for piece in instance["pieces"]:
            mesh = _mesh(
                piece["kind"],
                piece["size"],
                f"{instance['instance_id']}_{piece['name']}",
                textured=bool(instance.get("texture")),
            )
            mesh.parent = root
            mesh.matrix_parent_inverse = mathutils.Matrix.Identity(4)
            mesh.location = piece["local_position"]
            rotation = piece.get("local_rotation_deg") or (0.0, 0.0, 0.0)
            mesh.rotation_euler = tuple(math.radians(item) for item in rotation)
            mesh.data.materials.append(beauty[instance["instance_id"]])
            meshes[instance["instance_id"]].append(mesh)

    for light in job.get("lights") or []:
        data = bpy.data.lights.new(light["id"], "AREA")
        data.energy = float(light["energy"])
        data.color = light["color"]
        data.size = 0.45
        obj = bpy.data.objects.new(light["id"], data)
        scene.collection.objects.link(obj)
        obj.location = light["position"]

    camera_data = bpy.data.cameras.new("Camera")
    camera_data.sensor_fit = "HORIZONTAL"
    camera_data.sensor_width = 36.0
    camera_data.clip_start = 0.01
    camera_data.clip_end = 100.0
    camera = bpy.data.objects.new("Camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    background = job.get("background") or [0.16, 0.17, 0.18]
    color_map = {item["instance_id"]: tuple(item["id_color"]) for item in job["instances"]}
    slots = {item["instance_id"]: index for index, item in enumerate(job["instances"], start=1)}
    records = []
    total = len(job["frames"])
    progress_path = Path(job["progress_path"])
    cancel_path = Path(job["cancel_path"])

    def set_lights(visible: bool) -> None:
        for obj in scene.objects:
            if obj.type == "LIGHT":
                obj.hide_render = not visible

    def show(instance_id: str, material, present: bool) -> None:
        root = roots[instance_id]
        root.hide_render = not present
        for mesh in meshes[instance_id]:
            mesh.hide_render = not present
            if mesh.material_slots:
                mesh.material_slots[0].link = "OBJECT"
                mesh.material_slots[0].material = material
            elif mesh.data.materials:
                mesh.data.materials[0] = material
            else:
                mesh.data.materials.append(material)

    for frame in job["frames"]:
        if cancel_path.exists():
            raise SystemExit(2)
        for item in frame["objects"]:
            root = roots[item["instance_id"]]
            root.matrix_world = _matrix(item["matrix"])
            show(item["instance_id"], beauty[item["instance_id"]], bool(item["present"]))
        camera.matrix_world = _matrix(frame["camera_matrix"])
        camera_data.lens = 36.0 / (2.0 * math.tan(math.radians(float(frame["fov_x_deg"])) / 2.0))
        set_lights(True)
        bpy.context.view_layer.update()
        _world(scene, background, 1.0)
        scene.view_settings.view_transform = "Standard"
        _look(scene, "None")
        scene.render.filter_size = 1.5
        if hasattr(scene, "eevee"):
            scene.eevee.taa_render_samples = samples
        rgb_path = rgb_dir / f"{frame['index']:06d}.png"
        scene.render.filepath = str(rgb_path)
        bpy.ops.render.render(write_still=True)

        for item in frame["objects"]:
            show(item["instance_id"], identity[item["instance_id"]], bool(item["present"]))
        set_lights(False)
        bpy.context.view_layer.update()
        _world(scene, [0.0, 0.0, 0.0], 0.0)
        scene.view_settings.view_transform = "Raw"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        scene.render.filter_size = 0.0
        if hasattr(scene, "eevee"):
            scene.eevee.taa_render_samples = 1
        id_path = id_dir / f"{frame['index']:06d}.png"
        scene.render.filepath = str(id_path)
        bpy.ops.render.render(write_still=True)
        labels, counts = _decode(id_path, color_map)
        instances = []
        for item in frame["objects"]:
            present = bool(item["present"])
            slot = slots[item["instance_id"]]
            visible = counts.get(item["instance_id"], 0) if present else 0
            box, intersects, projected = (None, False, 0)
            if present:
                box, intersects, projected = _amodal(scene, camera, meshes[item["instance_id"]])
            status, fraction = _visibility(present, visible, intersects, projected)
            instances.append(
                {
                    "instance_id": item["instance_id"],
                    "present": present,
                    "matrix": item["matrix"],
                    "visibility": status,
                    "visible_pixels": visible,
                    "projected_pixels": projected,
                    "visibility_fraction": fraction,
                    "bbox": _box(labels, slot) if present else None,
                    "amodal_bbox": box,
                    "amodal_intersects": intersects,
                }
            )
        records.append(
            {
                "index": frame["index"],
                "timestamp": frame["timestamp"],
                "camera_matrix": frame["camera_matrix"],
                "fov_x_deg": frame["fov_x_deg"],
                "intrinsics": frame["intrinsics"],
                "instances": instances,
                "depth": None,
                "depth_status": "unavailable",
                "depth_reason": (
                    "Eevee exposes a Depth pass. It is not written: a depth image that has not "
                    "been checked against optical-axis meters would be fake ground truth."
                ),
            }
        )
        progress_path.write_text(json.dumps({"stage": "render", "frame": frame["index"] + 1, "total": total, "engine": engine}))

    (output / "annotations").mkdir(parents=True, exist_ok=True)
    (output / "annotations" / "frames.json").write_text(json.dumps({"frames": records, "engine": engine, "blender": bpy.app.version_string}))
    print(f"rendered {len(records)} frames with {engine}")


if __name__ == "__main__":
    main()

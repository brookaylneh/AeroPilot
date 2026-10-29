"""Render unmodified GLB geometry from six cameras in Blender for visual review."""

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sites = list((ROOT / ".venv-cosys/lib").glob("python*/site-packages"))
    assert len(sites) == 1
    sys.path.extend([str(ROOT), str(sites[0])])
    from mission_planner.environment import load_env

    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--longest-extent-m", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.model = ROOT / args.model
    args.output = ROOT / args.output
    assert digest(args.model) == args.sha256
    assert args.longest_extent_m > 0
    assert args.output.resolve().is_relative_to((ROOT / ".local/evidence/world").resolve())
    args.output.mkdir(parents=True)
    started = time.monotonic()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    assert bpy.ops.import_scene.gltf(filepath=str(args.model)) == {"FINISHED"}
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    objects = [obj for obj in scene.objects if obj.type == "MESH"]
    assert objects
    corners = [obj.matrix_world @ Vector(c) for obj in objects for c in obj.bound_box]
    low = Vector([min(c[i] for c in corners) for i in range(3)])
    high = Vector([max(c[i] for c in corners) for i in range(3)])
    dimensions = high - low
    assert min(dimensions) > 0
    scale = args.longest_extent_m / max(dimensions)
    offset = Vector((-(low.x + high.x) / 2, -(low.y + high.y) / 2, -low.z))
    transform = Matrix.Diagonal((scale, scale, scale, 1)) @ Matrix.Translation(offset)
    matrices = [(obj, obj.matrix_world.copy()) for obj in objects]
    for obj, matrix in matrices:
        obj.parent = None
        obj.matrix_world = transform @ matrix
    bpy.context.view_layer.update()
    textures = [
        {"name": image.name, "size": list(image.size), "colorSpace": image.colorspace_settings.name}
        for image in bpy.data.images
        if image.source != "VIEWER"
    ]
    triangles = 0
    for obj in objects:
        obj.data.calc_loop_triangles()
        triangles += len(obj.data.loop_triangles)
    ground = bpy.data.materials.new("Neutral review ground")
    ground.use_nodes = True
    shader = ground.node_tree.nodes["Principled BSDF"]
    shader.inputs["Base Color"].default_value = (0.24, 0.25, 0.24, 1)
    shader.inputs["Roughness"].default_value = 0.95
    bpy.ops.mesh.primitive_plane_add(size=args.longest_extent_m * 200, location=(0, 0, -0.01))
    bpy.context.object.data.materials.append(ground)
    scene.unit_settings.system = "METRIC"
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 48
    scene.cycles.use_denoising = True
    scene.cycles.seed = 731
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 2
    scene.render.resolution_x = 1440
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Filmic"
    scene.view_settings.look = "Medium High Contrast"
    scene.world = bpy.data.worlds.new("Review daylight")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (0.55, 0.67, 0.8, 1)
    background.inputs["Strength"].default_value = 0.7
    bpy.ops.object.light_add(type="SUN", location=(15, -15, 25))
    sun = bpy.context.object
    sun.rotation_euler = (math.radians(30), math.radians(-20), math.radians(-30))
    sun.data.energy = 3
    sun.data.angle = math.radians(10)
    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.data.type = "PERSP"
    camera.data.sensor_fit = "HORIZONTAL"
    camera.data.angle = math.radians(50)
    camera.data.clip_end = args.longest_extent_m * 100
    scene.camera = camera
    target = Vector((0, 0, dimensions.z * scale * 0.48))
    distance = args.longest_extent_m * 2.15
    views = [
        ("orbit-045", Vector((1, 1, 0.5))),
        ("orbit-135", Vector((-1, 1, 0.5))),
        ("orbit-225", Vector((-1, -1, 0.5))),
        ("orbit-315", Vector((1, -1, 0.5))),
        ("side-positive-y", Vector((0, 1, 0.12))),
        ("overhead", Vector((0, 0, 1))),
    ]
    report = {
        "model": str(args.model),
        "modelSha256": args.sha256,
        "scriptSha256": digest(Path(__file__)),
        "renderer": "Blender Cycles CPU; supplementary preview, not Unreal evaluation",
        "blenderVersion": bpy.app.version_string,
        "blenderBuildHash": bpy.app.build_hash.decode(),
        "samples": scene.cycles.samples,
        "threads": scene.render.threads,
        "sourceBoundsBlenderXYZ": [list(low), list(high)],
        "uniformScale": scale,
        "placedExtentsM": list(dimensions * scale),
        "geometryModified": False,
        "materialOverrides": False,
        "triangles": triangles,
        "textures": textures,
        "lighting": {"sunEnergy": 3, "sunAngleDegrees": 10, "skyStrength": 0.7},
        "renders": [],
    }
    for name, direction in views:
        camera.location = target + direction.normalized() * distance
        camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
        path = args.output / (name + ".png")
        scene.render.filepath = str(path)
        render_started = time.monotonic()
        assert bpy.ops.render.render(write_still=True) == {"FINISHED"}
        frame = {
            "view": name,
            "path": str(path.relative_to(ROOT)),
            "sha256": digest(path),
            "cameraPositionM": list(camera.location),
            "cameraTargetM": list(target),
            "horizontalFovDegrees": math.degrees(camera.data.angle_x),
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "renderSeconds": time.monotonic() - render_started,
        }
        report["renders"].append(frame)
        (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
        print("MESH_PERSPECTIVE " + json.dumps(frame), flush=True)
    report["elapsedSeconds"] = time.monotonic() - started
    (args.output / "renders.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()

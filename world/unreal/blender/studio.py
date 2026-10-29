"""Render an authored .blend through a fitted reference camera under fixed soft studio light.

Run headless: blender -b --factory-startup --python world/unreal/studio.py -- --blend <file>
--camera <camera.json> --output <new dir>. Writes a straight-alpha RGBA PNG whose floor shadow
is held in alpha for compositing over the reference studio colour, and render.json.
"""

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    sites = list((ROOT / ".venv-cosys/lib").glob("python*/site-packages"))
    assert len(sites) == 1
    sys.path.extend([str(ROOT), str(sites[0])])
    from mission_planner.environment import load_env

    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--camera", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=1024)
    parser.add_argument("--exposure", type=float, default=0.0)
    parser.add_argument("--percentage", type=int, default=100)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    output = ROOT / args.output
    assert (
        ".." not in args.output.parts
        and output.is_relative_to(ROOT / ".local")
        and not output.exists()
    )
    assert 16 <= args.samples <= 8192 and 10 <= args.percentage <= 100
    camera_spec = json.loads((ROOT / args.camera).read_text())
    blend = ROOT / args.blend
    started = time.monotonic()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    scene = bpy.context.scene
    preferences = bpy.context.preferences.addons["cycles"].preferences
    preferences.compute_device_type = "OPTIX"
    preferences.refresh_devices()
    devices = [d for d in preferences.devices if d.type == "OPTIX"]
    assert devices, "OptiX device required"
    for device in preferences.devices:
        device.use = device.type == "OPTIX"
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    scene.cycles.samples = args.samples
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.004
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.denoising_use_gpu = True
    scene.cycles.seed = 24
    scene.cycles.max_bounces = 12
    scene.render.film_transparent = True
    width, height = camera_spec["imageSize"]
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = args.percentage
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = args.exposure

    world = bpy.data.worlds.new("Studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    scene.world = world
    bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, 0))
    floor = bpy.context.object
    floor.name = "Studio floor"
    floor.is_shadow_catcher = True
    # Soft key from high front-left of the view; the shadow falls behind and right as in the reference.
    bpy.ops.object.light_add(type="SUN")
    sun = bpy.context.object
    toward = Vector((-0.35, -0.45, 0.82)).normalized()
    sun.rotation_euler = (-toward).to_track_quat("-Z", "Y").to_euler()
    sun.data.energy = 1.8
    sun.data.angle = math.radians(35)
    # A 35-degree sun disc would read as a white blob in the window glass.
    sun.visible_glossy = False

    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.location = camera_spec["positionM"]
    camera.rotation_euler = (math.pi / 2, 0, -math.radians(camera_spec["yawDegrees"]))
    camera.data.sensor_fit = "HORIZONTAL"
    camera.data.sensor_width = camera_spec["sensorWidthMm"]
    camera.data.lens = camera_spec["lensMm"]
    camera.data.shift_y = camera_spec["shiftY"]
    camera.data.clip_end = 1000
    scene.camera = camera

    image = output / "render-rgba.png"
    output.mkdir(parents=True)
    scene.render.filepath = str(image)
    render_started = time.monotonic()
    assert bpy.ops.render.render(write_still=True) == {"FINISHED"}
    record = {
        "blend": {"path": str(blend.relative_to(ROOT)), "sha256": digest(blend)},
        "camera": {"path": str(args.camera), "sha256": digest(ROOT / args.camera), **camera_spec},
        "script": {"path": str(Path(__file__).relative_to(ROOT)), "sha256": digest(__file__)},
        "blenderVersion": bpy.app.version_string,
        "device": [d.name for d in devices],
        "samples": args.samples,
        "adaptiveThreshold": scene.cycles.adaptive_threshold,
        "viewTransform": "Standard",
        "exposure": args.exposure,
        "lighting": {
            "worldStrength": 1.0,
            "sunEnergy": sun.data.energy,
            "sunAngleDegrees": 35,
            "toward": list(toward),
        },
        "image": {
            "path": str(image.relative_to(ROOT)),
            "sha256": digest(image),
            "resolution": [width * args.percentage // 100, height * args.percentage // 100],
        },
        "renderSeconds": time.monotonic() - render_started,
        "elapsedSeconds": time.monotonic() - started,
    }
    (output / "render.json").write_text(json.dumps(record, indent=2) + "\n")
    print("STUDIO " + json.dumps({"renderSeconds": record["renderSeconds"]}), flush=True)


if __name__ == "__main__":
    main()

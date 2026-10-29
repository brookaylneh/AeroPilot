"""Render a built asset through every fitted view under fixed soft studio light.

blender -b --factory-startup --python world/unreal/blender/studio.py -- --build <build dir>
--cameras <cameras.json> --output <new .local dir>
Writes one straight-alpha RGBA PNG per view, with the floor shadow held in alpha for compositing
over the reference studio colour, and render.json.
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from world.unreal.blender.spec import ROOT, digest, output_dir  # noqa: E402

SAMPLES = 1024
# Soft key from high front-right of the asset, so the master view's shadow falls behind it.
TOWARD = Vector((0.45, -0.35, 0.82)).normalized()
SUN_ANGLE_DEGREES = 35


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--cameras", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    build = json.loads((ROOT / args.build / "build.json").read_text())
    blend = ROOT / args.build / f"{build['asset']}.blend"
    cameras = json.loads((ROOT / args.cameras).read_text())
    assert cameras["build"]["sha256"] == digest(ROOT / args.build / "build.json")
    output = output_dir(args.output)
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
    scene.cycles.samples = SAMPLES
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.004
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.denoising_use_gpu = True
    scene.cycles.seed = 24
    scene.cycles.max_bounces = 12
    scene.render.film_transparent = True
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"

    world = bpy.data.worlds.new("Studio")
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    scene.world = world
    bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, 0))
    bpy.context.object.is_shadow_catcher = True
    bpy.ops.object.light_add(type="SUN")
    sun = bpy.context.object
    sun.rotation_euler = (-TOWARD).to_track_quat("-Z", "Y").to_euler()
    sun.data.energy = 1.8
    sun.data.angle = math.radians(SUN_ANGLE_DEGREES)
    # A 35-degree sun disc would read as a white blob in window glass.
    sun.visible_glossy = False
    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.data.sensor_fit = "HORIZONTAL"
    camera.data.sensor_width = 36.0
    camera.data.clip_end = 1000
    scene.camera = camera

    images = {}
    for view, spec in cameras["views"].items():
        width, height = spec["resolution"]
        scene.render.resolution_x, scene.render.resolution_y = width, height
        camera.location = spec["positionM"]
        camera.rotation_euler = (
            math.pi / 2 + math.radians(spec["pitchDegrees"]),
            0,
            -math.radians(spec["yawDegrees"]),
        )
        if spec["projection"] == "perspective":
            camera.data.type = "PERSP"
            camera.data.lens = spec["focalPixels"] / width * camera.data.sensor_width
        else:
            assert spec["projection"] == "orthographic", view
            camera.data.type = "ORTHO"
            camera.data.ortho_scale = spec["metresPerPixel"] * width
        # With horizontal sensor fit, Blender's shift is a fraction of the image width.
        camera.data.shift_y = spec["shiftYPixels"] / width
        image = output / f"{view}.png"
        scene.render.filepath = str(image)
        started = time.monotonic()
        assert bpy.ops.render.render(write_still=True) == {"FINISHED"}
        images[view] = {
            "sha256": digest(image),
            "resolution": [width, height],
            "renderSeconds": time.monotonic() - started,
        }
    record = {
        "blend": {"path": str(blend.relative_to(ROOT)), "sha256": digest(blend)},
        "cameras": {"path": str(args.cameras), "sha256": digest(ROOT / args.cameras)},
        "script": {"path": str(Path(__file__).relative_to(ROOT)), "sha256": digest(__file__)},
        "blenderVersion": bpy.app.version_string,
        "device": [d.name for d in devices],
        "samples": SAMPLES,
        "adaptiveThreshold": scene.cycles.adaptive_threshold,
        "viewTransform": "Standard",
        "lighting": {
            "worldStrength": 1.0,
            "sunEnergy": sun.data.energy,
            "sunAngleDegrees": SUN_ANGLE_DEGREES,
            "toward": list(TOWARD),
        },
        "images": images,
    }
    (output / "render.json").write_text(json.dumps(record, indent=2) + "\n")
    print("STUDIO " + json.dumps({v: i["renderSeconds"] for v, i in images.items()}), flush=True)


if __name__ == "__main__":
    main()

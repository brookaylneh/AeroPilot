"""Render actual CC0 candidate geometry with metric cameras in Blender 3.6."""

import hashlib
import json
import math
import random
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / ".venv-cosys/lib/python3.10/site-packages"))
from mission_planner.environment import load_env  # noqa: E402

OUT = ROOT / ".local/world/valley/asset-previews"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def material(name, color, roughness=0.7, metallic=0):
    value = bpy.data.materials.new(name)
    value.use_nodes = True
    shader = value.node_tree.nodes["Principled BSDF"]
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    return value


def box(name, location, dimensions, surface):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(surface)
    return obj


def mesh(name, vertices, faces, surface):
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(surface)
    return obj


def beam(name, start, end, width, surface):
    a, b = Vector(start), Vector(end)
    obj = box(name, (a + b) / 2, (width, width, (b - a).length), surface)
    obj.rotation_euler = (b - a).to_track_quat("Z", "Y").to_euler()
    return obj


def originals(candidate):
    wood = material("Weathered cedar concept", (0.25, 0.16, 0.08))
    steel = material("Painted metal concept", (0.12, 0.16, 0.15), 0.45, 0.6)
    kind = candidate["id"]
    if kind == "original_bridge":
        for i in range(31):
            box("Deck board", (i * 0.2 - 3, 0, 0.65), (0.18, 1.8, 0.12), wood)
        for y in (-0.8, 0.8):
            box("Stringer", (0, y, 0.4), (6.4, 0.15, 0.3), wood)
            box("Handrail", (0, y, 1.65), (6.4, 0.1, 0.12), wood)
            for x in (-3, -1, 1, 3):
                box("Rail post", (x, y, 0.85), (0.14, 0.14, 1.7), wood)
    elif kind == "original_tower":
        for x in (-1.3, 1.3):
            for y in (-1.3, 1.3):
                box("Tower post", (x, y, 3), (0.25, 0.25, 6), wood)
        for y in (-1.3, 1.3):
            for level in (0, 2.8):
                beam("Cross brace", (-1.3, y, level), (1.3, y, level + 2.8), 0.14, wood)
                beam("Cross brace", (1.3, y, level), (-1.3, y, level + 2.8), 0.14, wood)
        box("Lookout deck", (0, 0, 6), (4, 4, 0.2), wood)
        glass = material("Window concept", (0.25, 0.4, 0.43), 0.2, 0.35)
        box("Cabin lower", (0, 0, 6.6), (2.8, 2.8, 1), wood)
        box("Cabin windows", (0, 0, 7.5), (2.75, 2.75, 0.8), glass)
        for x in (-1.4, 1.4):
            for y in (-1.4, 1.4):
                box("Window frame", (x, y, 7.5), (0.1, 0.1, 0.9), wood)
        mesh(
            "Pitched roof",
            [
                (-1.8, -1.8, 7.95),
                (1.8, -1.8, 7.95),
                (-1.8, 0, 8.8),
                (1.8, 0, 8.8),
                (-1.8, 1.8, 7.95),
                (1.8, 1.8, 7.95),
            ],
            [(0, 1, 3, 2), (2, 3, 5, 4)],
            steel,
        )
        for i in range(24):
            box("Access stair tread", (0, -5 + i * 0.16, 0.125 + i * 0.25), (0.8, 0.25, 0.12), wood)
    elif kind == "original_signs":
        box("Trail signpost", (0, 0, 1.1), (0.14, 0.14, 2.2), wood)
        for z, width in ((1.6, 1.3), (1.95, 1.05)):
            mesh(
                "Arrow board",
                [
                    (-width / 2, -0.08, z - 0.11),
                    (width / 2, -0.08, z - 0.11),
                    (width / 2 + 0.15, -0.08, z),
                    (width / 2, -0.08, z + 0.11),
                    (-width / 2, -0.08, z + 0.11),
                ],
                [(0, 1, 2, 3, 4)],
                wood,
            )
        lettering = material("Sign lettering", (0.86, 0.79, 0.6))
        for label, z in (("CREEK LOOP", 1.61), ("POND", 1.96)):
            bpy.ops.object.text_add(
                location=(-0.45, -0.085, z - 0.04), rotation=(math.pi / 2, 0, 0)
            )
            text = bpy.context.object
            text.data.body = label
            text.data.size = 0.1
            text.data.extrude = 0.001
            text.data.materials.append(lettering)
            bpy.ops.object.convert(target="MESH")
    elif kind == "original_vehicles":
        paint = material("Utility van cream concept", (0.68, 0.65, 0.5), 0.4, 0.2)
        glass = material("Utility van window concept", (0.08, 0.17, 0.2), 0.16, 0.4)
        rubber = material("Rubber", (0.015, 0.019, 0.017), 0.8)
        box("Van body blockout", (0, 0, 1.1), (4.7, 1.9, 1.25), paint)
        box("Cargo roof blockout", (0.65, 0, 1.95), (3.3, 1.9, 0.65), paint)
        box("Cab windscreen blockout", (-1.55, 0, 1.95), (1, 1.8, 0.65), glass)
        for x in (-1.5, 1.5):
            for y in (-0.96, 0.96):
                bpy.ops.mesh.primitive_cylinder_add(
                    vertices=24,
                    radius=0.4,
                    depth=0.25,
                    location=(x, y, 0.4),
                    rotation=(math.pi / 2, 0, 0),
                )
                bpy.context.object.name = "Wheel"
                bpy.context.object.data.materials.append(rubber)
        for x in (-2.4, 2.4):
            box("Bumper", (x, 0, 0.7), (0.14, 1.9, 0.22), steel)
    elif kind == "original_tent":
        fabric = material("Ochre tent canvas concept", (0.48, 0.29, 0.065), 0.9)
        mesh(
            "Tent fly",
            [
                (-1.4, -1.1, 0),
                (-1.4, 1.1, 0),
                (-1.4, 0, 1.55),
                (1.4, -1.1, 0),
                (1.4, 1.1, 0),
                (1.4, 0, 1.55),
            ],
            [(0, 3, 5, 2), (2, 5, 4, 1), (0, 2, 1), (3, 4, 5)],
            fabric,
        )
        for x in (-1.4, 1.4):
            beam("Ridge pole", (x, 0, 0), (x, 0, 1.6), 0.03, steel)
        cord = material("Guy cord", (0.57, 0.55, 0.4))
        for x in (-1.4, 1.4):
            beam("Guy rope", (x, 0, 1.55), (x * 1.7, 0, 0), 0.012, cord)
    elif kind == "original_grass":
        rng = random.Random(7301)
        green = material("Meadow stems", (0.13, 0.22, 0.07))
        petal = material("Golden meadow blooms", (0.82, 0.58, 0.1))
        for _ in range(16):
            x, y = rng.uniform(-0.28, 0.28), rng.uniform(-0.28, 0.28)
            height = rng.uniform(0.25, 0.6)
            beam("Flower stem", (x, y, 0), (x + 0.04, y, height), 0.008, green)
            for i in range(5):
                angle = i * math.tau / 5
                a = (x + 0.04, y, height)
                b = (
                    a[0] + 0.06 * math.cos(angle - 0.5),
                    y + 0.06 * math.sin(angle - 0.5),
                    height + 0.015,
                )
                c = (
                    a[0] + 0.07 * math.cos(angle + 0.5),
                    y + 0.07 * math.sin(angle + 0.5),
                    height + 0.015,
                )
                mesh("Bloom petal", [a, b, c], [(0, 1, 2)], petal)
    elif kind == "original_water":
        water = material("Shallow creek water concept", (0.025, 0.12, 0.1), 0.15, 0.1)
        shader = water.node_tree.nodes["Principled BSDF"]
        shader.inputs["Transmission"].default_value = 0.55
        shader.inputs["IOR"].default_value = 1.333
        noise = water.node_tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 18
        bump = water.node_tree.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.25
        bump.inputs["Distance"].default_value = 0.03
        water.node_tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        water.node_tree.links.new(bump.outputs["Normal"], shader.inputs["Normal"])
        box("Water sample volume", (0, 0, 0.1), (12, 8, 0.2), water)
    else:
        raise AssertionError(kind)
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def bounds(objects):
    vertices = [o.matrix_world @ Vector(corner) for o in objects for corner in o.bound_box]
    low = Vector([min(v[a] for v in vertices) for a in range(3)])
    high = Vector([max(v[a] for v in vertices) for a in range(3)])
    return low, high


def import_asset(candidate):
    spec = candidate["previewInput"]
    source = OUT / spec["file"]
    gltf = json.loads(source.read_text())
    chosen = [n for n in gltf["nodes"] if n["name"] == spec["node"]]
    assert len(chosen) == 1
    selected = dict(chosen[0])
    gltf["meshes"] = [gltf["meshes"][selected["mesh"]]]
    selected["mesh"] = 0
    selected["translation"] = [0, 0, 0]
    gltf["nodes"] = [selected]
    gltf["scenes"] = [{"nodes": [0]}]
    gltf["scene"] = 0
    subset = source.parent / "preview-selected.gltf"
    assert not subset.exists(), subset
    subset.write_text(json.dumps(gltf))
    bpy.ops.import_scene.gltf(filepath=str(subset))
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert len(objects) == 1, [o.name for o in objects]
    if candidate["category"] == "ground_textures":
        surface = objects[0].data.materials[0]
        bpy.data.objects.remove(objects[0], do_unlink=True)
        bpy.ops.mesh.primitive_plane_add(size=12)
        obj = bpy.context.object
        obj.name = "12 m material review fixture"
        obj.data.materials.append(surface)
        for uv in obj.data.uv_layers.active.data:
            uv.uv *= 12 / spec["textureTileMetres"]
        objects = [obj]
    return objects


def triangles(objects):
    total = 0
    for obj in objects:
        obj.data.calc_loop_triangles()
        total += len(obj.data.loop_triangles)
    return total


def render_view(name, camera, location, target, folder):
    camera.location = location
    camera.rotation_euler = (Vector(target) - camera.location).to_track_quat("-Z", "Y").to_euler()
    path = folder / (name + ".png")
    bpy.context.scene.render.filepath = str(path)
    start = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    elapsed = time.perf_counter() - start
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": digest(path),
        "renderSeconds": elapsed,
        "cameraPositionMetres": list(location),
        "cameraTargetMetres": list(target),
        "horizontalFovDegrees": math.degrees(camera.data.angle_x),
        "projection": camera.data.type,
        "resolution": [1280, 720],
    }


def main():
    load_env()
    candidate_id = sys.argv[sys.argv.index("--") + 1]
    lock = json.loads((ROOT / "world/unreal/valley-assets.lock.json").read_text())
    candidate = next(c for c in lock["candidates"] if c["id"] == candidate_id)
    assert candidate["license"]["id"] == "CC0-1.0"
    folder = OUT / candidate_id / "renders"
    assert folder.is_dir() and not list(folder.iterdir())
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    spec = candidate["previewInput"]
    objects = import_asset(candidate) if spec["kind"] == "gltf" else originals(candidate)
    bpy.context.view_layer.update()
    low, high = bounds(objects)
    original_dimensions = list(high - low)
    offset = Vector((-(low.x + high.x) / 2, -(low.y + high.y) / 2, -low.z))
    for obj in objects:
        obj.location = (obj.location + offset) * spec["scale"]
        obj.scale *= spec["scale"]
    bpy.context.view_layer.update()
    single_triangles = triangles(objects)
    generated = None
    if spec["kind"] == "procedural":
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        generated_path = folder / "original-concept.glb"
        bpy.ops.export_scene.gltf(
            filepath=str(generated_path), export_format="GLB", use_selection=True
        )
        generated = {
            "path": str(generated_path.relative_to(ROOT)),
            "sha256": digest(generated_path),
            "license": candidate["license"]["id"],
            "scope": "Original preliminary Stage 1 concept geometry only",
        }
    if spec["instances"] > 1:
        assert spec["instances"] == 25
        base = list(objects)
        for row in range(5):
            for column in range(5):
                delta = Vector((column - 2, row - 2, 0))
                if row == 2 and column == 2:
                    continue
                for obj in base:
                    clone = obj.copy()
                    clone.data = obj.data
                    clone.location += delta
                    bpy.context.collection.objects.link(clone)
                    objects.append(clone)
    bpy.context.view_layer.update()
    low, high = bounds(objects)
    dimensions = list(high - low)
    if candidate["category"] == "trees":
        assert dimensions[2] <= 18, dimensions
    textures = []
    images = {
        node.image.name: node.image
        for obj in objects
        for mat in obj.data.materials
        for node in mat.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image is not None
    }
    for name, img in sorted(images.items()):
        derived = not img.filepath
        if derived:
            pixels = np.empty(len(img.pixels), dtype=np.float32)
            img.pixels.foreach_get(pixels)
            assert np.isfinite(pixels).all(), name
            path = folder / ("derived-" + name + ".png")
            img.filepath_raw = str(path)
            img.file_format = "PNG"
            img.save()
        else:
            path = Path(bpy.path.abspath(img.filepath))
        textures.append(
            {
                "name": name,
                "width": img.size[0],
                "height": img.size[1],
                "sha256": digest(path),
                "path": str(path.relative_to(ROOT)),
                "derivedByBlenderImporter": derived,
            }
        )
    ground = material("Neutral review ground", (0.24, 0.25, 0.18), 0.95)
    box(
        "Review ground fixture; excluded from asset metrics",
        (0, 0, -0.105),
        (150, 150, 0.2),
        ground,
    )
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.cycles.seed = 731
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 2
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Filmic"
    scene.view_settings.look = "Medium High Contrast"
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.55, 0.67, 0.8, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.7
    bpy.ops.object.light_add(type="SUN", location=(15, -15, 25))
    sun = bpy.context.object
    sun.rotation_euler = (math.radians(30), math.radians(-20), math.radians(-30))
    sun.data.energy = 3
    sun.data.angle = math.radians(1)
    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.data.type = "PERSP"
    camera.data.sensor_fit = "HORIZONTAL"
    camera.data.angle = math.radians(70)
    camera.data.clip_end = 300
    scene.camera = camera
    nadir = render_view("nadir", camera, (0, 0, 30), (0, 0, 0), folder)
    assert abs(nadir["horizontalFovDegrees"] - 70) < 1e-4
    close_dimensions = dimensions
    if candidate["category"] == "grass":
        for obj in objects:
            obj.hide_render = obj not in base
        close_dimensions = [v * spec["scale"] for v in original_dimensions]
    elif candidate["category"] == "ground_textures":
        close_dimensions = [spec["textureTileMetres"], spec["textureTileMetres"], 0]
    radius = max(close_dimensions) / 2
    target = Vector((0, 0, close_dimensions[2] / 2))
    close_location = target + Vector((1, -1.4, 0.9)).normalized() * max(radius * 4.3, 1)
    close = render_view("close", camera, close_location, target, folder)
    close["framedBoundsMetres"] = close_dimensions
    close["instances"] = 1
    report = {
        "candidate": candidate_id,
        "blenderVersion": bpy.app.version_string,
        "blenderBuildHash": bpy.app.build_hash.decode(),
        "renderer": "Cycles CPU",
        "threads": scene.render.threads,
        "samples": scene.cycles.samples,
        "generatorSha256": digest(Path(__file__)),
        "sourceNode": spec["node"],
        "sourceBoundsMetres": original_dimensions,
        "boundsMetres": dimensions,
        "scale": spec["scale"],
        "instances": spec["instances"],
        "singleAssetTriangles": single_triangles,
        "triangles": triangles(objects),
        "geometryNote": "Metric plane fixture"
        if candidate["category"] == "ground_textures"
        else "Actual selected geometry; review ground excluded",
        "textures": textures,
        "texturePixels": sum(t["width"] * t["height"] for t in textures),
        "generatedAsset": generated,
        "nadir": nadir,
        "close": close,
    }
    (folder / "metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()

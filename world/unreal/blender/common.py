"""Shared Blender asset harness: seeded parts, PBR materials, GLB export and build.json.

A family module calls `main(Path(__file__), BUILDERS)`. Every asset is built in metres with
+Z up and its front facing +X; `finish` moves the origin to the ground-contact centre at grade,
`buriedM` above the lowest vertex. The GLB holds meshes and material slots; textures ship as
the PNGs listed in build.json.
"""

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

from world.unreal.blender.spec import LOCK, ROOT, TEXTURES, digest, load, output_dir

GROUND_M = 0.01
# GPU bakes are not bit-exact: a repeat may move texels by up to 2 of 255 levels, in at most
# 1e-5 of the channel values of each texture.
TEXTURE_TOLERANCE = (2, 1e-5)


def add(parts, material, bm):
    """Accumulate a bmesh into the per-material geometry of `parts` and free it."""
    verts, faces, uvs = parts.setdefault(material, ([], [], []))
    uv = bm.loops.layers.uv.active
    bm.verts.index_update()
    bm.verts.ensure_lookup_table()
    order = {}
    # bmesh operators order their output by pointer hash; coordinate order keeps exports identical.
    for face in sorted(bm.faces, key=lambda f: [tuple(loop.vert.co) for loop in f.loops]):
        faces.append(
            [order.setdefault(loop.vert.index, len(verts) + len(order)) for loop in face.loops]
        )
        uvs.extend(tuple(loop[uv].uv) for loop in face.loops)
    verts.extend(bm.verts[i].co.copy() for i in order)
    bm.free()


def emit(parts, materials):
    """One smooth-shaded mesh object per material, sharp above 35 degrees."""
    objects = []
    for name, (verts, faces, uvs) in parts.items():
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([tuple(v) for v in verts], [], faces)
        mesh.uv_layers.new(name="UVMap").data.foreach_set(
            "uv", np.array(uvs, dtype=np.float32).ravel()
        )
        assert not mesh.validate(), name
        mesh.set_sharp_from_angle(angle=math.radians(35))
        mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
        mesh.materials.append(materials[name])
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        objects.append(obj)
    return objects


def frame(origin, x_axis, y_axis):
    x, y = Vector(x_axis).normalized(), Vector(y_axis).normalized()
    assert abs(x.dot(y)) < 1e-6, (x, y)
    z = x.cross(y)
    return Matrix(
        (
            (x.x, y.x, z.x, origin[0]),
            (x.y, y.y, z.y, origin[1]),
            (x.z, y.z, z.z, origin[2]),
            (0, 0, 0, 1),
        )
    )


def box(
    parts,
    material,
    size,
    matrix,
    rng,
    bevel=0.004,
    segments=1,
    tile=0.8,
    grain=0,
    clip=(),
    band=None,
):
    """Bevelled box centred on `matrix`; texture grain (image V) follows local axis `grain`.

    With `band`, the faces normal to local y show one scanned plank: image V spans the band
    across the local z height and image U runs `tile` metres per texture along local x.
    """
    assert min(size) > 0, size
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    if bevel:
        bmesh.ops.bevel(
            bm,
            geom=list(bm.edges),
            offset=min(bevel, 0.3 * min(size)),
            segments=segments,
            profile=0.5,
            affect="EDGES",
            clamp_overlap=True,
        )
    uv = bm.loops.layers.uv.new("UVMap")
    offset = (rng.random(), rng.random())
    flip = rng.choice((1, -1))
    for face in bm.faces:
        axis = max(range(3), key=lambda i: abs(face.normal[i]))
        across = [i for i in range(3) if i != axis]
        v_axis = grain if grain in across else across[1]
        u_axis = across[0] if across[0] != v_axis else across[1]
        for loop in face.loops:
            co = loop.vert.co
            if band and axis == 1:
                v = band[0] + (co[2] / size[2] + 0.5) * (band[1] - band[0])
                loop[uv].uv = (co[0] / tile + offset[0], v)
            else:
                loop[uv].uv = (co[u_axis] / tile + offset[0], flip * co[v_axis] / tile + offset[1])
    bm.transform(matrix)
    for point, normal in clip:
        cut = bmesh.ops.bisect_plane(
            bm,
            geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
            plane_co=point,
            plane_no=normal,
            clear_outer=True,
        )
        edges = [e for e in cut["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
        bmesh.ops.holes_fill(bm, edges=edges)
    if bm.faces:
        add(parts, material, bm)
    else:
        bm.free()


def placed(parts, material, size, centre, rng, **kwargs):
    """Axis-aligned `box` centred on `centre`."""
    box(parts, material, size, Matrix.Translation(centre), rng, **kwargs)


def beam(parts, material, start, end, width, depth, up, rng, **kwargs):
    """Timber from `start` to `end`; `depth` is measured along `up`, grain along the length."""
    start, end = Vector(start), Vector(end)
    axis = end - start
    side = Vector(up).cross(axis)
    assert axis.length > 1e-6 and side.length > 1e-6, (start, end, up)
    matrix = frame((start + end) / 2, axis, side.normalized())
    box(parts, material, (axis.length, width, depth), matrix, rng, grain=0, **kwargs)


def gpu(scene):
    """Select Cycles on every OptiX device and return their names."""
    preferences = bpy.context.preferences.addons["cycles"].preferences
    preferences.compute_device_type = "OPTIX"
    preferences.refresh_devices()
    devices = [d.name for d in preferences.devices if d.type == "OPTIX"]
    assert devices, "OptiX device required"
    for device in preferences.devices:
        device.use = device.type == "OPTIX"
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    return devices


def lin(srgb):
    """sRGB bytes to linear RGB."""
    return tuple(((c / 255 + 0.055) / 1.055) ** 2.4 for c in srgb)


def image(path, colour=False):
    img = bpy.data.images.load(str(path))
    img.colorspace_settings.name = "sRGB" if colour else "Non-Color"
    return img


def pixels(img):
    values = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(values)
    # Blender stores rows bottom-up, so row index maps directly to image V.
    return values.reshape(img.size[1], img.size[0], 4)


def matched(path, target, name, output):
    """Match a CC0 albedo's sRGB mean and luminance spread to a reference region."""
    source = bpy.data.images.load(str(path))
    values = pixels(source)
    # Byte images expose their stored sRGB values, not linear radiance.
    srgb = values[..., :3]
    mean = srgb.reshape(-1, 3).mean(axis=0)
    gray = srgb.mean(axis=2, keepdims=True)
    detail = (srgb - mean) * 0.6 + (gray - gray.mean()) * 0.4
    values[..., :3] = np.clip(
        np.array(target[0]) / 255 + detail * target[1] / 255 / gray.std(), 0, 1
    )
    bpy.data.images.remove(source)
    return saved(name, values, output, colour=True), {
        "source": str(path.relative_to(ROOT)),
        "sourceMeanSrgb": (mean * 255).round(1).tolist(),
        "targetMeanSrgb": list(target[0]),
        "targetLuminanceStd": target[1],
    }


def plank_bands(img):
    """Image-V intervals of individual planks, found from the dark gaps between them."""
    rows = pixels(img)[..., :3].mean(axis=(1, 2))
    smooth = np.convolve(rows, np.ones(9) / 9, mode="same")
    gaps = np.flatnonzero(smooth < smooth.mean() - 0.8 * smooth.std())
    edges = [g for i, g in enumerate(gaps) if i == 0 or g - gaps[i - 1] > 20]
    spans = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 60]
    typical = np.median([b - a for a, b in spans])
    bands = [
        ((a + 0.12 * (b - a)) / len(rows), (b - 0.12 * (b - a)) / len(rows))
        for a, b in spans
        if 0.75 * typical < b - a < 1.3 * typical
    ]
    assert len(bands) >= 8, len(bands)
    return bands


def stored(img, output):
    img.filepath_raw = str(output / f"{img.name}.png")
    img.file_format = "PNG"
    img.save()
    return img


def saved(name, values, output, colour=False):
    """An opaque PNG image from a (rows, columns, 3 or 4) array, rows along image V."""
    rows, columns, channels = values.shape
    assert channels in (3, 4), values.shape
    if channels == 3:
        values = np.concatenate([values, np.ones((rows, columns, 1))], axis=2)
    img = bpy.data.images.new(name, columns, rows, alpha=False, is_data=not colour)
    img.pixels.foreach_set(values.astype(np.float32).ravel())
    return stored(img, output)


def directx(name, gl, output):
    """T_<name>_N for Unreal (green down) from an OpenGL tangent-space normal map array."""
    dx = gl.copy()
    dx[..., 1] = 1 - dx[..., 1]
    return saved(f"T_{name}_N", dx, output)


def pbr(name, bc, ao, rough, output, gl=None):
    """MI_<name> from a base colour image, T_<name>_ORM (AO, roughness, metal) written from
    2D arrays, and an optional OpenGL normal map image."""
    assert ao.shape == rough.shape and ao.ndim == 2, (ao.shape, rough.shape)
    orm = saved(f"T_{name}_ORM", np.stack([ao, rough, np.zeros_like(rough)], axis=2), output)
    mat = principled(f"MI_{name}", base_image=bc, rough=0.5, normal_image=gl)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    channels = nodes.new("ShaderNodeSeparateColor")
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = orm
    links.new(texture.outputs["Color"], channels.inputs["Color"])
    links.new(channels.outputs["Green"], nodes["Principled BSDF"].inputs["Roughness"])
    return mat


def principled(
    name,
    base=None,
    base_image=None,
    rough=None,
    rough_image=None,
    normal_image=None,
    strength=1.0,
    metallic=0.0,
    transmission=0.0,
):
    assert (base is None) != (base_image is None) and (rough is None) != (rough_image is None)
    mat = bpy.data.materials.new(name)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]

    def texture(img, socket):
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        links.new(node.outputs["Color"], socket)

    if base_image:
        texture(base_image, bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = (*base, 1)
    if rough_image:
        texture(rough_image, bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = rough
    if normal_image:
        normal = nodes.new("ShaderNodeNormalMap")
        normal.inputs["Strength"].default_value = strength
        texture(normal_image, normal.inputs["Color"])
        links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Transmission Weight"].default_value = transmission
    return mat


def geometry(objects):
    """sha256 over each object's vertex positions, face sizes, face corners and UVs."""
    sha = hashlib.sha256()
    for obj in sorted(objects, key=lambda o: o.name):
        mesh = obj.data
        for items, key, width, kind in (
            (mesh.vertices, "co", 3, np.float32),
            (mesh.polygons, "loop_total", 1, np.int32),
            (mesh.loops, "vertex_index", 1, np.int32),
            (mesh.uv_layers.active.data, "uv", 2, np.float32),
        ):
            values = np.empty(len(items) * width, kind)
            items.foreach_get(key, values)
            sha.update(values.tobytes())
    return sha.hexdigest()


def repeated(report, previous, output):
    """Assert that this build repeats `previous`: identical geometry and GLB, and every texture
    within TEXTURE_TOLERANCE. Returns the measured texture differences."""
    before = json.loads((previous / "build.json").read_text())
    for key in ("geometrySha256", "glb"):
        assert report[key] == before[key], key
    assert report["textures"].keys() == before["textures"].keys()
    levels, fraction = TEXTURE_TOLERANCE
    differences = {}
    for name, entry in report["textures"].items():
        if entry == before["textures"][name]:
            differences[name] = {"maxLevels": 0, "fraction": 0.0}
            continue
        a, b = (
            np.round(pixels(image(d / f"{name}.png"))[..., :3] * 255) for d in (previous, output)
        )
        diff = np.abs(a - b)
        differences[name] = {"maxLevels": int(diff.max()), "fraction": float((diff > 0).mean())}
        assert diff.max() <= levels and (diff > 0).mean() <= fraction, (name, differences[name])
    return {"build": str(previous.relative_to(ROOT)), "textures": differences}


def finish(script, asset, spec, objects, output):
    """Move the origin to the ground-contact centre, assert dimensions, export and report."""
    vertices = []
    for obj in objects:
        assert obj.parent is None and obj.matrix_world == Matrix.Identity(4), obj.name
        co = np.empty(len(obj.data.vertices) * 3)
        obj.data.vertices.foreach_get("co", co)
        vertices.append(co.reshape(-1, 3))
    co = np.concatenate(vertices)
    grade = co[:, 2].min() + spec["buriedM"]
    ground = co[np.abs(co[:, 2] - grade) <= GROUND_M, :2]
    shift = -np.array([*(ground.min(axis=0) + ground.max(axis=0)) / 2, grade])
    for obj in objects:
        obj.data.transform(Matrix.Translation(shift))
    bounds = np.array([co.min(axis=0), co.max(axis=0)]) + shift
    extents = bounds[1] - bounds[0]
    assert np.allclose(extents, spec["dimensionsM"], rtol=0, atol=spec["toleranceM"]), extents
    textures = {}
    for img in bpy.data.images:
        assert max(img.size) <= spec["textureSize"], (img.name, tuple(img.size))
        textures[img.name] = {"size": list(img.size), "sha256": digest(img.filepath_raw)}
    triangles = {}
    for obj in objects:
        obj.data.calc_loop_triangles()
        triangles[obj.name] = len(obj.data.loop_triangles)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / f"{asset}.blend"), compress=True)
    glb = output / f"{asset}.glb"
    assert bpy.ops.export_scene.gltf(
        filepath=str(glb),
        export_format="GLB",
        export_image_format="NONE",
        export_cameras=False,
        export_lights=False,
        export_yup=True,
    ) == {"FINISHED"}
    return {
        "asset": asset,
        "scripts": {str(p.relative_to(ROOT)): digest(p) for p in (script, Path(__file__))},
        "blenderVersion": bpy.app.version_string,
        "blenderBuildHash": bpy.app.build_hash.decode(),
        "spec": spec,
        "frame": "metres, +Z up, front +X, origin at the ground-contact centre; GLB is glTF +Y up",
        "originShiftM": shift.tolist(),
        "boundsM": bounds.tolist(),
        "extentsM": extents.tolist(),
        "triangles": sum(triangles.values()),
        "objectTriangles": triangles,
        "geometrySha256": geometry(objects),
        "textures": textures,
        "polyhavenLockSha256": digest(LOCK),
        "glb": {"bytes": glb.stat().st_size, "sha256": digest(glb)},
    }


def main(script, builders):
    """Parse `<asset> --output <dir> [--repeat-of <dir>]`, build the asset with its seed and
    write build.json; with --repeat-of, assert it repeats that earlier build."""
    parser = argparse.ArgumentParser(prog=script.name)
    parser.add_argument("asset", choices=sorted(builders))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat-of", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    assets = load(script.stem)
    assert sorted(assets) == sorted(builders), script
    spec = assets[args.asset]
    output = output_dir(args.output)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for entry in json.loads(LOCK.read_text())["files"]:
        path = TEXTURES / entry["asset"] / Path(entry["url"]).name
        assert hashlib.md5(path.read_bytes()).hexdigest() == entry["md5"], path
    objects, details = builders[args.asset](random.Random(spec["seed"]), output)
    report = finish(script, args.asset, spec, objects, output) | {"details": details}
    if args.repeat_of:
        report["repeatOf"] = repeated(report, (ROOT / args.repeat_of).resolve(), output)
    (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
    print("BUILD " + json.dumps({k: report[k] for k in ("extentsM", "triangles")}), flush=True)

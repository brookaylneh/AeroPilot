"""Normalise a pinned external mesh into a valley asset build.

blender -b --factory-startup --python world/unreal/blender/external.py -- <asset> --output <dir>
external.json gives each asset its source row in external.lock.json, yaw to front +X, target
dimensions in metres (null where no standard applies), surface class and measured master
colours; every source sha256 is asserted. The mesh is turned and uniformly scaled to the
targets (each within 5 %), its base colour matched to the reference master, and it gets
T_<Name>_{BC,N,ORM} on its own UVs plus the shared tiling detail T_Detail<Surface>_{D,N}
sampled at UV0 times build.json's details.detailUvScale. T_*_N is DirectX for Unreal; the .blend
uses T_*_N_GL. With collision, UCX_<SM name> holds a convex hull for Unreal.
"""

import argparse
import hashlib
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from world.unreal.blender.common import directx, finish, image, pixels, saved  # noqa: E402
from world.unreal.blender.spec import ROOT, digest, output_dir  # noqa: E402
from world.unreal.import_assets import EXTERNAL, names  # noqa: E402

LOCK = EXTERNAL.with_name("external.lock.json")

ROW = {"id", "path", "source", "licence", "attribution", "sha256", "member"}
SURFACE = {"albedo", "normal", "tileM", "albedoStrength", "normalStrength"}
ASSET = {
    "source",
    "reference",
    "category",
    "surface",
    "yawDegrees",
    "dimensionsM",
    "dimensionsSource",
    "collision",
    "colour",
}
TOLERANCE = 0.05
# Area-weighted 5-95 % texel density spread allowed for tiling the detail layer on UV0.
UV_SPREAD = 0.15
HULL_DIRECTIONS = 128
DETAIL_PX = 2048
LUMINANCE = np.array([0.2126, 0.7152, 0.0722])


def check(external, lock):
    """Assert the external.json and external.lock.json schemas; return lock rows by id."""
    assert external["schemaVersion"] == 1 and set(external) == {
        "schemaVersion",
        "surfaces",
        "assets",
    }
    assert lock["schemaVersion"] == 1 and set(lock) == {"schemaVersion", "files"}
    rows = {row["id"]: row for row in lock["files"]}
    assert len(rows) == len(lock["files"]), "duplicate lock ids"
    for row in rows.values():
        assert set(row) == ROW and len(row["sha256"]) == 64, row["id"]
        assert row["member"] is None or set(row["member"]) == {"name", "sha256"}, row["id"]
    for name, surface in external["surfaces"].items():
        assert set(surface) == SURFACE, name
        assert (
            rows[surface["albedo"]]["member"] is None and rows[surface["normal"]]["member"] is None
        )
        assert 0 < surface["tileM"] < 10, name
        assert 0 < surface["albedoStrength"] < 1 and 0 < surface["normalStrength"] <= 1, name
    for name, asset in external["assets"].items():
        assert set(asset) == ASSET, (name, set(asset) ^ ASSET)
        assert rows[asset["source"]]["member"]["name"].endswith(".glb"), name
        assert asset["category"].isalpha() and asset["category"].istitle(), name
        assert asset["surface"] in external["surfaces"], name
        assert 0 <= asset["yawDegrees"] < 360 and isinstance(asset["collision"], bool), name
        targets = [d for d in asset["dimensionsM"] if d is not None]
        assert len(asset["dimensionsM"]) == 3 and targets and all(0 < d < 100 for d in targets)
        assert asset["colour"], name
        for measured in asset["colour"]:
            assert set(measured) == {"referenceSrgb", "renderSrgb", "source"}, name
            assert all(0 < c < 255 for c in measured["referenceSrgb"] + measured["renderSrgb"])
    return rows


def verified(row):
    """The bytes of a lock row's file, or of its zip member, after asserting their sha256."""
    data = (ROOT / row["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == row["sha256"], row["id"]
    if row["member"]:
        data = zipfile.ZipFile(io.BytesIO(data)).read(row["member"]["name"])
        assert hashlib.sha256(data).hexdigest() == row["member"]["sha256"], row["id"]
    return data


def linear(srgb):
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def encoded(lin):
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)


def gain(colour):
    """Linear per-channel gain that maps each measured master render mean onto its reference."""
    ratios = [
        linear(np.array(m["referenceSrgb"]) / 255) / linear(np.array(m["renderSrgb"]) / 255)
        for m in colour
    ]
    return np.prod(ratios, axis=0)


def scale(native, targets):
    """Uniform scale: geometric mean of the target ratios, each axis held within TOLERANCE."""
    axes = [i for i, t in enumerate(targets) if t is not None]
    s = float(np.exp(np.mean([np.log(targets[i] / native[i]) for i in axes])))
    for i in axes:
        assert abs(s * native[i] - targets[i]) <= TOLERANCE * targets[i], (
            i,
            s * native[i],
            targets,
        )
    return s


def vertices(mesh):
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def uv_density(mesh):
    """UV units per metre over the whole mesh and the area-weighted 5/95 % spread about it."""
    assert all(p.loop_total == 3 for p in mesh.polygons), "triangulated source expected"
    co = vertices(mesh)
    corner = np.empty(len(mesh.loops), np.int64)
    mesh.loops.foreach_get("vertex_index", corner)
    uv = np.empty(len(mesh.loops) * 2)
    mesh.uv_layers.active.data.foreach_get("uv", uv)
    p, t = co[corner].reshape(-1, 3, 3), uv.reshape(-1, 3, 2)
    area = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1) / 2
    e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    uv_area = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) / 2
    density = np.sqrt(uv_area.sum() / area.sum())
    ok = area > 0
    ratio = np.sqrt(uv_area[ok] / area[ok]) / density
    order = np.argsort(ratio)
    weight = np.cumsum(area[ok][order]) / area[ok].sum()
    spread = [float(ratio[order][np.searchsorted(weight, q)]) for q in (0.05, 0.95)]
    assert 1 - UV_SPREAD < spread[0] and spread[1] < 1 + UV_SPREAD, spread
    return float(density), spread


def maps(bsdf):
    """Pixel arrays feeding Base Color, Normal (through a Normal Map, None if unlinked) and
    Roughness/Metallic (through one Separate Color, a flat 4 px map of the value if unlinked)."""

    def source(socket):
        if not socket.is_linked:
            return None
        (link,) = socket.links
        node, channel = link.from_node, None
        if node.bl_idname == "ShaderNodeSeparateColor":
            channel = ("Red", "Green", "Blue").index(link.from_socket.name)
        if node.bl_idname in ("ShaderNodeSeparateColor", "ShaderNodeNormalMap"):
            (link,) = node.inputs["Color"].links
            node = link.from_node
        assert node.bl_idname == "ShaderNodeTexImage", node.bl_idname
        values = pixels(node.image)
        return values[..., :3] if channel is None else values[..., channel]

    found = {k: source(bsdf.inputs[k]) for k in ("Base Color", "Normal", "Roughness", "Metallic")}
    assert found["Base Color"] is not None, "base colour map required"
    flat = np.ones((4, 4))
    for key in ("Roughness", "Metallic"):
        found[key] = flat * bsdf.inputs[key].default_value if found[key] is None else found[key]
    return found


def detail(surface, entry, rows, output):
    """T_Detail<Surface>_D, linear grey of mean 0.5 whose log has standard deviation
    albedoStrength (base colour times 2 D), and its normal maps."""
    name = f"Detail{surface.title()}"
    loaded = {}
    for key in ("albedo", "normal"):
        row = rows[entry[key]]
        verified(row)
        img = image(ROOT / row["path"], colour=key == "albedo")
        # Unreal imports only power-of-two textures; scans can be a few pixels off square.
        assert abs(img.size[1] / img.size[0] - 1) < 0.01, (row["id"], tuple(img.size))
        img.scale(DETAIL_PX, DETAIL_PX)
        loaded[key] = pixels(img)[..., :3].astype(np.float64)
        bpy.data.images.remove(img)
    # 8-bit black has no logarithm; one sRGB level is the darkest measurable value.
    log = np.log(np.maximum(linear(loaded["albedo"]) @ LUMINANCE, 1 / 255 / 12.92))
    m = np.exp(entry["albedoStrength"] * (log - log.mean()) / log.std())
    d = 0.5 * m / m.mean()
    assert 0 < d.min() and d.max() < 1, (d.min(), d.max())
    n = loaded["normal"] * 2 - 1
    n[..., :2] *= entry["normalStrength"]
    gl = (n / np.linalg.norm(n, axis=2, keepdims=True) + 1) / 2
    return {
        "D": saved(f"T_{name}_D", np.repeat(d[..., None], 3, axis=2), output),
        "N": saved(f"T_{name}_N_GL", gl, output),
        "dx": directx(name, gl, output),
    }


def material(name, unique, shared, uv_scale):
    """MI_<name>: base colour times 2 D of the tiled detail, the unique and detail normals summed
    about the shading normal (Unreal blends them with BlendAngleCorrectedNormals), roughness and
    metallic from the ORM."""
    mat = bpy.data.materials.new(f"MI_{name}")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (uv_scale, uv_scale, 1)
    links.new(nodes.new("ShaderNodeTexCoord").outputs["UV"], mapping.inputs["Vector"])

    def texture(img, tiled=False):
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        if tiled:
            links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        return node.outputs["Color"]

    def vector(operation, *inputs):
        node = nodes.new("ShaderNodeVectorMath")
        node.operation = operation
        for socket, value in zip(node.inputs, inputs):
            links.new(value, socket)
        return node

    def normal(img, tiled):
        node = nodes.new("ShaderNodeNormalMap")
        links.new(texture(img, tiled), node.inputs["Color"])
        return node.outputs["Normal"]

    albedo = vector(
        "SCALE", vector("MULTIPLY", texture(unique["BC"]), texture(shared["D"], True)).outputs[0]
    )
    albedo.inputs["Scale"].default_value = 2.0
    links.new(albedo.outputs[0], bsdf.inputs["Base Color"])
    summed = vector("ADD", normal(unique["N"], False), normal(shared["N"], True)).outputs[0]
    shading = nodes.new("ShaderNodeNewGeometry").outputs["Normal"]
    links.new(
        vector("NORMALIZE", vector("SUBTRACT", summed, shading).outputs[0]).outputs[0],
        bsdf.inputs["Normal"],
    )
    channels = nodes.new("ShaderNodeSeparateColor")
    links.new(texture(unique["ORM"]), channels.inputs["Color"])
    links.new(channels.outputs["Green"], bsdf.inputs["Roughness"])
    links.new(channels.outputs["Blue"], bsdf.inputs["Metallic"])
    return mat


def hull(name, co):
    """UCX_<name>: convex hull of the vertices extreme along a Fibonacci sphere of directions."""
    i = np.arange(HULL_DIRECTIONS) + 0.5
    z = 1 - 2 * i / HULL_DIRECTIONS
    angle = np.pi * (3 - np.sqrt(5)) * i
    directions = np.stack([np.sqrt(1 - z**2) * np.cos(angle), np.sqrt(1 - z**2) * np.sin(angle), z])
    bm = bmesh.new()
    verts = [bm.verts.new(p) for p in co[np.unique(np.argmax(co @ directions, axis=0))]]
    result = bmesh.ops.convex_hull(bm, input=verts)
    inside = {*result["geom_interior"], *result["geom_unused"]}
    bmesh.ops.delete(bm, geom=list(inside), context="VERTS")
    mesh = bpy.data.meshes.new(f"UCX_{name}")
    bm.to_mesh(mesh)
    bm.free()
    mesh.uv_layers.new(name="UVMap")
    obj = bpy.data.objects.new(mesh.name, mesh)
    obj.hide_render = True
    bpy.context.scene.collection.objects.link(obj)
    return obj


def normalise(asset, entry, surfaces, rows, output):
    sm = names(asset)
    name = sm[3:].replace("_", "")
    staging = output / "source"
    staging.mkdir()
    glb = staging / rows[entry["source"]]["member"]["name"]
    glb.write_bytes(verified(rows[entry["source"]]))
    assert bpy.ops.import_scene.gltf(filepath=str(glb)) == {"FINISHED"}
    shutil.rmtree(staging)
    (obj,) = bpy.context.scene.objects
    mesh = obj.data
    (source_material,) = mesh.materials
    found = maps(source_material.node_tree.nodes["Principled BSDF"])
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)
    bpy.data.materials.remove(source_material)
    yaw = Matrix.Rotation(np.radians(entry["yawDegrees"]), 4, "Z")
    mesh.transform(yaw @ obj.matrix_world)
    obj.matrix_world = Matrix.Identity(4)
    native = np.ptp(vertices(mesh), axis=0)
    s = scale(native, entry["dimensionsM"])
    mesh.transform(Matrix.Scale(s, 4))
    obj.name = mesh.name = sm
    density, spread = uv_density(mesh)

    rgb = found["Base Color"].astype(np.float64)
    bc = encoded(np.clip(linear(rgb) * gain(entry["colour"]), 0, 1))
    rough, metal = found["Roughness"], found["Metallic"]
    gl = np.full((4, 4, 3), (0.5, 0.5, 1.0)) if found["Normal"] is None else found["Normal"]
    unique = {
        "BC": saved(f"T_{name}_BC", bc, output, colour=True),
        "ORM": saved(
            f"T_{name}_ORM", np.stack([np.ones_like(rough), rough, metal], axis=2), output
        ),
        "N": saved(f"T_{name}_N_GL", gl, output),
    }
    directx(name, gl, output)
    surface = surfaces[entry["surface"]]
    shared = detail(entry["surface"], surface, rows, output)
    size = shared["D"].size[0]
    uv_scale = 1 / (density * surface["tileM"])
    mesh.materials[0] = material(name, unique, shared, uv_scale)
    objects = [obj]
    if entry["collision"]:
        objects.append(hull(sm, vertices(mesh)))
    details = {
        "external": entry,
        "lockSha256": digest(LOCK),
        "externalSha256": digest(EXTERNAL),
        "nativeExtents": native.tolist(),
        "scale": s,
        "colourGain": gain(entry["colour"]).tolist(),
        "uvPerMetre": density,
        "uvDensitySpread": spread,
        "texelsPerMetre": {
            "unique": density * found["Base Color"].shape[1],
            "detail": size / surface["tileM"],
        },
        "detailUvScale": uv_scale,
        "detailTextures": sorted(img.name for img in shared.values()),
        "flatMaps": [k for k in ("N", "ORM") if unique[k].size[0] == 4],
        "collisionVertices": len(objects[-1].data.vertices) if entry["collision"] else 0,
    }
    spec = {
        "buriedM": 0.0,
        "dimensionsM": (s * native).tolist(),
        "toleranceM": 0.005,
        "textureSize": 4096,
        "collision": entry["collision"],
    }
    return spec, objects, details


def main():
    parser = argparse.ArgumentParser(prog="external.py")
    parser.add_argument("asset")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    external = json.loads(EXTERNAL.read_text())
    rows = check(external, json.loads(LOCK.read_text()))
    entry = external["assets"][args.asset]
    output = output_dir(args.output)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spec, objects, details = normalise(args.asset, entry, external["surfaces"], rows, output)
    report = finish(Path(__file__), args.asset, spec, objects, output) | {"details": details}
    (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
    print("BUILD " + json.dumps({k: report[k] for k in ("extentsM", "triangles")}), flush=True)


if __name__ == "__main__":
    main()

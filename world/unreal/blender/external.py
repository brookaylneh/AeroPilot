"""Normalise a pinned external mesh into a valley asset build.

blender -b --factory-startup --python world/unreal/blender/external.py -- <asset> --output <dir>
external.json gives each asset its source row in external.lock.json, the source mesh objects it
keeps (null for all) or the seeded clump of cards it cuts from a texture atlas, yaw to front +X,
target dimensions in metres with z above grade (null where no standard applies), surface class
and measured master colours per material class; every source sha256 is asserted. A lock row is
a file, a zip holding a GLB, or a directory: a PlantCatalog FBX export with its maps, a glTF
scene or an atlas. PlantCatalog plants stand on z = 0 with their roots below; other sources on
their lowest vertex. The kept objects are joined, turned and uniformly scaled to the targets
(each within 5 %), and each material's base colour is matched to the reference master by the
gains of its class: masked (alpha-tested cards) or opaque. Every material gets
T_<Name>[<Material>]_{BC,N,ORM} on its own UVs, with the material in the name when there are
several; a masked BC carries the opacity, and ORM takes the occlusion and roughness maps a
directory holds beside the material's normal map. With a surface the mesh also gets the shared
tiling detail T_Detail<Surface>_{D,N} sampled at UV0 times build.json's details.detailUvScale.
Source textures are resampled to powers of two of at most 4096 px. T_*_N is DirectX for Unreal;
the .blend uses T_*_N_GL. With collision, UCX_<SM name> holds a convex hull for Unreal.
"""

import argparse
import hashlib
import io
import json
import random
import re
import shutil
import sys
import zipfile
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from world.unreal.blender.common import directx, finish, image, pixels, saved  # noqa: E402
from world.unreal.blender.spec import ROOT, digest, output_dir  # noqa: E402
from world.unreal.import_assets import EXTERNAL, camel, names  # noqa: E402

LOCK = EXTERNAL.with_name("external.lock.json")

ROW = {"id", "path", "source", "licence", "attribution", "sha256", "member"}
DIRECTORY = {"id", "path", "source", "licence", "attribution", "files", "plant"}
PLANT = {"catalogue", "sha256", "preset", "seed", "options"}
SURFACE = {"albedo", "normal", "tileM", "albedoStrength", "normalStrength"}
ASSET = {
    "source",
    "objects",
    "cards",
    "reference",
    "category",
    "surface",
    "yawDegrees",
    "dimensionsM",
    "dimensionsSource",
    "collision",
    "colour",
}
CARDS = {"seed", "stems", "tiltDegrees"}
CLASSES = {"opaque", "masked"}
MESHES = (".glb", ".gltf", ".fbx")
# Directory maps that FBX and atlas materials leave unlinked, by lower-case file stem suffix.
CHANNELS = {
    "Occlusion": re.compile(r"_(ambientocclusion|ao)$"),
    "Roughness": re.compile(r"(_rough)?_roughness$"),
}
TOLERANCE = 0.05
# Area-weighted 5-95 % texel density spread allowed for tiling the detail layer on UV0.
UV_SPREAD = 0.15
HULL_DIRECTIONS = 128
DETAIL_PX = 2048
TEXTURE_SIZE = 4096
# Atlas stems are the islands of opacity above one half on this grid.
ISLAND_CELLS = 512
# Mean alpha of the outer 2 % of an atlas: an inverted map's border is opaque (PlantCatalog
# willow 0.92, Phragmites 0.99); the lodgepole needle atlas runs to its edges at 0.53.
BORDER, BORDER_ALPHA = 0.02, 0.6
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
        if "files" in row:
            assert set(row) == DIRECTORY and row["files"], row["id"]
            assert all(len(sha) == 64 for sha in row["files"].values()), row["id"]
            assert row["plant"] is None or set(row["plant"]) == PLANT, row["id"]
        else:
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
        row = rows[asset["source"]]
        found = row["files"] if "files" in row else [row["member"]["name"]]
        meshes = [f for f in found if f.endswith(MESHES)]
        assert len(meshes) == (asset["cards"] is None), (name, meshes)
        assert asset["cards"] is None or set(asset["cards"]) == CARDS, name
        assert asset["cards"] is None or asset["objects"] is None, name
        assert asset["category"].isalpha() and asset["category"].istitle(), name
        assert asset["surface"] is None or asset["surface"] in external["surfaces"], name
        assert 0 <= asset["yawDegrees"] < 360 and isinstance(asset["collision"], bool), name
        targets = [d for d in asset["dimensionsM"] if d is not None]
        assert len(asset["dimensionsM"]) == 3 and targets and all(0 < d < 100 for d in targets)
        assert asset["colour"] and set(asset["colour"]) <= CLASSES, name
        assert all(asset["colour"].values()), (name, "every class needs a measured colour record")
        for measured in sum(asset["colour"].values(), []):
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


def files(row):
    """Paths of a directory row's files, after asserting their sha256."""
    root = (ROOT / row["path"]).resolve()
    for name, sha in row["files"].items():
        assert digest(root / name) == sha, (row["id"], name)
    return {name: root / name for name in row["files"]}


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


def sized(img):
    """`img` resampled to power-of-two sides of at most TEXTURE_SIZE, so that Unreal streams its
    mips; a side below the cap moves by at most 1 %."""
    size = [min(TEXTURE_SIZE, 2 ** round(np.log2(n))) for n in img.size]
    assert all(n >= TEXTURE_SIZE or abs(s / n - 1) <= 0.01 for s, n in zip(size, img.size)), (
        img.name,
        tuple(img.size),
    )
    if list(img.size) != size:
        img.scale(*size)
    return img


def channels(paths):
    """A directory's unlinked maps by (channel, lower-case stem without the channel suffix)."""
    return {
        (channel, suffix.sub("", path.stem.lower())): path
        for path in paths
        for channel, suffix in CHANNELS.items()
        if suffix.search(path.stem.lower())
    }


def maps(material, extra, plant):
    """Pixel arrays of a source material and the directory maps it takes from `extra`: Base
    Color, Alpha (None when opaque), Normal (OpenGL, None when unlinked) and Occlusion, Roughness
    and Metallic (2D arrays or constants). A Separate Color picks a channel, a Math node scales
    (glTF factors), a Color output feeding a value takes its luminance, and the glTF output
    group gives Occlusion. A PlantCatalog (`plant`) Alpha map is a transparency map, white where
    clear, that Blender's FBX importer links uninverted. An unlinked occlusion or roughness map of
    `extra` belongs to the material when its stem without the channel suffix begins the stem of
    the material's normal or base colour map without its last word."""
    nodes = material.node_tree.nodes
    bsdf = nodes["Principled BSDF"]
    images = {}

    def source(socket):
        if not socket.is_linked:
            return None
        (link,) = socket.links
        node, channel, factor = link.from_node, None, 1.0
        if node.bl_idname == "ShaderNodeMath":
            assert node.operation == "MULTIPLY", node.operation
            (linked,) = [s for s in node.inputs[:2] if s.is_linked]
            (factor,) = [s.default_value for s in node.inputs[:2] if not s.is_linked]
            (link,) = linked.links
            node = link.from_node
        if node.bl_idname == "ShaderNodeSeparateColor":
            channel = ("Red", "Green", "Blue").index(link.from_socket.name)
        if node.bl_idname in ("ShaderNodeSeparateColor", "ShaderNodeNormalMap"):
            (link,) = node.inputs["Color"].links
            node = link.from_node
        assert node.bl_idname == "ShaderNodeTexImage", node.bl_idname
        images[socket.name] = node.image
        values = pixels(sized(node.image))
        if link.from_socket.name == "Alpha":
            return values[..., 3]
        if channel is None and socket.type == "VALUE":
            value = values[..., :3] @ LUMINANCE
            return 1 - value if plant and socket.name == "Alpha" else value
        return factor * (values[..., :3] if channel is None else values[..., channel])

    keys = ("Base Color", "Alpha", "Normal", "Roughness", "Metallic")
    found = {key: source(bsdf.inputs[key]) for key in keys}
    assert found["Base Color"] is not None, "base colour map required"
    groups = [n for n in nodes if n.bl_idname == "ShaderNodeGroup"]
    assert all(n.node_tree.name == "glTF Material Output" for n in groups), material.name
    (found["Occlusion"],) = [source(n.inputs["Occlusion"]) for n in groups] or [None]
    stems = [
        Path(images[key].filepath).stem.lower().rsplit("_", 1)[0]
        for key in ("Base Color", "Normal")
        if key in images
    ]
    used = {
        (key, path)
        for (key, base), path in extra.items()
        for stem in stems
        if stem.startswith(base)
    }
    for key, path in used:
        assert found[key] is None, (material.name, key)
        img = image(path)
        found[key] = pixels(sized(img))[..., 0]
        bpy.data.images.remove(img)
    found["Occlusion"] = 1.0 if found["Occlusion"] is None else found["Occlusion"]
    for key in ("Roughness", "Metallic"):
        found[key] = bsdf.inputs[key].default_value if found[key] is None else found[key]
    return found, [path for _, path in used]


def islands(opacity):
    """UV boxes (u0, v0, u1, v1) and base u of the 8-connected islands of opacity above one
    half on an ISLAND_CELLS grid, dropping those under 1 % of the largest island's cells."""
    n = opacity.shape[0] // ISLAND_CELLS
    assert opacity.shape == (ISLAND_CELLS * n,) * 2, opacity.shape
    mask = opacity.reshape(ISLAND_CELLS, n, ISLAND_CELLS, n).max(axis=(1, 3)) > 0.5
    label = np.where(mask, np.arange(mask.size).reshape(mask.shape), -1)
    while True:
        padded = np.pad(label, 1, constant_values=-1)
        shifted = [
            padded[1 + dy : 1 + dy + ISLAND_CELLS, 1 + dx : 1 + dx + ISLAND_CELLS]
            for dy in (-1, 0, 1)
            for dx in (-1, 0, 1)
        ]
        grown = np.where(mask, np.max(shifted, axis=0), -1)
        if (grown == label).all():
            break
        label = grown
    found = []
    for value in np.unique(label[mask]):
        rows, columns = np.nonzero(label == value)
        base = columns[rows == rows.min()].mean() + 0.5
        box = (columns.min(), rows.min(), columns.max() + 1, rows.max() + 1, base)
        found.append((len(rows), [c / ISLAND_CELLS for c in box]))
    largest = max(cells for cells, _ in found)
    return [box for cells, box in found if cells >= 0.01 * largest]


def cards(paths, spec):
    """A clump of spec["stems"] cards, each an atlas stem island with its base at the origin,
    scaled 0.5-1, turned at random and leaning up to tiltDegrees; its material links the atlas
    albedo, opacity and normal as an FBX import does."""
    atlas = {}
    for key in ("albedo", "opacity", "normal"):
        (path,) = [p for p in paths.values() if p.stem.lower().endswith(f"_{key}")]
        atlas[key] = image(path, colour=key == "albedo")
    boxes = islands(pixels(sized(atlas["opacity"]))[..., 0])
    rng = random.Random(spec["seed"])
    verts, faces, uvs = [], [], []
    for _ in range(spec["stems"]):
        u0, v0, u1, v1, base = rng.choice(boxes)
        size = rng.uniform(0.5, 1.0)
        lean = Matrix.Rotation(np.radians(spec["tiltDegrees"]) * np.sqrt(rng.random()), 4, "X")
        turn = Matrix.Rotation(rng.uniform(0, 2 * np.pi), 4, "Z") @ lean
        faces.append(tuple(range(len(verts), len(verts) + 4)))
        for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1)):
            verts.append(turn @ Vector(((u - base) * size, 0, (v - v0) * size)))
            uvs.append((u, v))
    mesh = bpy.data.meshes.new("cards")
    mesh.from_pydata(verts, [], faces)
    mesh.uv_layers.new(name="UVMap").data.foreach_set("uv", np.ravel(uvs))
    mat = bpy.data.materials.new("cards")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf, normal = nodes["Principled BSDF"], nodes.new("ShaderNodeNormalMap")
    for key, socket in (("albedo", "Base Color"), ("opacity", "Alpha"), ("normal", "Normal")):
        node = nodes.new("ShaderNodeTexImage")
        node.image = atlas[key]
        target = normal.inputs["Color"] if key == "normal" else bsdf.inputs[socket]
        links.new(node.outputs["Color"], target)
    links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
    mesh.materials.append(mat)
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("cards", mesh))


def imported(path):
    operator = bpy.ops.wm.fbx_import if path.suffix == ".fbx" else bpy.ops.import_scene.gltf
    assert operator(filepath=str(path)) == {"FINISHED"}, path


def load(entry, row, output):
    """The kept source mesh objects joined into one object with no parent and identity world
    matrix, their former world matrix, and a directory row's files (none for a zip)."""
    paths = {}
    if "files" in row:
        paths = files(row)
        if entry["cards"]:
            cards(paths, entry["cards"])
        else:
            (path,) = [p for p in paths.values() if p.suffix in MESHES]
            imported(path)
        for img in bpy.data.images:
            assert Path(bpy.path.abspath(img.filepath)).resolve() in paths.values(), img.filepath
    else:
        staging = output / "source"
        staging.mkdir()
        glb = staging / row["member"]["name"]
        glb.write_bytes(verified(row))
        imported(glb)
        shutil.rmtree(staging)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    kept = meshes if entry["objects"] is None else [bpy.data.objects[n] for n in entry["objects"]]
    assert kept and all(o.type == "MESH" and o.data.users == 1 for o in kept), kept
    if len(kept) > 1:
        # object.join cancels on a single object.
        with bpy.context.temp_override(active_object=kept[0], selected_editable_objects=kept):
            assert bpy.ops.object.join() == {"FINISHED"}
    obj = kept[0]
    world = obj.matrix_world.copy()
    for other in [o for o in bpy.data.objects if o != obj]:
        bpy.data.objects.remove(other)
    assert obj.parent is None
    obj.matrix_world = Matrix.Identity(4)
    return obj, world, paths


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


def material(name, unique, shared, uv_scale, masked):
    """MI_<name>: base colour, times 2 D of the tiled detail when `shared` holds it, the unique
    and detail normals summed about the shading normal (Unreal blends them with
    BlendAngleCorrectedNormals), roughness and metallic from the ORM, and for a masked material
    opacity from the base colour alpha."""
    mat = bpy.data.materials.new(f"MI_{name}")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (uv_scale, uv_scale, 1)
    links.new(nodes.new("ShaderNodeTexCoord").outputs["UV"], mapping.inputs["Vector"])

    def texture(img, tiled=False, output="Color"):
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        if tiled:
            links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        return node.outputs[output]

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

    if shared is None:
        links.new(texture(unique["BC"]), bsdf.inputs["Base Color"])
        links.new(normal(unique["N"], False), bsdf.inputs["Normal"])
    else:
        albedo = vector(
            "SCALE",
            vector("MULTIPLY", texture(unique["BC"]), texture(shared["D"], True)).outputs[0],
        )
        albedo.inputs["Scale"].default_value = 2.0
        links.new(albedo.outputs[0], bsdf.inputs["Base Color"])
        summed = vector("ADD", normal(unique["N"], False), normal(shared["N"], True)).outputs[0]
        shading = nodes.new("ShaderNodeNewGeometry").outputs["Normal"]
        links.new(
            vector("NORMALIZE", vector("SUBTRACT", summed, shading).outputs[0]).outputs[0],
            bsdf.inputs["Normal"],
        )
    if masked:
        links.new(texture(unique["BC"], output="Alpha"), bsdf.inputs["Alpha"])
    channels = nodes.new("ShaderNodeSeparateColor")
    links.new(texture(unique["ORM"]), channels.inputs["Color"])
    links.new(channels.outputs["Green"], bsdf.inputs["Roughness"])
    links.new(channels.outputs["Blue"], bsdf.inputs["Metallic"])
    return mat


def baked(label, found, gains, output):
    """T_<label>_{BC,ORM,N_GL} and the DirectX T_<label>_N from a source material's maps, the
    base colour times the linear `gains`; a masked base colour keeps the opacity as alpha."""
    rgb = encoded(np.clip(linear(found["Base Color"].astype(np.float64)) * gains, 0, 1))
    alpha = found["Alpha"]
    if alpha is not None:
        k = max(1, round(BORDER * min(alpha.shape)))
        ring = np.concatenate([alpha[:k], alpha[-k:], alpha[:, :k].T, alpha[:, -k:].T], axis=1)
        assert alpha.shape == rgb.shape[:2] and ring.mean() < BORDER_ALPHA, (label, ring.mean())
        rgb = np.concatenate([rgb, alpha[..., None]], axis=2)
    orm = [found[k] for k in ("Occlusion", "Roughness", "Metallic")]
    # Constant channels make a flat 4 px map.
    (shape,) = {np.shape(c) for c in orm if np.ndim(c)} or {(4, 4)}
    gl = np.full((4, 4, 3), (0.5, 0.5, 1.0)) if found["Normal"] is None else found["Normal"]
    directx(label, gl, output)
    return {
        "BC": saved(f"T_{label}_BC", rgb, output, colour=True, alpha=alpha is not None),
        "ORM": saved(
            f"T_{label}_ORM", np.stack([np.broadcast_to(c, shape) for c in orm], axis=2), output
        ),
        "N": saved(f"T_{label}_N_GL", gl, output),
    }


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
    row = rows[entry["source"]]
    obj, world, paths = load(entry, row, output)
    sources = list(bpy.data.images)
    mesh = obj.data
    mesh.transform(Matrix.Rotation(np.radians(entry["yawDegrees"]), 4, "Z") @ world)
    co = vertices(mesh)
    plant = "files" in row and row["plant"] is not None
    grade = 0.0 if plant else co[:, 2].min()
    native = np.ptp(co, axis=0)
    s = scale([*native[:2], co[:, 2].max() - grade], entry["dimensionsM"])
    mesh.transform(Matrix.Scale(s, 4))
    obj.name = mesh.name = sm

    details = {}
    shared, uv_scale = None, 1.0
    if entry["surface"]:
        density, spread = uv_density(mesh)
        surface = surfaces[entry["surface"]]
        shared = detail(entry["surface"], surface, rows, output)
        uv_scale = 1 / (density * surface["tileM"])
        details = {
            "uvPerMetre": density,
            "uvDensitySpread": spread,
            "texelsPerMetre": {"detail": shared["D"].size[0] / surface["tileM"]},
            "detailUvScale": uv_scale,
        }
    originals = list(mesh.materials)
    slots = np.empty(len(mesh.polygons), np.int64)
    mesh.polygons.foreach_get("material_index", slots)
    faces = np.bincount(slots, minlength=len(originals))
    assert faces.all() and len(set(originals)) == len(originals), (originals, faces)
    labels = [name + camel(m.name) for m in originals] if len(originals) > 1 else [name]
    assert len(set(labels)) == len(labels), labels
    extra = channels(paths.values())
    taken, report = [], {}
    for slot, (source, label) in enumerate(zip(originals, labels)):
        found, used = maps(source, extra, plant)
        taken += used
        kind = "opaque" if found["Alpha"] is None else "masked"
        unique = baked(label, found, gain(entry["colour"][kind]), output)
        mesh.materials[slot] = material(label, unique, shared, uv_scale, kind == "masked")
        report[label] = {
            "source": source.name,
            "class": kind,
            "colourGain": gain(entry["colour"][kind]).tolist(),
            "texelsPerMetre": details["uvPerMetre"] * unique["BC"].size[0] if shared else None,
            "flatMaps": [k for k in ("N", "ORM") if unique[k].size[0] == 4],
        }
        bpy.data.materials.remove(source)
    assert set(taken) == set(extra.values()), set(extra.values()) - set(taken)
    assert set(entry["colour"]) == {m["class"] for m in report.values()}, entry["colour"].keys()
    for img in sources:
        bpy.data.images.remove(img)
    objects = [obj]
    if entry["collision"]:
        objects.append(hull(sm, vertices(mesh)))
    details |= {
        "external": entry,
        "lockSha256": digest(LOCK),
        "externalSha256": digest(EXTERNAL),
        "nativeExtents": native.tolist(),
        "scale": s,
        "materials": report,
        "detailTextures": sorted(img.name for img in shared.values()) if shared else [],
        "collisionVertices": len(objects[-1].data.vertices) if entry["collision"] else 0,
    }
    spec = {
        "buriedM": s * (grade - co[:, 2].min()),
        "dimensionsM": (s * native).tolist(),
        "toleranceM": 0.005,
        "textureSize": TEXTURE_SIZE,
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

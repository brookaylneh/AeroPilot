"""Build valley granite rocks: seeded 1 cm sculpts for Nanite with unique baked PBR textures.

blender -b --factory-startup --python world/unreal/blender/rocks.py -- <asset> --output <dir>
Writes <asset>.blend, <asset>.glb, T_<Name>_{BC,N,ORM}.png and build.json into a new .local
directory. T_<Name>_N is DirectX (green down) for Unreal; the .blend and GLB use the OpenGL
T_<Name>_N_GL that glTF requires. UCX_SM_<Name> is the convex hull that Unreal Interchange
imports as collision for SM_<Name>.
"""

import math
import sys
import time
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector, noise

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from world.unreal.blender.common import add, emit, gpu, lin, main, pixels, principled  # noqa: E402
from world.unreal.blender.spec import load  # noqa: E402

STEP = 0.015
# Lattice box half-extents and tangent warp: near-uniform 1 cm spacing, the base face buried.
BOX = np.array([1.6, 1.85, 1.85])
WARP = 0.9
TAPER = 0.35
GAP = 2
TEXELS = 512
BURIED_TEXEL_SCALE = 0.25
HULL_STEP = 80
SAMPLES = {"DIFFUSE": 16, "ROUGHNESS": 16, "NORMAL": 16, "AO": 128}
AO_DISTANCE_M = 0.5


def smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def unit(v):
    return np.asarray(v, float) / np.linalg.norm(v)


def lattice(cells):
    """Outward quads over an integer box lattice surface, their box face and per-corner chart
    coordinates (lattice units, orientation-preserving seen from outside)."""
    points, quads, faces, local, count = [], [], [], [], 0
    for axis in range(3):
        b, c = (axis + 1) % 3, (axis + 2) % 3
        for side in (0, cells[axis]):
            i, j = np.meshgrid(np.arange(cells[b] + 1), np.arange(cells[c] + 1), indexing="ij")
            p = np.empty((*i.shape, 3), int)
            p[..., axis], p[..., b], p[..., c] = side, i, j
            k = count + np.arange(i.size).reshape(i.shape)
            q = np.stack([k[:-1, :-1], k[1:, :-1], k[1:, 1:], k[:-1, 1:]], -1).reshape(-1, 4)
            q = q if side else q[:, ::-1]
            corner = p.reshape(-1, 3)[q - count]
            u = corner[..., b] if side else cells[b] - corner[..., b]
            local.append(np.stack([u, corner[..., c]], -1))
            quads.append(q)
            faces.append(np.full(len(q), 2 * axis + (side > 0)))
            points.append(p.reshape(-1, 3))
            count += i.size
    unique, inverse = np.unique(np.concatenate(points), axis=0, return_inverse=True)
    quads = inverse.ravel()[np.concatenate(quads)]
    return unique, quads, np.concatenate(faces), np.concatenate(local)


def atlas(local, faces, cells):
    """Pack the face charts in rows: X faces; Y faces; the top in two halves and the buried base."""
    uv = local.astype(float)
    k = cells[0] // 2
    top = faces == 5
    first = top & (local[..., 0].min(axis=1) < k)
    second = top & ~first
    uv[top] = np.stack(
        [local[top][..., 1], np.where(first[top, None], k, cells[0]) - local[top][..., 0]], -1
    )
    uv[faces == 4] *= BURIED_TEXEL_SCALE
    size = {f: cells[[(f // 2 + 1) % 3, (f // 2 + 2) % 3]] for f in range(6)}
    rows = [
        [(faces == 1, size[1]), (faces == 0, size[0])],
        [(faces == 3, size[3]), (faces == 2, size[2])],
        [
            (first, (cells[1], k)),
            (second, (cells[1], cells[0] - k)),
            (faces == 4, size[4] * BURIED_TEXEL_SCALE),
        ],
    ]
    y, width = 0, 0
    for row in rows:
        x = 0
        for mask, (w, h) in row:
            uv[mask] += (x, y)
            x += w + GAP
        width = max(width, x - GAP)
        y += max(h for _, (_, h) in row) + GAP
    return uv / max(width, y - GAP)


def radius(u, axes, power, planes, sharpness):
    """Radius along unit rows `u` of a superellipsoid softly cut by (normal, offset) planes."""
    terms = [np.sum(np.abs(u / axes) ** power, axis=1) ** (1 / power)]
    terms += [np.maximum(u @ n, 0) / d for n, d in planes]
    return np.sum(np.stack(terms) ** sharpness, axis=0) ** (-1 / sharpness)


def normals(co, quads):
    face = np.cross(co[quads[:, 2]] - co[quads[:, 0]], co[quads[:, 3]] - co[quads[:, 1]])
    out = np.zeros_like(co)
    for k in range(4):
        np.add.at(out, quads[:, k], face)
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def fractal(points, scale, octaves, seed):
    """Unit-variance Perlin fractal sampled at `points` metres with features of 1/scale m."""
    noise.seed_set(seed)
    values = np.fromiter(
        (noise.fractal(Vector(p), 1.0, 2.0, octaves) for p in (points * scale).tolist()),
        float,
        len(points),
    )
    return values / values.std()


def terraces(points, seeds, depths, edge):
    """Exfoliation shells: each Voronoi cell of `seeds` lies `depths` deep, with soft steps."""
    out = np.empty(len(points))
    for s in range(0, len(points), 40000):
        d = np.linalg.norm(points[s : s + 40000, None] - seeds[None], axis=2)
        near = np.argpartition(d, 3, axis=1)[:, :4]
        dn = np.take_along_axis(d, near, 1)
        w = np.exp(-(dn - dn.min(axis=1, keepdims=True)) / edge)
        out[s : s + 40000] = (w * depths[near]).sum(axis=1) / w.sum(axis=1)
    return out


def fracture(points, origin, normal, warp, fade, depth):
    """Inward displacement and mask of a weathered joint: a groove with rounded lips and a step."""
    d = (points - origin) @ unit(normal) + warp
    groove = np.exp(-((d / 0.006) ** 2)) + 0.15 * np.exp(-((d / 0.04) ** 2))
    step = 0.25 / (1 + np.exp(-d / 0.008))
    return -depth * fade * (groove + step), fade * np.exp(-((d / 0.03) ** 2))


def granite():
    """Procedural Sierra granite, the bake source: mineral mosaic, weathering, lichen, relief."""
    mat = bpy.data.materials.new("Granite source")
    tree = mat.node_tree

    def node(kind, out, inputs=(), **attrs):
        n = tree.nodes.new(kind)
        for key, value in attrs.items():
            setattr(n, key, value)
        for key, value in inputs:
            socket = next(s for s in n.inputs if s.enabled and s.identifier == key)
            if isinstance(value, bpy.types.NodeSocket):
                tree.links.new(value, socket)
            else:
                socket.default_value = value
        return next(s for s in n.outputs if s.enabled and s.identifier == out)

    def math(op, *values):
        inputs = zip(("Value", "Value_001", "Value_002"), values)
        return node("ShaderNodeMath", "Value", inputs, operation=op)

    def band(x, lo, hi):
        inputs = (("Value", x), ("From Min", lo), ("From Max", hi))
        return node("ShaderNodeMapRange", "Result", inputs, interpolation_type="SMOOTHSTEP")

    def mix(f, a, b):
        b = (*lin(b), 1) if isinstance(b, tuple) else b
        inputs = (("Factor_Float", f), ("A_Color", a), ("B_Color", b))
        return node("ShaderNodeMix", "Result_Color", inputs, data_type="RGBA")

    def fbm(p, scale, detail, out="Fac"):
        return node(
            "ShaderNodeTexNoise", out, (("Vector", p), ("Scale", scale), ("Detail", detail))
        )

    def cells(p, scale):
        """Distance to the nearest Voronoi feature and a uniform random id per cell."""
        inputs = (("Vector", p), ("Scale", scale))
        position = node("ShaderNodeTexVoronoi", "Position", inputs)
        cell = node("ShaderNodeTexWhiteNoise", "Value", (("Vector", position),))
        return node("ShaderNodeTexVoronoi", "Distance", inputs), cell

    def ramp(f, stops, constant=False, grey=False):
        out = node("ShaderNodeValToRGB", "Color", (("Fac", f),))
        r = out.node.color_ramp
        r.interpolation = "CONSTANT" if constant else "LINEAR"
        while len(r.elements) < len(stops):
            r.elements.new(0.5)
        for element, (position, value) in zip(r.elements, stops):
            element.position = position
            element.color = (value, value, value, 1) if grey else (*lin(value), 1)
        assert [e.position for e in r.elements] == [np.float32(s[0]) for s in stops], stops
        return out

    p = node("ShaderNodeTexCoord", "Object")
    nz = node("ShaderNodeSeparateXYZ", "Z", (("Vector", node("ShaderNodeNewGeometry", "Normal")),))
    z = node("ShaderNodeSeparateXYZ", "Z", (("Vector", p),))
    crack = node("ShaderNodeAttribute", "Fac", attribute_name="crack")
    fresh = node("ShaderNodeAttribute", "Fac", attribute_name="fresh")
    warp = node(
        "ShaderNodeVectorMath",
        "Vector",
        (("Vector", fbm(p, 45, 3, "Color")), ("Vector_001", (0.006,) * 3), ("Vector_002", p)),
        operation="MULTIPLY_ADD",
    )
    # Visible mosaic of 1-3 cm biotite clots in feldspar, over 3-4 mm mineral grains.
    clot_d, clot = cells(warp, 45)
    grain_d, grain = cells(warp, 260)
    minerals = ((0, (28, 26, 25)), (0.16, (64, 63, 61)), (0.22, (118, 116, 112)))
    minerals += ((0.36, (184, 172, 158)), (0.84, (190, 168, 148)))
    fresh_rock = mix(0.5, ramp(clot, minerals, True), ramp(grain, minerals, True))
    rock = mix(band(fbm(p, 11, 3), 0.55, 0.72), fresh_rock, (112, 108, 102))
    rock = mix(math("MULTIPLY", band(fbm(p, 5, 4), 0.45, 0.62), 0.6), rock, (100, 90, 78))
    tone = ramp(fbm(p, 0.8, 6), ((0.35, (170, 152, 132)), (0.65, (140, 138, 132))))
    rock = mix(math("MULTIPLY", fresh, 0.6), mix(0.3, rock, tone), fresh_rock)
    streak_p = node(
        "ShaderNodeVectorMath",
        "Vector",
        (("Vector", p), ("Vector_001", (7, 7, 0.6))),
        operation="MULTIPLY",
    )
    steep = band(math("ABSOLUTE", nz), 0.65, 0.15)
    rock = mix(
        math("MULTIPLY", band(fbm(streak_p, 1, 3), 0.52, 0.7), math("MULTIPLY", steep, 0.4)),
        rock,
        (96, 92, 86),
    )
    dirt = band(math("MULTIPLY_ADD", fbm(p, 3, 3), 0.3, z), 0.45, 0.15)
    rock = mix(math("MULTIPLY", dirt, 0.7), rock, (84, 70, 54))
    rock = mix(math("MULTIPLY", crack, 0.35), rock, (70, 62, 54))
    # Lichen: areolate crusts with lobed margins on exposed rock, absent from fresh spalls,
    # joints and the soil-splashed base.
    cover = math("MULTIPLY_ADD", nz, 0.25, fbm(p, 1.3, 5))
    for mask, weight in ((crack, -0.4), (fresh, -0.25), (dirt, -0.35)):
        cover = math("MULTIPLY_ADD", mask, weight, cover)
    patch = band(cover, 0.45, 0.75)
    lichen = band(math("MULTIPLY_ADD", patch, 0.25, fbm(p, 18, 5)), 0.72, 0.75)
    areole = node(
        "ShaderNodeTexVoronoi",
        "Distance",
        (("Vector", p), ("Scale", 350)),
        feature="DISTANCE_TO_EDGE",
    )
    species = ((0.3, (128, 100, 52)), (0.45, (150, 120, 62)), (0.58, (160, 140, 84)))
    species = ramp(fbm(p, 0.9, 2), species + ((0.7, (160, 160, 146)),))
    species = mix(math("MULTIPLY", band(fbm(p, 140, 2), 0.4, 0.75), 0.5), species, (104, 96, 60))
    species = mix(math("MULTIPLY", band(areole, 0.08, 0.0), 0.6), species, (70, 64, 46))
    rock = mix(math("MULTIPLY", lichen, 0.75), rock, species)
    dot_d, _ = cells(p, 70)
    dots = math(
        "MULTIPLY",
        band(dot_d, 0.17, 0.11),
        band(math("MULTIPLY_ADD", nz, 0.15, fbm(p, 2.2, 3)), 0.6, 0.68),
    )
    rock = mix(math("MULTIPLY", dots, 0.85), rock, (40, 38, 34))
    rough = ramp(
        clot, ((0, 0.58), (0.13, 0.48), (0.24, 0.5), (0.45, 0.74), (0.87, 0.72)), True, True
    )
    matte = math("MAXIMUM", lichen, math("MULTIPLY", dirt, 0.6))
    rough = math("MULTIPLY_ADD", math("SUBTRACT", 0.9, rough), matte, rough)
    relief = ramp(
        grain, ((0, 0.1), (0.13, 0.8), (0.24, 0.7), (0.45, 0.5), (0.87, 0.55)), True, True
    )
    height = math("MULTIPLY_ADD", fbm(p, 330, 2), 0.7, relief)
    height = math("MULTIPLY_ADD", fbm(p, 32, 4), 1.6, height)
    height = math("MULTIPLY_ADD", math("MULTIPLY", lichen, band(areole, 0.0, 0.1)), 0.5, height)
    bump = (("Height", height), ("Distance", 0.002), ("Strength", 1.0))
    bsdf = tree.nodes["Principled BSDF"]
    tree.links.new(rock, bsdf.inputs["Base Color"])
    tree.links.new(rough, bsdf.inputs["Roughness"])
    tree.links.new(node("ShaderNodeBump", "Normal", bump), bsdf.inputs["Normal"])
    target = tree.nodes.new("ShaderNodeTexImage")
    tree.nodes.active = target
    return mat, target


def texels(co, quads, uv, size, above):
    """Area-weighted texel density in px/m over the faces marked `above`."""

    p, t = co[quads], uv.astype(float)
    surface = 0.5 * np.linalg.norm(np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 1]), axis=1)
    a, b = t[:, 2] - t[:, 0], t[:, 3] - t[:, 1]
    texture = 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
    surface, texture = surface[above], texture[above]
    density = size * np.sqrt(texture / surface)
    order = np.argsort(density)
    cumulative = np.cumsum(surface[order]) / surface.sum()
    pick = {
        f"p{q}": float(density[order][np.searchsorted(cumulative, q / 100)]) for q in (1, 5, 50)
    }
    return pick | {"min": float(density.min()), "aboveGradeAreaM2": float(surface.sum())}


def store(img, output):
    img.filepath_raw = str(output / f"{img.name}.png")
    img.file_format = "PNG"
    img.save()


def bake(obj, target, name, size, kind, colour, output=None, **options):
    img = bpy.data.images.new(name, size, size, alpha=False, is_data=not colour)
    target.image = img
    bpy.context.scene.cycles.samples = SAMPLES[kind]
    started = time.monotonic()
    result = bpy.ops.object.bake(
        type=kind, margin=8, margin_type="EXTEND", use_clear=True, **options
    )
    assert result == {"FINISHED"}, kind
    print(f"TIME bake {kind} {time.monotonic() - started:.1f}", flush=True)
    if output:
        store(img, output)
    return img


def derived(name, values, size, output):
    img = bpy.data.images.new(name, size, size, alpha=False, is_data=True)
    img.pixels.foreach_set(values.ravel())
    store(img, output)
    return img


def textures(obj, name, size, output):
    """Bake BC, N and ORM (AO, roughness, metal) and give `obj` the material that ships."""
    source, target = obj.data.materials[0], obj.data.materials[0].node_tree.nodes.active
    scene = bpy.context.scene
    devices = gpu(scene)
    scene.world = bpy.data.worlds.new("Bake")
    scene.world.light_settings.distance = AO_DISTANCE_M
    bc = bake(obj, target, f"T_{name}_BC", size, "DIFFUSE", True, output, pass_filter={"COLOR"})
    gl = bake(obj, target, f"T_{name}_N_GL", size, "NORMAL", False, output, normal_space="TANGENT")
    rough = bake(obj, target, "roughness", size, "ROUGHNESS", False)
    ao = bake(obj, target, "ao", size, "AO", False)
    orm = pixels(ao).copy()
    orm[..., 1], orm[..., 2] = pixels(rough)[..., 0], 0
    orm = derived(f"T_{name}_ORM", orm, size, output)
    dx = pixels(gl).copy()
    dx[..., 1] = 1 - dx[..., 1]
    derived(f"T_{name}_N", dx, size, output)
    for img in (rough, ao):
        bpy.data.images.remove(img)
    bpy.data.worlds.remove(scene.world)
    mat = principled(f"MI_{name}", base_image=bc, rough=0.5, normal_image=gl)
    tree = mat.node_tree
    channels = tree.nodes.new("ShaderNodeSeparateColor")
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = orm
    tree.links.new(texture.outputs["Color"], channels.inputs["Color"])
    tree.links.new(channels.outputs["Green"], tree.nodes["Principled BSDF"].inputs["Roughness"])
    obj.data.materials[0] = mat
    bpy.data.materials.remove(source)
    return mat, devices


def hull(co, points, name, mat):
    """Convex collision hull of the sculpt's vertices on a coarse sub-lattice."""
    bm = bmesh.new()
    for p in co[np.all((points % HULL_STEP == 0) | (points == points.max(axis=0)), axis=1)]:
        bm.verts.new(p)
    bmesh.ops.convex_hull(bm, input=bm.verts[:])
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.loops.layers.uv.new("UVMap")
    parts = {}
    add(parts, f"UCX_SM_{name}", bm)
    (obj,) = emit(parts, {f"UCX_SM_{name}": mat})
    obj.hide_render = True
    assert len(obj.data.vertices) <= 255, len(obj.data.vertices)
    return obj


def boulder(rng, output):
    """Glacial erratic: rounded superellipsoid with spall facets, two joints and exfoliation."""
    started = time.monotonic()
    asset = "granite-hero-boulder"
    name = "GraniteHeroBoulder"
    spec = load("rocks")[asset]
    size, buried = np.array(spec["dimensionsM"]), spec["buriedM"]
    axes = np.array([1.6, 1.85, 1.5])
    planes = [((0, 0, -1), 0.8 * axes[2]), (unit((0.25, 0.2, 1)), 0.85 * axes[2])]
    for _ in range(14):
        phi, theta = rng.uniform(0, 2 * math.pi), rng.uniform(-0.2, 0.9)
        n = np.array(
            [math.cos(phi) * math.cos(theta), math.sin(phi) * math.cos(theta), math.sin(theta)]
        )
        planes.append((n, np.linalg.norm(axes * n) * rng.uniform(0.84, 0.94)))
    cells = np.ceil(2 * BOX / STEP).astype(int)
    points, quads, faces, local = lattice(cells)
    q = np.tan(WARP * (points / cells * 2 - 1)) / np.tan(WARP) * BOX
    u = q / np.linalg.norm(q, axis=1, keepdims=True)
    co = u * radius(u, axes, 2.2, planes, 9)[:, None]
    co[:, :2] *= 1 - TAPER * smooth(co[:, 2:], 0.4 * axes[2], -axes[2])
    n = normals(co, quads)
    assert (np.einsum("ij,ij->i", n, co) > 0).all()
    seeds = [rng.randrange(2**31) for _ in range(7)]
    warp = co + 0.05 * np.stack([fractal(co, 3, 3, s) for s in seeds[:3]], axis=1)
    centres = co[rng.sample(range(len(co)), 150)]
    depths = np.array([0 if rng.random() < 0.45 else rng.uniform(0.008, 0.035) for _ in centres])
    shells = terraces(warp, centres, depths, 0.012)
    relief = 0.035 * fractal(co, 0.9, 4, seeds[3]) + 0.010 * fractal(co, 4, 4, seeds[4])
    relief -= shells
    # A smooth buried base keeps the lowest vertex, and so grade, on the bottom plane.
    relief *= smooth(co[:, 2], co[:, 2].min(), co[:, 2].min() + 0.4)
    joint = 0.05 * fractal(co, 1.5, 3, seeds[5]) + 0.01 * fractal(co, 8, 2, seeds[6])
    cut_a, mask_a = fracture(
        co, (0.9, 0.75, 0), (-0.8, 1, 0.08), joint, smooth(co[:, 0], -0.6, 0.0), 0.045
    )
    fade_b = smooth(co[:, 1], 0.4, 0.9) * smooth(co[:, 2], 0.5, 1.0)
    cut_b, mask_b = fracture(co, (-0.7, 0, 0), (1, 0.15, -0.1), joint, fade_b, 0.03)
    co = co + n * (relief + cut_a + cut_b)[:, None]
    low, high = co.min(axis=0), co.max(axis=0)
    co = (co - (low + high) / 2) * size / (high - low)
    co[:, 2] += size[2] / 2 - buried
    print(f"TIME sculpt {time.monotonic() - started:.1f}", flush=True)
    uv = atlas(local, faces, cells)
    above = co[quads].mean(axis=1)[:, 2] > 0
    assert not above[faces == 4].any(), co[quads[faces == 4]].mean(axis=1)[:, 2].max()
    crack = np.maximum(mask_a, mask_b)
    # Joint walls are steep and dark; their stretched texels are measured, not asserted.
    density = {
        "outsideJoints": texels(
            co, quads, uv, spec["textureSize"], above & (crack[quads].max(axis=1) < 0.05)
        ),
        "all": texels(co, quads, uv, spec["textureSize"], above),
    }
    assert density["outsideJoints"]["p5"] >= TEXELS, density
    mat, target = granite()
    parts = {f"SM_{name}": (co, quads.tolist(), uv.reshape(-1, 2))}
    (obj,) = emit(parts, {f"SM_{name}": mat})
    for key, values in (("crack", crack), ("fresh", shells / depths.max())):
        obj.data.attributes.new(key, "FLOAT", "POINT").data.foreach_set("value", values)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    mat, devices = textures(obj, name, spec["textureSize"], output)
    collision = hull(co, points, name, mat)
    return [obj, collision], {
        "name": name,
        "latticeStepM": STEP,
        "vertices": len(co),
        "texelsPerMetre": density,
        "bakeSamples": SAMPLES,
        "aoDistanceM": AO_DISTANCE_M,
        "bakeDevices": devices,
        "collisionVertices": len(collision.data.vertices),
    }


if __name__ == "__main__":
    main(Path(__file__).resolve(), {"granite-hero-boulder": boulder})

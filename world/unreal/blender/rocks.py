"""Build valley granite rocks: faceted, edge-rounded sculpts for Nanite with unique baked PBR.

blender -b --factory-startup --python world/unreal/blender/rocks.py -- <asset> --output <dir>
Writes <asset>.blend, <asset>.glb, T_<Name>_{BC,N,ORM}.png and build.json into a new .local
directory. T_<Name>_N is DirectX (green down) for Unreal; the .blend uses the OpenGL T_<Name>_N_GL.
UCX_SM_<Name> is the convex hull that Unreal Interchange imports as collision for SM_<Name>.

A rock is the union of two lobes that share one set of facet planes, each facet rounding its
edges with its own radius, parted by a joint through the projection centre. One smooth warp bends
every facet, so faces undulate and edges wander; spall flakes, Voronoi chips and pits then cut
the surface. A box lattice is projected along rays from the centre, so the rock must be
star-shaped about it (asserted).
"""

import math
import sys
import time
from pathlib import Path

import bmesh
import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from world.unreal.blender.common import add, directx, emit, gpu, lin, main, pbr, pixels, stored  # noqa: E402
from world.unreal.blender.spec import load  # noqa: E402

STEP = 0.01
WARP = 0.5
MARGIN = 0.05
GAP = 2
TEXELS = 512
BURIED_TEXEL_SCALE = 0.5
HULL_STEP = 80
SAMPLES = {"DIFFUSE": 16, "ROUGHNESS": 16, "NORMAL": 16, "AO": 128}
AO_DISTANCE_M = 0.5
# Rays within JOINT_RAYS[0] radians of the joint crowd toward it by up to 1 / (1 - JOINT_RAYS[1]).
JOINT_RAYS = (0.03, 0.75)
CONCAVITY_ROUNDS = (10, 40, 160)
# Standard deviation of the warp that bends every facet, in metres.
BEND_M = 0.015
FLAKES = 50
UNDERCUT = (0.5, 1.0, -0.3)
ROUND = 0.2
GRADIENTS = np.array(
    [(1, 1, 0), (-1, 1, 0), (1, -1, 0), (-1, -1, 0), (1, 0, 1), (-1, 0, 1)]
    + [(1, 0, -1), (-1, 0, -1), (0, 1, 1), (0, -1, 1), (0, 1, -1), (0, -1, -1)],
    float,
)
MIX = np.uint64(0x9E3779B97F4A7C15)

# Hero boulder fitted to the reference turnaround silhouettes, the master silhouette and the
# master's traced joint (metres; bounding box centred, grade at z 0, before scaling to the spec).
# Facets are (azimuth from +X toward +Y, elevation, offset, rounding radius), degrees and metres.
HERO = {
    "centre": (0.004, -0.1934, 0.5364),
    "joint": (-0.6562, 0.6661, 0.3545),
    "overlap": 0.02523193009469464,
    "shoulder": 0.10092772037877856,
    "bend": 0.0723,
    "closed": (-0.12729950328255069, -0.6111974869910276),
    "facets": [
        (3.3, 4.2, 1.4287, 0.11),
        (-20.9, -23.1, 1.9185, 0.11),
        (6.7, 35.6, 2.3135, 0.11),
        (-90.0, 0.0, 1.8, 0.176),
        (-61.2, 50.0, 2.5488, 0.186),
        (-13.5, 78.6, 2.7197, 0.171),
        (180.0, 4.2, 1.4751, 0.194),
        (173.3, 40.5, 2.2764, 0.262),
        (82.5, 0.0, 1.7463, 0.175),
        (61.2, 45.0, 3.1843, 0.227),
        (20.9, 4.6, 2.2893, 0.125),
        (-146.5, 5.0, 2.8884, 0.285),
        (146.5, 5.0, 1.5614, 0.285),
        (-33.5, 5.0, 2.7975, 0.171),
        (0.0, -30.6, 0.845, 0.122),
        (-90.0, -41.9, 1.2064, 0.266),
        (180.0, -30.6, 1.0837, 0.254),
        (90.0, -41.9, 0.9833, 0.266),
        (33.5, -35.0, 0.9996, 0.343),
        (-33.5, -35.0, 1.8984, 0.343),
        (146.5, -35.0, 1.4112, 0.343),
        (-146.5, -35.0, 1.4162, 0.343),
        (0.0, -61.1, 0.4127, 0.332),
        (90.0, -70.0, 0.3363, 0.356),
        (180.0, -61.1, 0.753, 0.332),
        (-90.0, -70.0, 0.4364, 0.356),
        (0.0, -90.0, 0.7171, 0.172),
        (-146.5, 40.0, 3.1145, 0.343),
        (146.5, 40.0, 2.2407, 0.343),
        (29.0, 59.1, 2.8728, 0.34),
        (-3.5, 51.3, 2.5206, 0.324),
        (54.2, 58.5, 2.7567, 0.358),
        (132.3, 60.9, 2.8033, 0.352),
        (171.1, 56.2, 2.8682, 0.329),
        (-145.5, 59.4, 3.2598, 0.344),
        (-65.1, 60.1, 2.924, 0.363),
        (12.3, 25.7, 2.3192, 0.256),
        (62.5, 25.5, 3.0946, 0.333),
        (153.6, 29.1, 2.0866, 0.275),
        (-165.1, 21.2, 1.9566, 0.257),
        (-87.8, 24.4, 2.2343, 0.353),
        (-22.0, 20.8, 2.1462, 0.266),
        (20.0, 42.8, 2.6366, 0.272),
        (105.2, 51.1, 2.4513, 0.317),
        (159.2, 43.2, 2.3618, 0.273),
        (-164.7, 41.6, 2.2934, 0.268),
        (-85.3, 54.7, 2.967, 0.315),
        (-28.8, 42.0, 2.1834, 0.28),
        (71.8, 79.9, 2.919, 0.346),
        (-81.3, 75.1, 3.101, 0.35),
        (-170.3, 77.0, 3.2476, 0.341),
    ],
}


def smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def lattice(counts):
    """Outward quads over an integer box lattice surface, their box face and per-corner chart
    coordinates (lattice units, orientation-preserving seen from outside)."""
    points, quads, faces, local, count = [], [], [], [], 0
    for axis in range(3):
        b, c = (axis + 1) % 3, (axis + 2) % 3
        for side in (0, counts[axis]):
            i, j = np.meshgrid(np.arange(counts[b] + 1), np.arange(counts[c] + 1), indexing="ij")
            p = np.empty((*i.shape, 3), int)
            p[..., axis], p[..., b], p[..., c] = side, i, j
            k = count + np.arange(i.size).reshape(i.shape)
            q = np.stack([k[:-1, :-1], k[1:, :-1], k[1:, 1:], k[:-1, 1:]], -1).reshape(-1, 4)
            q = q if side else q[:, ::-1]
            corner = p.reshape(-1, 3)[q - count]
            u = corner[..., b] if side else counts[b] - corner[..., b]
            local.append(np.stack([u, corner[..., c]], -1))
            quads.append(q)
            faces.append(np.full(len(q), 2 * axis + (side > 0)))
            points.append(p.reshape(-1, 3))
            count += i.size
    unique, inverse = np.unique(np.concatenate(points), axis=0, return_inverse=True)
    quads = inverse.ravel()[np.concatenate(quads)]
    return unique, quads, np.concatenate(faces), np.concatenate(local)


def atlas(local, faces, counts):
    """Pack the face charts in rows: X faces; Y faces and the buried base; the top in two halves."""
    uv = local.astype(float)
    k = counts[0] // 2
    top = faces == 5
    first = top & (local[..., 0].min(axis=1) < k)
    second = top & ~first
    uv[top] = np.stack(
        [local[top][..., 1], np.where(first[top, None], k, counts[0]) - local[top][..., 0]], -1
    )
    uv[faces == 4] *= BURIED_TEXEL_SCALE
    size = {f: counts[[(f // 2 + 1) % 3, (f // 2 + 2) % 3]] for f in range(6)}
    rows = [
        [(faces == 1, size[1]), (faces == 0, size[0])],
        [(faces == 3, size[3]), (faces == 2, size[2]), (faces == 4, size[4] * BURIED_TEXEL_SCALE)],
        [(first, (counts[1], k)), (second, (counts[1], counts[0] - k))],
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


def facets(rows):
    """(azimuth, elevation, offset, radius) rows as (normal, offset, radius) planes."""
    rows = np.asarray(rows, float)
    assert rows.ndim == 2 and rows.shape[1] == 4 and (rows[:, 3] > 0).all(), rows.shape
    az, el = np.radians(rows[:, 0]), np.radians(rows[:, 1])
    normal = np.stack([np.cos(az) * np.cos(el), np.sin(az) * np.cos(el), np.sin(el)], 1)
    return np.concatenate([normal, rows[:, 2:]], 1)


def field(x, s, design, planes):
    """Positive outside the union of the lobes on either side of the joint, `s` the signed
    distance from it. Facet edges blend elliptically over both facets' radii; the lobes overlap
    the joint by `overlap`, which grows until they fuse toward the back of the rock."""
    q = (x @ planes[:, :3].T - planes[:, 3] + planes[:, 4]) / planes[:, 4]
    facet = (np.maximum(q, 0) ** 2).sum(axis=1)
    overlap = design["overlap"] + 0.5 * smooth(x[:, 0], *design["closed"])
    lobes = [
        (np.maximum(side * s - overlap + design["shoulder"], 0) / design["shoulder"]) ** 2
        for side in (1, -1)
    ]
    return np.sqrt(facet + np.minimum(*lobes)) - 1


def shaped(x, half):
    """Facet-frame points of a rock drawn in toward its vertical axis below UNDERCUT[1] m, by up
    to UNDERCUT[0] at UNDERCUT[2] m, so that its underside rounds into a narrow contact, and
    toward its plan diagonals by up to ROUND at the plan half-extents `half`."""
    shrink = 1 - UNDERCUT[0] * smooth(x[:, 2], UNDERCUT[1], UNDERCUT[2])
    corner = 1 - ROUND * (x[:, 1::-1] / half[::-1]) ** 2
    return np.concatenate([x[:, :2] / (shrink[:, None] * corner), x[:, 2:]], axis=1)


def surface(u, omega, bend, half, design, planes, reach=4.0):
    """Distance from the centre along unit rows `u` to the rock surface, by bisection. The warped
    joint leaves each ray at angle `omega`, so `s` grows linearly along it; each ray sees the
    facets shifted by its row of `bend`."""
    centre, joint = np.array(design["centre"]), unit(design["joint"])
    v = u @ joint - omega

    def inside(r):
        return field(shaped(centre + r[:, None] * u + bend, half), r * v, design, planes) < 0

    assert field(centre[None], np.zeros(1), design, planes)[0] < 0, "centre outside the rock"
    assert not inside(np.full(len(u), reach)).any(), "rock beyond reach"
    low, high = np.zeros(len(u)), np.full(len(u), reach)
    for _ in range(20):
        mid = (low + high) / 2
        hit = inside(mid)
        low, high = np.where(hit, mid, low), np.where(hit, high, mid)
    rho = (low + high) / 2
    for f in (0.25, 0.5, 0.75, 0.95):
        assert inside(f * rho).all(), f"rock not star-shaped about its centre at {f}"
    return rho


def normals(co, quads):
    face = np.cross(co[quads[:, 2]] - co[quads[:, 0]], co[quads[:, 3]] - co[quads[:, 1]])
    out = np.zeros_like(co)
    for k in range(4):
        np.add.at(out, quads[:, k], face)
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def hashed(cells, seed):
    """64-bit hashes of integer lattice rows."""
    h = np.full(len(cells), seed, np.uint64)
    for k in range(3):
        h = (h ^ cells[:, k].astype(np.uint64)) * MIX
        h ^= h >> np.uint64(29)
    return h


def uniform(h, bit):
    """Uniform values in [0, 1) from the 16 bits of hashes `h` above `bit`."""
    return ((h >> np.uint64(bit)) & np.uint64(0xFFFF)).astype(float) / 65536


def noise(points, seed):
    """Perlin gradient noise at rows of `points`, with gradients hashed from the lattice."""
    base = np.floor(points)
    f = points - base
    base = base.astype(np.int64)
    w = f * f * f * (f * (f * 6 - 15) + 10)
    out = np.zeros(len(points))
    for corner in np.ndindex(2, 2, 2):
        c = np.array(corner)
        g = GRADIENTS[hashed(base + c, seed) % np.uint64(len(GRADIENTS))]
        out += np.prod(np.where(c, w, 1 - w), axis=1) * np.einsum("ij,ij->i", g, f - c)
    return out


def fractal(points, scale, octaves, seed):
    """Unit-variance fractal noise at rows of `points` with features of 1/scale."""
    values = sum(0.5**k * noise(points * scale * 2**k, seed + k) for k in range(octaves))
    return values / values.std()


def cells(points, scale, seed):
    """Voronoi cells of a jittered grid with `scale` cells per metre: the nearest and second
    nearest feature distances in cell units, and the nearest cell's hash."""
    p = points * scale
    base = np.floor(p).astype(np.int64)
    f1, f2 = np.full(len(p), np.inf), np.full(len(p), np.inf)
    owner = np.zeros(len(p), np.uint64)
    for offset in np.ndindex(3, 3, 3):
        cell = base + offset - np.ones(3, np.int64)
        h = hashed(cell, seed)
        d = np.linalg.norm(cell + np.stack([uniform(h, b) for b in (8, 24, 40)], 1) - p, axis=1)
        nearer = d < f1
        f2 = np.where(nearer, f1, np.minimum(f2, d))
        f1, owner = np.where(nearer, d, f1), np.where(nearer, h, owner)
    return f1, f2, owner


def chips(points, scale, fraction, depth, seed):
    """Flat-floored chips in `fraction` of the Voronoi cells at `scale` per metre, 30-100 % of
    `depth` deep, with walls over the outer quarter of each cell."""
    f1, f2, h = cells(points, scale, seed)
    floor = depth * (0.3 + 0.7 * uniform(h, 0)) * smooth(f2 - f1, 0, 0.25)
    return np.where(uniform(h, 16) < fraction, floor, 0)


def pits(points, scale, fraction, depth, seed):
    """Round pits a third of a cell across in `fraction` of the cells at `scale` per metre."""
    f1, _, h = cells(points, scale, seed)
    return np.where(uniform(h, 16) < fraction, depth * smooth(f1, 0.17, 0.05), 0)


def flakes(co, n, rng, sites, count):
    """Planar spall scars at `count` of `sites`: each plane 1-4 cm under its site, tilted up to
    12 degrees off its normal, cuts the cap above it inside an elongated 8-30 cm footprint whose
    rim stays crisp, sparing the lowest 0.15 m. Returns the displacement and the cut depth."""
    shift, depth = np.zeros_like(co), np.zeros(len(co))
    for i in rng.sample(sites.tolist(), count):
        along = unit(np.cross(n[i], [rng.uniform(-1, 1) for _ in range(3)]))
        normal = unit(n[i] + math.tan(math.radians(rng.uniform(0, 12))) * along)
        reach, stretch = 0.08 * (0.3 / 0.08) ** rng.random(), rng.uniform(0, 0.75)
        d = co - co[i]
        h = d @ normal + rng.uniform(0.01, 0.04)
        # A 4 mm quadratic blend rounds the scar floor into its walls.
        cut = np.where(h > 0.004, h, np.maximum(h + 0.004, 0) ** 2 / 0.016)
        r = np.sqrt(np.einsum("ij,ij->i", d, d) - stretch * (d @ along) ** 2)
        cut *= smooth(r, reach, 0.85 * reach) * smooth(co[:, 2], 0.05, 0.15)
        shift -= cut[:, None] * normal
        depth += cut
    return shift, depth


def concavity(co, quads, n):
    """Offset along the normal of the surface smoothed by each of CONCAVITY_ROUNDS neighbour
    averages: positive in crevices and hollows, negative on convex edges."""
    a, b = quads.ravel(), np.roll(quads, -1, axis=1).ravel()
    i, j = np.concatenate([a, b]), np.concatenate([b, a])
    count = np.bincount(i, minlength=len(co))[:, None]
    p, out = co.copy(), []
    for k in range(1, max(CONCAVITY_ROUNDS) + 1):
        p = np.stack([np.bincount(i, p[j, d], len(co)) for d in range(3)], 1) / count
        if k in CONCAVITY_ROUNDS:
            out.append(np.einsum("ij,ij->i", p - co, n))
    return out


def granite():
    """Procedural Sierra granite, the bake source: a bare mineral mosaic and its stained rind
    mixed by facet age, mottles, iron stain, lichen, grime, edge wear, a soil band and grain
    relief, masked by the sculpt's vertex attributes."""
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

    def weighted(mask, weight):
        return math("MULTIPLY", mask, weight)

    p = node("ShaderNodeTexCoord", "Object")
    nz = node("ShaderNodeSeparateXYZ", "Z", (("Vector", node("ShaderNodeNewGeometry", "Normal")),))
    z = node("ShaderNodeSeparateXYZ", "Z", (("Vector", p),))
    crack, fresh, age, cavity, hollow, edge, lobe = (
        node("ShaderNodeAttribute", "Fac", attribute_name=name)
        for name in ("crack", "fresh", "age", "cavity", "hollow", "edge", "lobe")
    )
    warp = node(
        "ShaderNodeVectorMath",
        "Vector",
        (("Vector", fbm(p, 60, 2, "Color")), ("Vector_001", (0.004,) * 3), ("Vector_002", p)),
        operation="MULTIPLY_ADD",
    )
    # Fresh spalls and chips are bare; old facets, the smaller lobe and sheltered hollows carry
    # the weathering rind, which worn edges lose.
    weather = math("SUBTRACT", age, weighted(age, fresh))
    for mask, weight in ((lobe, 0.25), (hollow, 0.2), (edge, -0.35)):
        weather = math("MULTIPLY_ADD", mask, weight, weather)
    weather = band(weather, 0, 1)
    rind = weighted(math("MULTIPLY_ADD", band(fbm(p, 2.2, 4), 0.35, 0.6), 0.45, 0.55), weather)
    # 8-12 mm grains sorted by a 7 cm field so that biotite clusters, and 1 cm biotite books.
    grain_d, grain = cells(warp, 110)
    sorting = math("MULTIPLY_ADD", fbm(p, 14, 2), 0.3, math("SUBTRACT", grain, 0.15))
    bare = ((0, (32, 30, 29)), (0.13, (62, 61, 58)), (0.17, (140, 140, 137)))
    bare += ((0.38, (208, 204, 196)), (0.8, (200, 186, 170)))
    stained = ((0, (64, 54, 44)), (0.13, (86, 76, 64)), (0.17, (132, 122, 108)))
    stained += ((0.38, (178, 160, 136)), (0.8, (176, 152, 124)))
    rock = mix(rind, ramp(sorting, bare, True), ramp(sorting, stained, True))
    flake_d, flake = cells(warp, 50)
    rock = mix(weighted(band(flake_d, 0.32, 0.24), band(flake, 0.72, 0.74)), rock, (30, 28, 27))
    fine_d, fine = cells(p, 300)
    rock = mix(weighted(fine, 0.25), rock, (96, 92, 86))
    # Broad tone, the rind's brown film, 10-30 cm dark mottles and iron stain.
    rock = mix(0.2, rock, ramp(fbm(p, 0.7, 4), ((0.35, (160, 146, 128)), (0.65, (130, 128, 124)))))
    rock = mix(weighted(rind, 0.35), rock, (122, 104, 84))
    mottle = weighted(band(fbm(p, 3.2, 4), 0.58, 0.74), weighted(weather, 0.55))
    stain = weighted(band(fbm(p, 1.4, 3), 0.56, 0.74), weighted(weather, 0.4))
    rock = mix(stain, mix(mottle, rock, (78, 70, 62)), (150, 108, 64))
    streak_p = node(
        "ShaderNodeVectorMath",
        "Vector",
        (("Vector", p), ("Vector_001", (7, 7, 0.6))),
        operation="MULTIPLY",
    )
    steep = band(math("ABSOLUTE", nz), 0.65, 0.15)
    streaks = weighted(band(fbm(streak_p, 1, 3), 0.52, 0.7), weighted(steep, weather))
    rock = mix(weighted(streaks, 0.4), rock, (92, 84, 74))
    rock = mix(weighted(edge, 0.3), rock, (200, 194, 184))
    # Grime in hollows, crevices and the joint, then moisture and the soil band above grade.
    rock = mix(weighted(hollow, 0.4), rock, (88, 78, 66))
    rock = mix(weighted(cavity, 0.5), rock, (66, 58, 50))
    rock = mix(weighted(crack, 0.85), rock, (56, 48, 40))
    rock = mix(weighted(band(z, 0.5, 0.15), 0.35), rock, (88, 78, 64))
    soil = band(math("MULTIPLY_ADD", fbm(p, 4, 3), 0.15, z), 0.32, 0.15)
    splash = weighted(band(z, 0.45, 0.15), band(fbm(p, 30, 2), 0.62, 0.68))
    earth = ramp(fbm(p, 3, 3), ((0.4, (74, 60, 46)), (0.6, (66, 60, 46))))
    rock = mix(math("MAXIMUM", weighted(soil, 0.95), weighted(splash, 0.5)), rock, earth)
    # Crustose lichen on old up-facing and sheltered rock, absent from spalls, joints and soil.
    cover = math("MULTIPLY_ADD", weighted(weather, math("MULTIPLY_ADD", nz, 0.5, 0.5)), 0.8, 0)
    cover = math("MULTIPLY_ADD", fbm(p, 1.1, 5), 0.7, cover)
    for mask, weight in ((hollow, 0.15), (crack, -0.6), (fresh, -0.4), (soil, -0.6)):
        cover = math("MULTIPLY_ADD", mask, weight, cover)
    crust = band(math("MULTIPLY_ADD", band(cover, 0.62, 0.9), 0.3, fbm(p, 16, 5)), 0.69, 0.71)
    areole = node(
        "ShaderNodeTexVoronoi",
        "Distance",
        (("Vector", p), ("Scale", 300)),
        feature="DISTANCE_TO_EDGE",
    )
    species = ((0.3, (182, 136, 56)), (0.45, (124, 124, 72)), (0.58, (170, 150, 78)))
    species = ramp(fbm(p, 3, 2), species + ((0.7, (146, 150, 128)),))
    species = mix(weighted(band(areole, 0.08, 0.0), 0.5), species, (66, 60, 44))
    species = mix(weighted(fbm(p, 40, 3), 0.35), species, (96, 88, 60))
    crust = weighted(crust, band(fbm(p, 60, 4), 0.45, 0.5))
    rock = mix(weighted(crust, 0.75), rock, species)
    dot_d, _ = cells(p, 80)
    rock = mix(weighted(band(dot_d, 0.16, 0.1), band(cover, 0.66, 0.74)), rock, (40, 38, 34))
    rough = ramp(sorting, ((0, 0.42), (0.13, 0.4), (0.17, 0.62), (0.8, 0.66)), True, True)
    matte = math("MAXIMUM", crust, math("MAXIMUM", soil, cavity))
    rough = math("MULTIPLY_ADD", math("SUBTRACT", 0.86, rough), matte, rough)
    relief = ((0, 0.15), (0.13, 0.5), (0.17, 0.8), (0.38, 0.6), (0.8, 0.55))
    height = math("MULTIPLY_ADD", fbm(p, 350, 2), 1.0, ramp(sorting, relief, True, True))
    height = math("MULTIPLY_ADD", fbm(p, 45, 3), 1.4, height)
    # Grains and sub-grains weather into domes: the granular crumb of exposed granite.
    height = math("MULTIPLY_ADD", grain_d, -1.2, height)
    height = math("MULTIPLY_ADD", fine_d, -0.6, height)
    height = math("MULTIPLY_ADD", weighted(crust, band(areole, 0.0, 0.1)), 0.6, height)
    bump = (("Height", height), ("Distance", 0.0015), ("Strength", 1.0))
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
        stored(img, output)
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
    rough = pixels(bake(obj, target, "roughness", size, "ROUGHNESS", False))
    ao = pixels(bake(obj, target, "ao", size, "AO", False))
    assert (ao[..., 3] == 1).all() and (rough[..., 3] == 1).all()
    mat = pbr(name, bc, ao[..., 0], rough[..., 0], output, gl)
    directx(name, pixels(gl), output)
    for img in ("roughness", "ao"):
        bpy.data.images.remove(bpy.data.images[img])
    bpy.data.worlds.remove(scene.world)
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


def sculpt(rng, design, size, buried):
    """The displaced rock lattice in the spec frame, its atlas and its material masks."""
    started = time.monotonic()
    planes = facets(design["facets"])
    centre, joint = np.array(design["centre"]), unit(design["joint"])
    low = np.array([-size[0] / 2, -size[1] / 2, -buried]) - MARGIN
    high = np.array([size[0] / 2, size[1] / 2, size[2] - buried]) + MARGIN
    counts = np.ceil((high - low) / STEP).astype(int)
    points, quads, faces, local = lattice(counts)
    t = np.tan(WARP * (points / counts * 2 - 1)) / np.tan(WARP)
    u = unit(low + (t + 1) / 2 * (high - low) - centre)
    seeds = [rng.randrange(2**31) for _ in range(14)]
    # The joint curves by `bend` toward its top and bottom. Finer joint wander would fold the
    # crowded rays; the crack wanders within the joint below.
    omega = design["bend"] * u[:, 2] ** 2 + 0.018 * fractal(u, 2, 2, seeds[0])
    width, crowd = JOINT_RAYS
    u = unit(u - joint * (crowd * width * np.tanh((u @ joint - omega) / width))[:, None])
    bend = BEND_M * np.stack([fractal(u, 3, 3, s) for s in seeds[2:5]], 1)
    rho = surface(u, omega, bend, size[:2] / 2, design, planes)
    base = centre + rho[:, None] * u
    n = normals(base, quads)
    assert (np.einsum("ij,ij->i", n, base - centre) > 0).all(), "inverted lattice faces"
    print(f"TIME surface {time.monotonic() - started:.1f}", flush=True)
    q = shaped(base + bend, size[:2] / 2) @ planes[:, :3].T - planes[:, 3] + planes[:, 4]
    q /= planes[:, 4]
    weight = np.maximum(q, 0) ** 8
    age = weight @ np.array([rng.random() ** 0.5 for _ in planes]) / weight.sum(axis=1)
    edge = smooth(np.sort(q, axis=1)[:, -2], 0.1, 0.6)
    side = rho * (u @ joint - omega)
    wander = 0.01 * fractal(base, 6, 2, seeds[1]) + 0.003 * fractal(base, 25, 2, seeds[13])
    lateral = np.abs(side - wander)
    sites = np.flatnonzero((base[:, 2] > 0.3) & (lateral > 0.12))[::7]
    scars, scarred = flakes(base, n, rng, sites, FLAKES)
    chipped = chips(base, 14, 0.5, 0.008, seeds[5]) * (0.3 + 0.7 * edge)
    relief = 0.012 * fractal(base, 1.3, 3, seeds[6]) + 0.004 * fractal(base, 5, 2, seeds[7])
    relief += 0.0015 * fractal(base, 18, 2, seeds[8]) - chipped
    relief -= pits(base, 45, 0.06, 0.005, seeds[9])
    # A V crack with rounded lips along the joint, 3-9 cm wide and 5-9 cm deep along the rays,
    # with chipped lips, closing toward the back with the lobe shoulders.
    opening = smooth(base[:, 0], *design["closed"][::-1])
    half = np.clip(0.025 * (1 + 0.4 * fractal(base, 3, 2, seeds[10])), 0.015, 0.045)
    crack = 0.07 * (1 + 0.3 * fractal(base, 2, 2, seeds[11])) * opening
    crack *= np.clip(1 - lateral / half, 0, 1) ** 2
    relief -= chips(base, 30, 0.6, 0.012, seeds[12]) * smooth(lateral, half + 0.04, half) * opening
    # A smooth buried base keeps the lowest vertex, and so grade, on the bottom plane.
    bottom = smooth(base[:, 2], base[:, 2].min(), base[:, 2].min() + 0.25)
    co = base + bottom[:, None] * (scars + n * relief[:, None]) - u * crack[:, None]
    lo, hi = co.min(axis=0), co.max(axis=0)
    co = (co - (lo + hi) / 2) * size / (hi - lo)
    co[:, 2] += size[2] / 2 - buried
    print(f"TIME sculpt {time.monotonic() - started:.1f}", flush=True)
    fine, mid, broad = concavity(co, quads, normals(co, quads))
    masks = {
        "crack": np.clip(crack / 0.02, 0, 1),
        "fresh": np.maximum(smooth(scarred * bottom, 0.004, 0.02), smooth(chipped, 0.003, 0.007)),
        "age": age,
        "cavity": np.clip(fine / 0.003, 0, 1),
        "hollow": np.clip(broad / 0.012, 0, 1),
        "edge": np.clip(-mid / 0.008, 0, 1),
        "lobe": smooth(side, -0.05, 0.25),
    }
    print(f"TIME masks {time.monotonic() - started:.1f}", flush=True)
    return co, points, quads, atlas(local, faces, counts), masks


def boulder(rng, output):
    """Glacial erratic: two plane-cut lobes parted by a weathered joint, spalled and lichened."""
    asset, name = "granite-hero-boulder", "GraniteHeroBoulder"
    spec = load("rocks")[asset]
    size, buried = np.array(spec["dimensionsM"]), spec["buriedM"]
    co, points, quads, uv, masks = sculpt(rng, HERO, size, buried)
    above = co[quads].mean(axis=1)[:, 2] > 0
    outside = above & (masks["crack"][quads].max(axis=1) < 0.05)
    # Crack walls are steep and dark; their stretched texels are measured, not asserted.
    density = {
        "outsideJoints": texels(co, quads, uv, spec["textureSize"], outside),
        "all": texels(co, quads, uv, spec["textureSize"], above),
    }
    assert density["outsideJoints"]["p5"] >= TEXELS, density
    mat, target = granite()
    parts = {f"SM_{name}": (co, quads.tolist(), uv.reshape(-1, 2))}
    (obj,) = emit(parts, {f"SM_{name}": mat})
    for key, values in masks.items():
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
        "masksMean": {k: float(v.mean()) for k, v in masks.items()},
        "bakeSamples": SAMPLES,
        "aoDistanceM": AO_DISTANCE_M,
        "bakeDevices": devices,
        "collisionVertices": len(collision.data.vertices),
    }


if __name__ == "__main__":
    main(Path(__file__).resolve(), {"granite-hero-boulder": boulder})

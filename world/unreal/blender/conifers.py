"""Build valley conifers: seeded Sierra lodgepole pines with geometric needle tufts.

blender -b --factory-startup --python world/unreal/blender/conifers.py -- <asset> --output <dir>
Writes <asset>.blend, <asset>.glb, T_<Name>_{BC,N,ORM}.png and build.json into a new .local
directory. T_<Name>_N is DirectX (green down) for Unreal; the .blend and GLB use T_<Name>_N_GL.
The tree is a Nanite assembly: the bark mesh is its base, the twigs one whole part and the
needle tufts a few parts instanced at every shoot tip. tufts.glb holds the tuft parts and
tufts.json their instances in the build frame (before build.json's originShiftM). The GLB also
carries the tufts realized in place, for renders and extents; build.json names every role.
Morphology follows Pinus contorta var. murrayana (USFS FEIS pinconm): thin scaly bark, short
branches finely subdivided into small branchlets, needles 3-6 cm in fascicles of two.
"""

import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from world.unreal.blender.common import directx, emit, main, pbr, saved  # noqa: E402
from world.unreal.blender.spec import digest  # noqa: E402

UP = np.array([0.0, 0.0, 1.0])
GOLDEN = math.pi * (3 - math.sqrt(5))
HEIGHT_M = 16.6
DBH_M = 0.44
CROWN_BASE_M = 4.4
CROWN_RADIUS_M = 3.0
LEAN_M = 0.35
TUFT_M = (0.2, 0.25, 0.3, 0.35, 0.4, 0.45)
FASCICLES_PER_M = 720
NEEDLE_M = (0.055, 0.076)
NEEDLE_RADIUS_M = 0.0014
SHOOT_M = 0.0045
BARK_PX = 2048
# Plate depth where one texture spans the 1.45 m circumference at breast height.
BARK_RELIEF_M = 0.008
# sRGB albedo derived from the approved master's bark and foliage regions.
BARK = {
    "grey": (152, 150, 148),
    "warm": (176, 134, 108),
    "rim": (194, 188, 182),
    "fissure": (78, 66, 61),
}
# Foliage columns: 0-5 needle greens, 6 yellowing old needles, 7 twig bark.
FOLIAGE = [
    (123, 115, 64),
    (113, 109, 60),
    (133, 121, 68),
    (107, 105, 62),
    (139, 125, 70),
    (119, 111, 58),
    (150, 124, 72),
    (92, 80, 72),
]
TWIG = 7
SHEATH = (112, 86, 62)
# The Nanite Foliage stand cost was measured with 4453 tufts and 7,008,832 triangles.
TUFTS_MAX = 4453
TRIANGLES_MAX = 7_008_832


def unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0, 1)
    return t * t * (3 - 2 * t)


def fbm(nrng, size, beta):
    """Periodic fractal noise with unit variance: white noise filtered by 1/f^beta."""
    fy, fx = np.meshgrid(np.fft.fftfreq(size), np.fft.rfftfreq(size), indexing="ij")
    falloff = np.hypot(fx, fy)
    falloff[0, 0] = np.inf
    noise = np.fft.irfft2(np.fft.rfft2(nrng.standard_normal((size, size))) / falloff**beta)
    return (noise - noise.mean()) / noise.std()


def voronoi(nrng, columns, rows, warp):
    """Periodic Voronoi in cell units at domain-warped texel centres: nearest and second
    distances, nearest cell id and the rows from each texel up to its cell's seed.

    Seeds keep to the middle of their rows, so cells stack in columns and stay as elongated
    as the lattice.
    """
    jitter = nrng.random((rows, columns, 2)) * [1, 0.6] + [0, 0.2]
    texel = (np.arange(BARK_PX) + 0.5) / BARK_PX
    u, v = np.meshgrid(texel * columns, texel * rows)
    u, v = u + warp[0], v + warp[1]
    iu, iv = np.floor(u).astype(int), np.floor(v).astype(int)
    f1, f2 = np.full(u.shape, np.inf), np.full(u.shape, np.inf)
    nearest, below = np.zeros(u.shape, dtype=int), np.zeros(u.shape)
    for dv in (-1, 0, 1):
        for du in (-1, 0, 1):
            cu, cv = (iu + du) % columns, (iv + dv) % rows
            dy = iv + dv + jitter[cv, cu, 1] - v
            d = np.hypot(iu + du + jitter[cv, cu, 0] - u, dy)
            closer = d < f1
            f2 = np.where(closer, f1, np.minimum(f2, d))
            nearest = np.where(closer, cv * columns + cu, nearest)
            below = np.where(closer, dy, below)
            f1 = np.minimum(f1, d)
    return f1, f2, nearest, below


def bark_maps(nrng):
    """Tileable thin scaly bark: colour, roughness, AO and height, rows along V.

    Thin loose scales twice as tall as wide stack in columns like shingles, lower edges
    lifted. Boundaries between columns open into fissures, those between stacked scales are
    thin steps. Most scales have weathered grey, the rest show orange-brown under-bark.
    """
    warp = fbm(nrng, BARK_PX, 1.8), fbm(nrng, BARK_PX, 1.8)
    s1, s2, scale, below = voronoi(nrng, 48, 22, (0.5 * warp[0], 0.25 * warp[1]))
    noise, tone = fbm(nrng, BARK_PX, 1.2), fbm(nrng, BARK_PX, 2.3)
    edge = s2 - s1
    gv, gu = np.gradient(edge)
    across = gu**2 / (gu**2 + gv**2 + 1e-12)
    # The orientation is unstable where cells meet; a 5-texel box blur settles it.
    for axis in (0, 1):
        across = sum(np.roll(across, k, axis) for k in range(-2, 3)) / 5
    gap = smoothstep(0.0, 0.03 + 0.12 * across * (1 + 0.5 * np.abs(noise)), edge)
    lift, weather = nrng.random((2, 48 * 22))[:, scale]
    height = gap * (0.55 + 0.2 * lift + 0.25 * np.clip(2 * below, -1, 1))
    height = np.clip(height + 0.04 * noise, 0, 1)
    c = {k: np.array(v) / 255 for k, v in BARK.items()}
    grey = smoothstep(0.35, 0.65, weather + 0.3 * tone)[..., None]
    colour = c["warm"] + (c["grey"] - c["warm"]) * grey
    rim = (1 - smoothstep(0.0, 0.14, edge)) * (below > 0) * (1 - across)
    colour = colour + (c["rim"] - colour) * 0.45 * rim[..., None]
    lit = colour * (0.9 + 0.08 * np.clip(below, -0.5, 0.5)[..., None] + 0.05 * noise[..., None])
    colour = c["fissure"] + (lit - c["fissure"]) * gap[..., None]
    return np.clip(colour, 0, 1), 0.96 - 0.12 * height, 0.5 + 0.5 * np.sqrt(height), height


def foliage_maps():
    """Colour columns along U: needles run from the brown sheath at V = 0 to the tip, the
    twig column is uniform."""
    v = (np.arange(512) + 0.5) / 512
    sheath = np.array(SHEATH) / 255
    columns = np.repeat(np.array(FOLIAGE) / 255, 8, axis=0)[None]
    tone = (0.86 + 0.24 * v)[:, None, None] * columns
    colour = sheath + (tone - sheath) * smoothstep(0.03, 0.07, v)[:, None, None]
    colour[:, 8 * TWIG : 8 * TWIG + 8] = columns[:, 8 * TWIG : 8 * TWIG + 8]
    ao = np.broadcast_to((0.72 + 0.28 * v)[:, None], colour.shape[:2])
    return colour, np.full(colour.shape[:2], 0.62), ao


def sample(values, u, v):
    """Bilinear lookup of a periodic map whose rows run along V."""
    size = values.shape[0]
    x, y = u * size - 0.5, v * size - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - x0, y - y0

    def at(dx, dy):
        return values[(y0 + dy) % size, (x0 + dx) % size]

    bottom = at(0, 0) * (1 - fx) + at(1, 0) * fx
    return bottom + (at(0, 1) * (1 - fx) + at(1, 1) * fx - bottom) * fy


def material(name, maps, output, relief=None):
    """MI_<name> from BC and ORM maps, with a normal map when given height."""
    colour, rough, ao, *height = maps
    bc = saved(f"T_{name}_BC", colour, output, colour=True)
    gl = None
    if height:
        (h,) = height
        slope = relief * h.shape[0]
        du = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) / 2 * slope
        dv = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) / 2 * slope
        n = unit(np.stack([-du, -dv, np.ones_like(h)], axis=2)) * 0.5 + 0.5
        gl = saved(f"T_{name}_N_GL", n, output)
        directx(name, n, output)
    return pbr(name, bc, ao, rough, output, gl)


def tube(points, radii, facets, relief=None):
    """Swept circle closed by a tip cone: vertices, triangles and per-loop bark UVs.

    U wraps the circumference once and V advances by arc length over circumference, so bark
    plates keep their shape and shrink with the stem. `relief(u, v)` adds radial offsets.
    """
    tangent = unit(np.gradient(points, axis=0))
    normal = np.empty_like(points)
    reference = UP if abs(tangent[0] @ UP) < 0.9 else np.array([1.0, 0.0, 0.0])
    normal[0] = unit(np.cross(tangent[0], reference))
    for i in range(1, len(points)):
        normal[i] = unit(normal[i - 1] - (normal[i - 1] @ tangent[i]) * tangent[i])
    angle = np.arange(facets + 1) * 2 * np.pi / facets
    binormal = np.cross(tangent, normal)
    ring = np.cos(angle)[:, None] * normal[:, None] + np.sin(angle)[:, None] * binormal[:, None]
    step = np.linalg.norm(np.diff(points, axis=0), axis=1)
    v = np.concatenate([[0], np.cumsum(step / (np.pi * (radii[1:] + radii[:-1])))])
    uv = np.stack(np.broadcast_arrays(angle[None] / (2 * np.pi), v[:, None]), axis=2)
    r = radii[:, None] + (0 if relief is None else relief(uv[..., 0], uv[..., 1]))
    rings = points[:, None] + r[..., None] * ring
    verts = np.concatenate(
        [rings[:, :facets].reshape(-1, 3), [points[-1] + tangent[-1] * radii[-1]]]
    )
    # The seam column shares column 0's vertices; only its loops carry U = 1.
    vid = np.arange(len(points) * facets).reshape(-1, facets)[:, np.r_[0:facets, 0]]
    a, b, c, d = np.s_[:-1, :-1], np.s_[1:, :-1], np.s_[1:, 1:], np.s_[:-1, 1:]
    tip = np.full(facets, len(verts) - 1)
    tris = [
        np.stack(q, -1).reshape(-1, 3) for q in ((vid[a], vid[d], vid[c]), (vid[a], vid[c], vid[b]))
    ]
    loops = [
        np.stack(q, -2).reshape(-1, 3, 2) for q in ((uv[a], uv[d], uv[c]), (uv[a], uv[c], uv[b]))
    ]
    tris.append(np.stack([vid[-1, :-1], vid[-1, 1:], tip], -1))
    loops.append(
        np.stack([uv[-1, :-1], uv[-1, 1:], np.broadcast_to([0.5, v[-1]], (facets, 2))], -2)
    )
    return verts, np.concatenate(tris), np.concatenate(loops)


def limb(nrng, start, heading, elevation, length, droop, upturn, step=0.04):
    """Sinuous shoot from `start`, bending down by `droop` then up by `upturn` radians."""
    n = max(4, int(length / step) + 2)
    s = np.linspace(0, 1, n)
    phase = nrng.uniform(0, 2 * np.pi, 4)
    sway = 0.16 * np.sin(2.3 * np.pi * s + phase[0]) + 0.07 * np.sin(5.1 * np.pi * s + phase[1])
    lift = 0.12 * np.sin(2.7 * np.pi * s + phase[2]) + 0.05 * np.sin(6.3 * np.pi * s + phase[3])
    e = elevation - droop * s + upturn * s**3 + lift * s
    h = heading + sway * s
    d = np.stack([np.cos(e) * np.cos(h), np.cos(e) * np.sin(h), np.sin(e)], axis=1)
    steps = np.cumsum((d[1:] + d[:-1]) / 2, axis=0) * length / (n - 1)
    return start + np.concatenate([np.zeros((1, 3)), steps]), e, h, d[-1]


def facets(radius):
    return 5 if radius < 0.008 else 7 if radius < 0.02 else 10


def envelope(u):
    """Crown radius in metres at relative live-crown height u, from the master silhouette."""
    return CROWN_RADIUS_M * np.interp(u, [0, 0.07], [0.45, 1]) * min(1, ((1 - u) / 0.55) ** 1.1)


def axis(z):
    """Trunk centre at height z: a slight bow toward -Y with a gentle wander, plumb at grade."""
    t = np.clip(z, 0, HEIGHT_M) / HEIGHT_M
    wander = 0.02 * np.sin(2 * np.pi * z / 5.3 + 1.1) + 0.012 * np.sin(2 * np.pi * z / 2.9 + 0.4)
    wander = wander * smoothstep(0.0, 2.0, z)
    return np.stack([wander, 0.6 * wander - LEAN_M * t**1.6, np.asarray(z, float)], axis=-1)


def trunk(nrng, height):
    """Tapered stem with a buttressed root flare, displaced by the bark height map."""
    z = np.concatenate(
        [
            np.arange(-0.25, 2.5, 0.012),
            np.arange(2.5, HEIGHT_M - 0.6, 0.03),
            np.linspace(HEIGHT_M - 0.6, HEIGHT_M, 31),
        ]
    )
    above = np.clip(z, 0, None)
    taper = DBH_M / 2 * np.clip((HEIGHT_M - z) / (HEIGHT_M - 1.37), 0, None) ** 0.8
    radius = taper + 0.09 * np.exp(-above / 0.45)
    roots, spread = nrng.uniform(0, 2 * np.pi, 6), nrng.uniform(0.25, 0.6, 6)
    flare = radius * np.exp(-above / 0.5)
    depth = BARK_RELIEF_M * 2 * np.pi * radius / 1.45 * (1 + 1.2 * np.exp(-above / 0.9))

    def relief(u, v):
        gap = np.angle(np.exp(1j * (2 * np.pi * u[..., None] - roots)))
        lobes = (spread * np.exp(-((gap / 0.32) ** 2))).sum(axis=-1)
        return flare[:, None] * lobes + depth[:, None] * (sample(height, u, v) - 0.5)

    return tube(axis(z), np.maximum(radius, 0.002), 96, relief)


def orient(direction, twist):
    """Rotation whose columns are the local X, Y and Z axes, Z along `direction`."""
    reference = np.array([1.0, 0.0, 0.0]) if abs(direction[2]) > 0.9 else UP
    x = unit(np.cross(reference, direction))
    y = np.cross(direction, x)
    x, y = np.cos(twist) * x + np.sin(twist) * y, np.cos(twist) * y - np.sin(twist) * x
    return np.stack([x, y, direction], axis=1)


def cluster(nrng, tip, tangent, u, tufts):
    """A terminal tuft turned up along the shoot with shorter laterals: a pom-pom cluster.

    Clusters shrink toward the leader so the crown ends in a narrow spire, and gain laterals
    there so the upper crown stays dense.
    """
    direction = unit(tangent + (0.3 + 0.6 * u) * UP)
    frame = orient(direction, nrng.uniform(0, 2 * np.pi))
    size = 1 - 0.45 * u**3
    tufts.append((int(nrng.integers(2, len(TUFT_M))), frame * nrng.uniform(0.85, 1.15) * size, tip))
    for azimuth in nrng.uniform(
        0, 2 * np.pi, 1 + (nrng.random((2,)) < [0.2 + 0.6 * u, 0.6 * u**2]).sum()
    ):
        tilt = nrng.uniform(0.45, 0.9)
        side = np.cos(azimuth) * frame[:, 0] + np.sin(azimuth) * frame[:, 1]
        lateral = orient(np.cos(tilt) * direction + np.sin(tilt) * side, nrng.uniform(0, 2 * np.pi))
        scaled = lateral * nrng.uniform(0.75, 1.0) * size
        tufts.append((int(nrng.integers(0, 3)), scaled, tip - 0.01 * direction))


def shoot(nrng, meshes, tufts, start, heading, elevation, length, bend, r0, u, order):
    """Shoot of `order` 1-3 with its laterals; live tips (u set) end in a tuft cluster.

    Order 0 is a broken dead stub. Dead limbs and stubs take the bark texture, live shoots the
    foliage texture's darker twig column. Inner limbs are bare.
    """
    points, e, h, tangent = limb(nrng, start, heading, elevation, length, *bend)
    s = np.linspace(0, 1, len(points))
    tip = SHOOT_M if u is not None else 0.6 * r0 if order == 0 else 0.002
    r0 = max(r0, 1.15 * tip)
    radii = (r0 - tip) * (1 - s) ** 0.8 + tip + 0.4 * r0 * np.exp(-s * length / 0.06) * (order == 1)
    meshes["bark" if u is None else "twig"].append(tube(points, radii, facets(r0)))
    if u is not None:
        cluster(nrng, points[-1], tangent, u, tufts)
    if order not in (1, 2) or length < 0.25:
        return
    at = (0.3 if order == 1 else 0.2) * length + nrng.uniform(0, 0.08)
    side = nrng.choice([-1, 1])
    while at < 0.96 * length:
        i = int(at / length * (len(points) - 1))
        if order == 1:
            child = (0.1 + 0.32 * (length - at)) * nrng.uniform(0.5, 1.3)
        else:
            child = nrng.uniform(0.08, 0.22) + 0.15 * (length - at)
        child = min(child, 0.95)
        heading = h[i] + side * nrng.uniform(0.45, 1.25)
        elevation = 0.5 * e[i] + nrng.uniform(-0.5, 0.55)
        bend = (0.15, nrng.uniform(0.3, 0.7))
        r = min(0.6 * radii[i], 0.0035 + 0.012 * child)
        shoot(nrng, meshes, tufts, points[i], heading, elevation, child, bend, r, u, order + 1)
        side = -side
        at += nrng.uniform(0.15, 0.27) if order == 1 else nrng.uniform(0.2, 0.38)


def crown(nrng):
    """Live whorls from the crown base to the leader, dead branches and stubs below them."""
    meshes, tufts = {"bark": [], "twig": []}, []
    z, azimuth = CROWN_BASE_M, nrng.uniform(0, 2 * np.pi)
    s = np.linspace(0, 1, 50)
    while z < HEIGHT_M - 0.3:
        u = (z - CROWN_BASE_M) / (HEIGHT_M - CROWN_BASE_M)
        count = int(nrng.integers(2, 5))
        azimuth += GOLDEN + nrng.normal(0, 0.5)
        # Weak whorls leave the gaps between the crown's foliage tiers.
        vigour = nrng.uniform(0.82, 1.15) if nrng.random() < 0.85 else nrng.uniform(0.65, 0.85)
        for k in range(count):
            # Each branch has its own stream, so tuning one leaves the rest of the crown alone.
            b = np.random.default_rng(nrng.integers(1 << 62))
            if b.random() < (0.5 if u < 0.2 else 0.08):
                continue
            reach = envelope(u) * vigour * b.uniform(0.65, 1.2) - 0.1
            elevation = math.radians(np.interp(u, [0, 0.3, 0.7, 1], [-8, 0, 16, 42]))
            elevation += b.normal(0, 0.2)
            bend = ((0.15 + 0.12 * reach) * (1 - u), b.uniform(0.35, 0.7))
            length = max(reach, 0.15) / np.cos(elevation - bend[0] * s + bend[1] * s**3).mean()
            heading = azimuth + 2 * np.pi * k / count + b.normal(0, 0.45)
            r0 = 0.005 + 0.012 * length
            # Lodgepoles are multinodal: branches of a whorl leave over a short span of stem.
            at = axis(z + b.uniform(-0.08, 0.08))
            shoot(b, meshes, tufts, at, heading, elevation, length, bend, r0, u, 1)
        z += np.clip(nrng.gamma(6, 0.045), 0.12, 0.5) * (1 - 0.35 * z / HEIGHT_M)
    cluster(nrng, axis(HEIGHT_M), UP, 1.0, tufts)
    for _ in range(6):
        z, length = nrng.uniform(CROWN_BASE_M - 1.8, CROWN_BASE_M - 0.1), nrng.uniform(0.5, 1.4)
        heading, elevation = nrng.uniform(0, 2 * np.pi), nrng.uniform(-0.3, 0.05)
        r0 = 0.006 + 0.01 * length
        shoot(nrng, meshes, tufts, axis(z), heading, elevation, length, (0.3, 0.0), r0, None, 1)
    for _ in range(16):
        z, length = nrng.uniform(1.2, CROWN_BASE_M - 0.5), nrng.uniform(0.03, 0.28)
        heading, elevation = nrng.uniform(0, 2 * np.pi), nrng.uniform(-0.25, 0.1)
        r0 = nrng.uniform(0.012, 0.028)
        shoot(nrng, meshes, tufts, axis(z), heading, elevation, length, (0.05, 0.0), r0, None, 0)
    return meshes, tufts


def tuft(nrng, length):
    """Fascicles of two needles spiralling along a shoot on local +Z from the origin.

    Older needles spread wide; the current year's stay appressed into a terminal brush. The
    shoot maps to the texture's brown sheath row, so a tuft needs one material.
    """
    count = int(FASCICLES_PER_M * length)
    s = np.sort(nrng.uniform(0.012, length, count))
    t = s / length
    azimuth = np.arange(count) * GOLDEN + nrng.normal(0, 0.25, count)
    spread = np.radians(np.interp(t, [0, 0.7, 1], [80, 68, 40]) + nrng.normal(0, 7, count))
    needle = nrng.uniform(*NEEDLE_M, count) * np.interp(t, [0, 0.8, 1], [1, 1, 0.6])
    old = (t < 0.3) & (nrng.random(count) < 0.08)
    column = np.where(old, nrng.integers(6, 8, count), nrng.integers(0, 6, count))
    radial = np.stack([np.cos(azimuth), np.sin(azimuth), np.zeros(count)], axis=1)
    tangential = np.cross(UP, radial)
    base = radial * np.interp(t, [0, 1], [SHOOT_M, 0.0028])[:, None] + s[:, None] * UP
    direction = np.cos(spread)[:, None] * UP + np.sin(spread)[:, None] * radial
    # Fascicle pairs splay 0.1 rad either side about the tangential axis.
    pair = [
        np.cos(a) * direction + np.sin(a) * np.cross(tangential, direction) for a in (-0.1, 0.1)
    ]
    direction, p1 = np.concatenate(pair), np.concatenate([tangential, tangential])
    base, needle, column = (np.concatenate([x, x]) for x in (base, needle, column))
    angle = np.arange(3) * 2 * np.pi / 3
    p2 = np.cross(direction, p1)
    ring = np.cos(angle)[None, :, None] * p1[:, None] + np.sin(angle)[None, :, None] * p2[:, None]
    # Three-sided needles taper to a point: three triangles each, the base hidden in the shoot.
    tip = base + direction * needle[:, None]
    verts = np.concatenate([base[:, None] + NEEDLE_RADIUS_M * ring, tip[:, None]], axis=1)
    local = np.array([[0, 1, 3], [1, 2, 3], [2, 0, 3]])
    tris = (np.arange(len(base))[:, None, None] * 4 + local[None]).reshape(-1, 3)
    uv = np.stack([np.repeat((column + 0.5) / 8, 4), np.tile([0.0, 0, 0, 1], len(base))], 1)
    z = np.linspace(0, length + 0.012, 12)
    stem = tube(
        z[:, None] * UP, np.interp(z, [0, length, length + 0.012], [SHOOT_M, 0.0028, 0.0005]), 6
    )
    sheath = (stem[0], stem[1], np.broadcast_to([0.5, 0.02], stem[2].shape))
    return gather([(verts.reshape(-1, 3), tris, uv[tris]), sheath])


def gather(meshes):
    """Concatenate (verts, tris, per-loop uv) meshes."""
    offsets = np.cumsum([0] + [len(v) for v, _, _ in meshes[:-1]])
    verts = np.concatenate([v for v, _, _ in meshes])
    tris = np.concatenate([t + o for (_, t, _), o in zip(meshes, offsets)])
    return verts, tris, np.concatenate([w.reshape(-1, 3, 2) for _, _, w in meshes])


def realize(parts, tufts):
    """Every tuft instance copied into the build frame."""
    index = np.array([k for k, _, _ in tufts])
    matrices = np.array([m for _, m, _ in tufts])
    offsets = np.array([t for _, _, t in tufts])
    meshes = []
    for k, (verts, tris, uv) in enumerate(parts):
        chosen = index == k
        count = int(chosen.sum())
        assert count, k
        world = np.einsum("nij,vj->nvi", matrices[chosen], verts) + offsets[chosen][:, None]
        shift = (np.arange(count) * len(verts))[:, None, None]
        meshes.append(
            (world.reshape(-1, 3), (tris + shift).reshape(-1, 3), np.tile(uv, (count, 1, 1)))
        )
    return meshes


def flat(mesh):
    """emit() takes UVs per loop as one flat list."""
    return mesh[0], mesh[1], mesh[2].reshape(-1, 2)


def lodgepole(rng, output):
    nrng = np.random.default_rng(rng.getrandbits(64))
    bark = bark_maps(nrng)
    bark_mat = material("LodgepoleMatureBark", bark, output, relief=BARK_RELIEF_M / 1.45)
    needle_mat = material("LodgepoleMatureFoliage", foliage_maps(), output)
    meshes, tufts = crown(nrng)
    assert len(tufts) <= TUFTS_MAX, len(tufts)
    twig = [(TWIG + 0.5) / 8, 0.5]
    twigs = [(v, t, np.broadcast_to(twig, w.shape)) for v, t, w in meshes["twig"]]
    parts = [tuft(nrng, length) for length in TUFT_M]
    names = [f"LodgepoleTuft{k}" for k in range(len(parts))]
    library = emit({n: flat(p) for n, p in zip(names, parts)}, dict.fromkeys(names, needle_mat))
    for obj in library:
        obj.select_set(True)
    assert bpy.ops.export_scene.gltf(
        filepath=str(output / "tufts.glb"),
        export_format="GLB",
        use_selection=True,
        export_image_format="NONE",
        export_yup=True,
    ) == {"FINISHED"}
    for obj in library:
        mesh = obj.data
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
    instances = [
        {"part": k, "matrix": m.round(6).tolist(), "translationM": t.round(6).tolist()}
        for k, m, t in tufts
    ]
    (output / "tufts.json").write_text(json.dumps({"instances": instances}) + "\n")
    # Twigs are separate from the tufts so an assembly can keep them as a single part.
    objects = emit(
        {
            "LodgepoleBark": flat(gather([trunk(nrng, bark[3]), *meshes["bark"]])),
            "LodgepoleTwigs": flat(gather(twigs)),
            "LodgepoleTufts": flat(gather(realize(parts, tufts))),
        },
        {"LodgepoleBark": bark_mat, "LodgepoleTwigs": needle_mat, "LodgepoleTufts": needle_mat},
    )
    for obj in objects:
        obj.data.set_sharp_from_angle(angle=math.pi)
    triangles = sum(len(obj.data.polygons) for obj in objects)
    assert triangles <= TRIANGLES_MAX, triangles
    return objects, {
        "assembly": {
            "base": "LodgepoleBark",
            "whole": ["LodgepoleTwigs"],
            "realized": "LodgepoleTufts",
            "parts": names,
            "partTriangles": [len(p[1]) for p in parts],
            "instances": len(tufts),
            "glb": digest(output / "tufts.glb"),
            "json": digest(output / "tufts.json"),
        },
        "barkTexelsPerMetreAtBreastHeight": BARK_PX / (math.pi * DBH_M),
    }


if __name__ == "__main__":
    main(Path(__file__).resolve(), {"lodgepole-mature": lodgepole})

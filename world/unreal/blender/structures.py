"""Build valley structures from real-world dimensions and CC0 materials.

blender -b --factory-startup --python world/unreal/blender/structures.py -- <asset> --output <dir>
Writes <asset>.blend, <asset>.glb, derived albedo PNGs and build.json into a new .local directory.
The ranger cabin, the snow survey hut and the fire lookout cab are one cabin kit with different
parameters. Every asset but the cabin is one render mesh SM_<Name> plus box hulls UCX_SM_<Name>_##
that Unreal Interchange imports as its collision.
"""

import math
import sys
from collections import namedtuple
from functools import partial
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from world.unreal.blender.common import (  # noqa: E402
    TEXTURES,
    add,
    beam,
    box,
    emit,
    frame,
    image,
    lin,
    main,
    matched,
    pixels,
    plank_bands,
    principled,
)
from world.unreal.blender.spec import load  # noqa: E402

# Cabin kit frame: -Y is the front, y = 0 the front wall, x = 0 the ridge line. A kit has the body
# outside the siding (w across the ridge, l along it), the sill-beam base `plinth` (the top of the
# stone ring), floor, wall plate and ceiling heights, roof pitch and overhangs, the openings per
# wall with their casing width, the stone ring's mesh step (column, row) or None, and whether it
# has the porch and a hip roof.
Kit = namedtuple(
    "Kit", "w l plinth floor plate ceiling pitch eave rake openings casing porch stone hip"
)
SEAT = 0.12
PORCH_PITCH = math.radians(30)
POST, POST_Y, DECK_Y = 0.30, -1.40, -1.58
HEADER = (2.87, 3.12)
PORCH_POSTS = (-2.735, 2.02, 2.735)
NEWEL_X = -0.50
STAIR = (-0.42, 1.90)
TREAD = 0.28
EXPOSURE, BOARD_T = 0.19, 0.022
SEAM_SPACING = 0.40
TURN = Matrix.Rotation(math.pi / 2, 4, "Z")  # kit front (-Y) to the asset front (+X)
# The cabin body outside the siding and its opening layout come from the camera/proportion fit of
# the approved reference; heights follow standard framing and stair practice.
CABIN = Kit(
    w=5.72,
    l=6.90,
    plinth=0.50,
    floor=0.65,
    plate=3.20,
    ceiling=3.0,
    pitch=math.radians(40),
    eave=0.40,
    rake=0.30,
    openings={
        # wall: [(kind, centre along the wall, rough width, sill height, rough height)]
        "front": [("door", 0.26, 0.97, 0.65, 2.16), ("window", -1.47, 0.96, 1.70, 0.98)],
        "right": [("window", 2.07, 0.74, 1.60, 1.15), ("window", 4.72, 0.74, 1.60, 1.15)],
        "rear": [("window", 0.0, 0.74, 1.60, 1.15)],
        "left": [
            ("window", 6.90 - 2.07, 0.74, 1.60, 1.15),
            ("window", 6.90 - 4.72, 0.74, 1.60, 1.15),
        ],
    },
    casing=0.11,
    porch=True,
    stone=(0.012, 0.0125),
    hip=False,
)
# Snow survey hut: the kit at shelter scale on a taller stone ring, without the porch, under a
# 40 degree snow roof; a 2.05 m door and sills 0.92 m above the floor.
HUT = CABIN._replace(
    w=3.6,
    l=4.2,
    plinth=0.75,
    floor=0.93,
    plate=3.10,
    ceiling=2.90,
    eave=0.35,
    stone=(0.02, 0.02),
    openings={
        "front": [("door", 0.45, 0.90, 0.93, 2.05), ("window", -1.05, 0.66, 1.85, 0.90)],
        "right": [("window", 2.1, 0.74, 1.85, 0.90)],
        "rear": [],
        "left": [("window", 2.1, 0.74, 1.85, 0.90)],
    },
    porch=False,
)
# Fire lookout cab: the kit as a square glazed cab on the catwalk under a hip roof with 1.1 m
# boxed eaves; six double-hung units a side in narrow casings, the door next to the stair head.
DECK, UNITS, SILL, GLAZING = 7.0, (-2.15, -1.29, -0.43, 0.43, 1.29, 2.15), 0.95, 1.55
CAB = CABIN._replace(
    w=5.5,
    l=5.5,
    plinth=DECK - 0.18,
    floor=DECK,
    plate=9.82,
    ceiling=9.62,
    pitch=math.radians(24),
    eave=1.1,
    openings={
        "front": [("window", a, 0.655, DECK + SILL, GLAZING) for a in UNITS[:4]]
        + [("door", 1.72, 0.84, DECK, 2.16)],
        "right": [("window", 2.75 + a, 0.655, DECK + SILL, GLAZING) for a in UNITS],
        "rear": [("window", a, 0.655, DECK + SILL, GLAZING) for a in UNITS],
        "left": [("window", 2.75 + a, 0.655, DECK + SILL, GLAZING) for a in UNITS],
    },
    casing=0.05,
    porch=False,
    stone=None,
    hip=True,
)
FASCIA = 0.36
# Reference-region sRGB mean and luminance standard deviation on the approved master.
TARGETS = {
    "timber": ((172, 157, 143), 24),
    "siding": ((123, 116, 110), 22),
    "stone": ((168, 154, 142), 37),
    "deck": ((138, 127, 119), 32),
}
CABIN_ROOF = (((84, 92, 85), 0.42), ((80, 89, 82), 0.46), ((88, 95, 88), 0.39))
HUT_ROOF = (((74, 72, 68), 0.50), ((70, 68, 64), 0.53), ((78, 75, 70), 0.47))
LOOKOUT_ROOF = (((128, 140, 128), 0.42), ((122, 134, 122), 0.46), ((132, 144, 131), 0.40))
# Cabin targets rescaled by the lookout master's leg, cab-board and tread regions over the first
# studio render's.
LOOKOUT_TARGETS = {
    "timber": ((150, 133, 123), 24),
    "siding": ((109, 100, 97), 22),
    "deck": ((105, 90, 85), 30),
}
PLANK_TILE = 3.06  # metres per texture width so a 0.14 m scanned plank spans a 0.19 m board


def roof_under(k, x):
    return k.plate + SEAT + (k.w / 2 - abs(x)) * math.tan(k.pitch)


def porch_under(k, x):
    return HEADER[1] + 0.03 + (k.w / 2 - abs(x)) * math.tan(PORCH_PITCH)


def under_beam(parts, under, pitch, x0, x1, y, width, depth, rng, **kwargs):
    """Timber in the x-z plane at `y` whose top face lies on the underside `under`."""
    sx = 1 if x1 + x0 >= 0 else -1
    normal = Vector((sx * math.sin(pitch), 0, math.cos(pitch)))
    a = Vector((x0, y, under(x0))) - normal * depth / 2
    b = Vector((x1, y, under(x1))) - normal * depth / 2
    beam(parts, "timber", a, b, width, depth, normal, rng, **kwargs)


def walls(k):
    """Outer siding planes: origin at a = 0, unit along-wall axis, outward normal, extent."""
    return {
        "front": (Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((0, -1, 0)), (-k.w / 2, k.w / 2)),
        "right": (Vector((k.w / 2, 0, 0)), Vector((0, 1, 0)), Vector((1, 0, 0)), (0, k.l)),
        "rear": (Vector((0, k.l, 0)), Vector((-1, 0, 0)), Vector((0, 1, 0)), (-k.w / 2, k.w / 2)),
        "left": (Vector((-k.w / 2, k.l, 0)), Vector((0, -1, 0)), Vector((-1, 0, 0)), (0, k.l)),
    }


def siding(parts, k, rng, bands):
    for name, (origin, along, out, (a0, a1)) in walls(k).items():
        lo, hi = a0 + 0.13, a1 - 0.13
        holes = [(c - w / 2, c + w / 2, z, z + h) for _, c, w, z, h in k.openings[name]]
        z0 = k.floor if name == "front" and k.porch else k.plinth + 0.18
        gable = name in ("front", "rear") and not k.hip
        top = roof_under(k, 0) if gable else k.plate - 0.14
        row = 0
        while z0 + row * EXPOSURE < top:
            zb = z0 + row * EXPOSURE
            height = EXPOSURE - 0.006 + rng.uniform(-0.004, 0.004)
            row += 1
            spans = [(lo, hi)]
            for h0, h1, hz0, hz1 in holes:
                if zb < hz1 and zb + height > hz0:
                    # Courses crossing a sill or head keep the part outside the opening.
                    for p0, p1 in ((zb, hz0), (hz1, zb + height)):
                        if p1 - p0 > 0.015:
                            centre = origin + along * ((h0 + h1) / 2) + out * (BOARD_T / 2)
                            centre.z = (p0 + p1) / 2
                            box(
                                parts,
                                "siding",
                                (h1 - h0, BOARD_T, p1 - p0),
                                frame(centre, along, out),
                                rng,
                                bevel=0.003,
                                tile=PLANK_TILE,
                                band=rng.choice(bands),
                            )
                    spans = [
                        s
                        for a, b in spans
                        for s in ((a, min(b, h0)), (max(a, h1), b))
                        if s[1] - s[0] > 0.02
                    ]
            for a, b in spans:
                cuts = [a]
                while b - cuts[-1] > 3.4:
                    cuts.append(cuts[-1] + rng.uniform(1.6, 3.2))
                cuts.append(b)
                for s0, s1 in zip(cuts, cuts[1:]):
                    s0, s1 = s0 + 0.002, s1 - 0.002
                    centre = (
                        origin
                        + along * ((s0 + s1) / 2)
                        + out * (BOARD_T / 2 + rng.uniform(0, 0.003))
                    )
                    centre.z = zb + height / 2
                    tilt = Matrix.Rotation(rng.uniform(-0.006, 0.006), 4, along)
                    matrix = Matrix.Translation(centre) @ tilt @ frame((0, 0, 0), along, out)
                    clip = ()
                    if gable:
                        apex = Vector((0, 0, roof_under(k, 0) - 0.01))
                        clip = tuple(
                            (apex, Vector((s * math.sin(k.pitch), 0, math.cos(k.pitch))))
                            for s in (-1, 1)
                        )
                    box(
                        parts,
                        "siding",
                        (s1 - s0, BOARD_T, height),
                        matrix,
                        rng,
                        bevel=0.003,
                        tile=PLANK_TILE,
                        clip=clip,
                        band=rng.choice(bands),
                    )
        sheathing(parts, k, name, origin, along, out, a0, a1, holes, rng)


def panel(parts, material, origin, along, out, a0, a1, z0, z1, holes, depth, rng):
    """Flat wall panel with rectangular holes, facing `out`, set `depth` behind the siding face."""
    xs = sorted({a0, a1, *[v for h in holes for v in h[:2] if a0 < v < a1]})
    zs = sorted({z0, z1, *[v for h in holes for v in h[2:] if z0 < v < z1]})
    for xa, xb in zip(xs, xs[1:]):
        for za, zb in zip(zs, zs[1:]):
            mid = ((xa + xb) / 2, (za + zb) / 2)
            if any(h[0] <= mid[0] <= h[1] and h[2] <= mid[1] <= h[3] for h in holes):
                continue
            centre = origin + along * mid[0] - out * depth
            centre.z = mid[1]
            box(parts, material, (xb - xa, 0.01, zb - za), frame(centre, along, out), rng, bevel=0)


def sheathing(parts, k, name, origin, along, out, a0, a1, holes, rng):
    depth = BOARD_T + 0.006
    panel(parts, "sheathing", origin, along, out, a0, a1, k.plinth, k.plate, holes, depth, rng)
    panel(
        parts,
        "interior",
        origin,
        along,
        -out,
        a0 + 0.15,
        a1 - 0.15,
        k.floor,
        k.ceiling,
        holes,
        -0.145,
        rng,
    )
    if name in ("front", "rear") and not k.hip:
        bm = bmesh.new()
        uv = bm.loops.layers.uv.new("UVMap")
        corners = [(-k.w / 2, k.plate), (k.w / 2, k.plate), (0, roof_under(k, 0))]
        verts = [bm.verts.new(Vector((x, origin.y, z)) - out * depth) for x, z in corners]
        face = bm.faces.new(verts if name == "front" else verts[::-1])
        for loop in face.loops:
            loop[uv].uv = (loop.vert.co.x, loop.vert.co.z)
        add(parts, "sheathing", bm)


def openings(parts, k, rng):
    for name, (origin, along, out, _) in walls(k).items():
        for kind, c, w, z, h in k.openings[name]:

            def at(a, zz, d):
                return Vector((*(origin + along * a + out * d).xy, zz))

            def trim(material, size, a, zz, d, **kwargs):
                box(parts, material, size, frame(at(a, zz, d), along, out), rng, **kwargs)

            # Jambs line the 0.145 m wall reveal; timber casing sits proud of the siding.
            for a in (c - w / 2 + 0.015, c + w / 2 - 0.015):
                trim("paint", (0.03, 0.15, h), a, z + h / 2, -0.07, bevel=0.002)
            trim("paint", (w, 0.15, 0.03), c, z + h - 0.015, -0.07, bevel=0.002)
            casing = k.casing
            bottom = z if kind == "door" else z - 0.05
            for a in (c - w / 2 - casing / 2, c + w / 2 + casing / 2):
                size = (casing, 0.03, h + 0.05 + (z - bottom))
                trim("timber", size, a, (bottom + z + h + 0.05) / 2, BOARD_T + 0.015, segments=2)
            size = (w + 2 * casing + 0.06, 0.035, 0.15)
            trim("timber", size, c, z + h + 0.075, BOARD_T + 0.0175, segments=2)
            size = (w + 2 * casing + 0.10, 0.07, 0.03)
            trim("timber", size, c, z + h + 0.165, BOARD_T + 0.035, segments=2)
            if kind == "door":
                door(parts, at, along, out, c, w - 0.06, z, h - 0.03, rng)
                continue
            trim("timber", (w + 2 * casing + 0.08, 0.10, 0.045), c, z - 0.0225, 0.02, segments=2)
            trim("timber", (w + 2 * casing, 0.025, 0.08), c, z - 0.085, BOARD_T + 0.0125)
            half = (h - 0.03) / 2
            sash(
                parts, at, along, out, c, w - 0.06, z + half - 0.02, half + 0.02, -0.075, 2, 2, rng
            )
            sash(parts, at, along, out, c, w - 0.06, z, half + 0.02, -0.035, 2, 1, rng)


def sash(parts, at, along, out, c, w, z0, h, d, cols, rows, rng):
    stile, rail, t = 0.05, 0.05, 0.04

    def paint(size, a, zz, bevel):
        box(parts, "paint", size, frame(at(a, zz, d), along, out), rng, bevel=bevel)

    for a in (c - w / 2 + stile / 2, c + w / 2 - stile / 2):
        paint((stile, t, h), a, z0 + h / 2, 0.003)
    for zz in (z0 + rail / 2, z0 + h - rail / 2):
        paint((w, t, rail), c, zz, 0.003)
    inner_w, inner_h = w - 2 * stile, h - 2 * rail
    for i in range(1, cols):
        paint((0.02, 0.025, inner_h), c - inner_w / 2 + inner_w * i / cols, z0 + h / 2, 0.002)
    for j in range(1, rows):
        paint((inner_w, 0.025, 0.02), c, z0 + rail + inner_h * j / rows, 0.002)
    glass = frame(at(c, z0 + h / 2, d - 0.005), along, out)
    box(parts, "glass", (inner_w, 0.006, inner_h), glass, rng, bevel=0)


def door(parts, at, along, out, c, w, z, h, rng):
    d, t = -0.05, 0.045
    stile, top, lock, bottom = 0.12, 0.12, 0.16, 0.22
    for a in (c - w / 2 + stile / 2, c + w / 2 - stile / 2):
        box(parts, "paint", (stile, t, h), frame(at(a, z + h / 2, d), along, out), rng, bevel=0.003)
    lock_z = z + 1.0
    for zz, rh in ((z + h - top / 2, top), (lock_z, lock), (z + bottom / 2, bottom)):
        rail = frame(at(c, zz, d), along, out)
        box(parts, "paint", (w - 2 * stile, t, rh), rail, rng, bevel=0.003)
    inner = w - 2 * stile
    glass_z0, glass_z1 = lock_z + lock / 2, z + h - top
    sash(parts, at, along, out, c, inner, glass_z0, glass_z1 - glass_z0, d, 2, 3, rng)
    boards, low0, low1 = 5, z + bottom, lock_z - lock / 2
    for i in range(boards):
        a = c - inner / 2 + inner * (i + 0.5) / boards
        board = frame(at(a, (low0 + low1) / 2, d - 0.004), along, out)
        size = (inner / boards - 0.006, 0.03, low1 - low0)
        box(parts, "paint", size, board, rng, bevel=0.004, segments=2)
    knob = at(c - w / 2 + 0.075, z + 0.95, -0.0215)
    box(parts, "iron", (0.05, 0.012, 0.16), frame(knob, along, out), rng, bevel=0.004, segments=2)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=0.028)
    bm.loops.layers.uv.new("UVMap")
    bm.transform(Matrix.Translation(knob + out * 0.03))
    add(parts, "iron", bm)


def stone_plinth(parts, k, rng):
    """One continuous ring, displaced outward from the scanned stone height map."""
    disp = image(TEXTURES / "stone_wall/stone_wall_disp_4k.png")
    # Flip so row 0 is the image top, as the plinth's UV lookup expects.
    height = pixels(disp)[::-1, :, 0].copy()
    bpy.data.images.remove(disp)
    y0 = DECK_Y + 0.06 if k.porch else -0.06
    x0, x1, y1 = -k.w / 2 - 0.06, k.w / 2 + 0.06, k.l + 0.06
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edges = [(Vector((*corners[i], 0)), Vector((*corners[(i + 1) % 4], 0))) for i in range(4)]
    perimeter = sum((b - a).length for a, b in edges)
    step, rows, tile, relief = k.stone[0], int(round(k.plinth / k.stone[1])), 3.2, 0.035
    columns = int(perimeter / step)
    hh, hw = height.shape
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    grid = []
    for i in range(columns):
        s = i * perimeter / columns
        for a, b in edges:
            if s <= (b - a).length:
                grid.append(a + (b - a).normalized() * s)
                break
            s -= (b - a).length
    assert len(grid) == columns
    normals = []
    for i in range(columns):
        tangent = (grid[(i + 1) % columns] - grid[i - 1]).normalized()
        normals.append(Vector((tangent.y, -tangent.x, 0)))
    verts = []
    for i, (p, n) in enumerate(zip(grid, normals)):
        u = i * perimeter / columns / tile
        column = []
        for j in range(rows + 1):
            z = k.plinth * j / rows
            v = z / tile
            sample = height[int((1 - v % 1) * (hh - 1)), int((u % 1) * (hw - 1))]
            taper = min(1.0, (k.plinth - z) / 0.04)
            column.append(
                (bm.verts.new(p + n * (relief * sample - 0.02) * taper + Vector((0, 0, z))), (u, v))
            )
        verts.append(column)
    for i in range(columns):
        a, b = verts[i], verts[(i + 1) % columns]
        for j in range(rows):
            face = bm.faces.new((a[j][0], b[j][0], b[j + 1][0], a[j + 1][0]))
            for loop, (u, v) in zip(face.loops, (a[j][1], b[j][1], b[j + 1][1], a[j + 1][1])):
                if i == columns - 1 and loop.vert in (b[j][0], b[j + 1][0]):
                    u = perimeter / tile
                loop[uv].uv = (u, v)
    bm.normal_update()
    bm.faces.ensure_lookup_table()
    if bm.faces[0].normal.dot(normals[0]) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    add(parts, "stone", bm)
    cap = bmesh.new()
    cap_uv = cap.loops.layers.uv.new("UVMap")
    inset = 0.3
    outer = [Vector((x, y, k.plinth)) for x, y in corners]
    inner = [
        Vector((x - math.copysign(inset, x), y - math.copysign(inset, y - (y0 + y1) / 2), k.plinth))
        for x, y in corners
    ]
    vo, vi = [cap.verts.new(v) for v in outer], [cap.verts.new(v) for v in inner]
    for i in range(4):
        f = cap.faces.new((vo[i], vo[(i + 1) % 4], vi[(i + 1) % 4], vi[i]))
        for loop in f.loops:
            loop[cap_uv].uv = (loop.vert.co.x / tile, loop.vert.co.y / tile)
    cap.normal_update()
    cap.faces.ensure_lookup_table()
    if cap.faces[0].normal.z < 0:
        bmesh.ops.reverse_faces(cap, faces=cap.faces[:])
    add(parts, "stone", cap)


def frame_timbers(parts, k, rng):
    def timber(size, centre, **kwargs):
        box(parts, "timber", size, Matrix.Translation(centre), rng, **kwargs)

    # Corner posts, sill beam and frieze of the body.
    for sx in (-1, 1):
        for y in (0.055, k.l - 0.055):
            centre = (sx * (k.w / 2 - 0.055), y, (k.plinth + k.plate) / 2)
            timber((0.16, 0.16, k.plate - k.plinth), centre, segments=2, grain=2)
        timber((0.07, k.l + 0.06, 0.18), (sx * (k.w / 2 + 0.03), k.l / 2, k.plinth + 0.09), grain=1)
        timber((0.05, k.l - 0.2, 0.14), (sx * (k.w / 2 + 0.025), k.l / 2, k.plate - 0.07), grain=1)
    for y in (k.l + 0.03,) if k.porch else (-0.03, k.l + 0.03):
        timber((k.w + 0.06, 0.07, 0.18), (0, y, k.plinth + 0.09), grain=0)
    if k.hip:
        for y in (-0.025, k.l + 0.025):
            timber((k.w - 0.2, 0.05, 0.14), (0, y, k.plate - 0.07), grain=0)
        return
    under = partial(roof_under, k)
    # Main roof: rafter tails, fascia and barge rafters.
    for sx in (-1, 1):
        y = 0.25
        while y < k.l - 0.15:
            x0, x1 = sx * (k.w / 2 - 0.02), sx * (k.w / 2 + k.eave - 0.05)
            under_beam(parts, under, k.pitch, x0, x1, y, 0.05, 0.14, rng)
            y += 0.61
        xf = sx * (k.w / 2 + k.eave - 0.03)
        timber((0.04, k.l + 2 * k.rake, 0.2), (xf, k.l / 2, under(xf) - 0.09), grain=1)
        for yb in (-k.rake + 0.04, k.l + k.rake - 0.04):
            x0, x1 = sx * 0.001, sx * (k.w / 2 + k.eave)
            under_beam(parts, under, k.pitch, x0, x1, yb, 0.08, 0.24, rng, segments=2)
        # Purlins carrying the rake overhang.
        for frac in (0.35, 0.75):
            x = sx * (k.w / 2) * frac
            for y0, y1 in ((-k.rake + 0.08, 0.2), (k.l - 0.2, k.l + k.rake - 0.08)):
                timber((0.1, y1 - y0, 0.14), (x, (y0 + y1) / 2, under(x) - 0.115), grain=1)
    if k.porch:
        # Front gable king post and collar visible above the porch roof.
        zc = under(0) - 0.32
        xc = k.w / 2 - (zc - k.plate - SEAT) / math.tan(k.pitch)
        timber((2 * xc, 0.12, 0.14), (0, -0.12, zc), segments=2)
        size = (0.16, 0.14, under(0) - zc - 0.05)
        timber(size, (0, -0.12, (zc + under(0)) / 2 - 0.02), segments=2, grain=2)


def standing_seam(parts, rng, mid, down, along, normal, length, y0, y1, clip=()):
    """Timber deck, standing-seam panels and seams on the slope strip y0..y1 along `along`."""

    def sheet(material, size, centre, y, **kwargs):
        box(parts, material, size, frame(centre + along * y, down, along), rng, clip=clip, **kwargs)

    sheet(
        "timber",
        (length, y1 - y0, 0.025),
        mid + normal * 0.0125,
        (y0 + y1) / 2,
        bevel=0.002,
        tile=1.2,
    )
    panels = int(round((y1 - y0) / SEAM_SPACING))
    for i in range(panels):
        py0 = y0 + (y1 - y0) * i / panels
        py1 = y0 + (y1 - y0) * (i + 1) / panels
        centre = mid + normal * 0.031 + down * 0.02
        size = (length + 0.04, py1 - py0, 0.012)
        sheet(f"roof{rng.randrange(3)}", size, centre, (py0 + py1) / 2, bevel=0.002)
        if i:
            seam = mid + normal * 0.05 + down * 0.02
            size = (length + 0.04, 0.022, 0.03)
            sheet("roof0", size, seam, py0, bevel=0.006, segments=2)


def roof(parts, k, rng):
    def slope(under, pitch, x_end, y0, y1):
        along = Vector((0, 1, 0))
        for sx in (-1, 1):
            a = Vector((0, 0, under(0)))
            b = Vector((sx * x_end, 0, under(x_end)))
            down = (b - a).normalized()
            normal = Vector((sx * math.sin(pitch), 0, math.cos(pitch)))
            standing_seam(parts, rng, (a + b) / 2, down, along, normal, (b - a).length, y0, y1)
            cap = a + normal * 0.075 + down * 0.08 + along * ((y0 + y1) / 2)
            size = (0.17, y1 - y0 + 0.02, 0.012)
            box(parts, "roof0", size, frame(cap, down, along), rng, bevel=0.003)

    slope(partial(roof_under, k), k.pitch, k.w / 2 + k.eave, -k.rake, k.l + k.rake)
    if k.porch:
        y0 = POST_Y - POST / 2 - 0.30
        slope(partial(porch_under, k), PORCH_PITCH, k.w / 2 + 0.30, y0, -0.005)


def hip_roof(parts, k, rng, bands):
    """Pyramidal roof over boxed eaves: seams mitred at the hips, hip caps, fascia of two scanned
    boards a side, plank soffit and apex cap."""
    assert k.w == k.l
    half, top = k.w / 2 + k.eave, k.plate + FASCIA
    centre, z = Vector((0, k.l / 2, 0)), Vector((0, 0, 1))
    apex = centre + z * (top + half * math.tan(k.pitch))
    length = half / math.cos(k.pitch)
    for turn in range(4):
        rotation = Matrix.Rotation(turn * math.pi / 2, 3, "Z")
        u, v = rotation @ Vector((1, 0, 0)), rotation @ Vector((0, 1, 0))
        down = u * math.cos(k.pitch) - z * math.sin(k.pitch)
        normal = u * math.sin(k.pitch) + z * math.cos(k.pitch)
        clip = ((centre, v - u), (centre, -v - u))
        mid = apex + down * (length / 2)
        standing_seam(parts, rng, mid, down, v, normal, length, -half, half, clip)
        corner = centre + (u + v) * half + z * top
        beam(parts, "roof0", corner + z * 0.05, apex + z * 0.05, 0.17, 0.05, z, rng, bevel=0.006)
        for row in range(2):
            fascia = centre + u * (half - 0.02) + z * (top - FASCIA * (row + 0.5) / 2)
            size = (2 * half - 0.08 * (turn % 2), 0.04, FASCIA / 2 - 0.003)
            matrix, band = frame(fascia, v, u), rng.choice(bands)
            box(parts, "siding", size, matrix, rng, bevel=0.003, tile=PLANK_TILE, band=band)
    soffit = Matrix.Translation(centre + z * (k.plate + 0.01))
    size = (2 * half - 0.08, 2 * half - 0.08, 0.02)
    box(parts, "deck", size, soffit, rng, bevel=0, tile=2.25, grain=1)
    box(parts, "roof0", (0.26, 0.26, 0.10), Matrix.Translation(apex + z * 0.07), rng, bevel=0.03)


def railing(parts, rng, p0, p1):
    """Guard rails 0.885 m and 0.10 m above the line p0-p1, balusters under 0.1 m apart."""
    p0, p1 = Vector(p0), Vector(p1)
    length = (p1 - p0).length
    d = (p1 - p0).normalized()
    side = Vector((-d.y, d.x, 0))
    for z, t, h in ((0.885, 0.09, 0.05), (0.10, 0.07, 0.045)):
        c = (p0 + p1) / 2
        matrix = frame(Vector((c.x, c.y, c.z + z)), d, side)
        box(parts, "timber", (length, t, h), matrix, rng, bevel=0.006, segments=2)
    count = int(length / 0.135)
    for i in range(count):
        p = p0 + d * (length * (i + 0.5) / count)
        centre = Matrix.Translation((p.x, p.y, p.z + 0.495))
        box(parts, "timber", (0.04, 0.04, 0.74), centre, rng, bevel=0.004, grain=2)


def porch(parts, k, rng):
    def timber(size, centre, material="timber", **kwargs):
        box(parts, material, size, Matrix.Translation(centre), rng, **kwargs)

    # Deck boards run across the porch; rim boards close the edge above the stone.
    deck = (k.w + 0.08, -DECK_Y + 0.02, 0.04)
    timber(deck, (0, DECK_Y / 2, k.floor - 0.02), "deck", bevel=0.002, tile=2.25, grain=1)
    timber((k.w + 0.08, 0.035, 0.15), (0, DECK_Y + 0.0175, k.floor - 0.115), grain=0)
    for sx in (-1, 1):
        centre = (sx * (k.w / 2 + 0.055), DECK_Y / 2, k.floor - 0.115)
        timber((0.035, -DECK_Y, 0.15), centre, grain=1)
    for x in PORCH_POSTS:
        size, centre = (POST, POST, HEADER[0] - k.floor), (x, POST_Y, (k.floor + HEADER[0]) / 2)
        timber(size, centre, bevel=0.012, segments=2, grain=2)
    hz, hh = (HEADER[0] + HEADER[1]) / 2, HEADER[1] - HEADER[0]
    timber((k.w + 0.26, 0.22, hh), (0, POST_Y, hz), bevel=0.01, segments=2)
    for sx in (-1, 1):
        centre = (sx * PORCH_POSTS[2], POST_Y / 2 + 0.05, hz)
        timber((0.22, -POST_Y - 0.1, hh), centre, bevel=0.01, segments=2, grain=1)
    # King-post truss on the header and exposed rafters under the porch roof.
    under = partial(porch_under, k)
    apex = under(0)
    size, centre = (0.2, 0.2, apex - HEADER[1] - 0.02), (0, POST_Y, (apex + HEADER[1]) / 2 - 0.01)
    timber(size, centre, bevel=0.01, segments=2, grain=2)
    for sx in (-1, 1):
        for y, width, depth in ((POST_Y, 0.2, 0.2), (-0.72, 0.07, 0.17), (-0.12, 0.07, 0.17)):
            x0, x1 = sx * 0.001, sx * (k.w / 2 + 0.28)
            under_beam(parts, under, PORCH_PITCH, x0, x1, y, width, depth, rng, segments=2)
        x0, x1, yb = sx * 0.001, sx * (k.w / 2 + 0.30), POST_Y - POST / 2 - 0.30 + 0.04
        under_beam(parts, under, PORCH_PITCH, x0, x1, yb, 0.08, 0.22, rng, segments=2)

    # Railings: 0.91 m guard height, balusters at under 0.1 m clear spacing.
    inner, f = POST / 2, k.floor
    railing(parts, rng, (PORCH_POSTS[0] + inner, POST_Y, f), (NEWEL_X - 0.075, POST_Y, f))
    railing(parts, rng, (PORCH_POSTS[1] + inner, POST_Y, f), (PORCH_POSTS[2] - inner, POST_Y, f))
    for x in (PORCH_POSTS[0], PORCH_POSTS[2]):
        railing(parts, rng, (x, POST_Y + inner, f), (x, -0.03, f))
    timber((0.15, 0.15, 1.0), (NEWEL_X, POST_Y, k.floor + 0.5), bevel=0.01, segments=2, grain=2)
    timber((0.19, 0.19, 0.04), (NEWEL_X, POST_Y, k.floor + 1.02), bevel=0.008, segments=2)
    # Stairs: three treads and four risers between grade and the deck.
    x0, x1 = STAIR
    width, riser = x1 - x0, k.floor / 4
    for i in range(1, 4):
        top = k.floor - i * riser
        ya, yb = DECK_Y - TREAD * i - 0.025, DECK_Y - TREAD * (i - 1)
        size, centre = (width, yb - ya, 0.04), ((x0 + x1) / 2, (ya + yb) / 2, top - 0.02)
        timber(size, centre, "deck", bevel=0.004, segments=2, tile=2.25, grain=1)
        for x in (x0 + 0.02, x1 - 0.02):
            size, centre = (
                (0.04, yb - ya - 0.03, top - 0.04),
                (x, (ya + yb) / 2 + 0.015, top / 2 - 0.02),
            )
            timber(size, centre, grain=1)
    for i in range(2, 5):
        top = k.floor - (i - 1) * riser - 0.04
        bottom = k.floor - i * riser if i < 4 else 0
        y = DECK_Y - TREAD * (i - 1)
        timber((width - 0.08, 0.025, top - bottom), ((x0 + x1) / 2, y + 0.0125, (top + bottom) / 2))


def kit(parts, k, rng, bands):
    """Stone ring, siding, openings, frame, porch, roof and interior planes of a kit."""
    if k.stone:
        stone_plinth(parts, k, rng)
    siding(parts, k, rng, bands)
    openings(parts, k, rng)
    frame_timbers(parts, k, rng)
    if k.porch:
        porch(parts, k, rng)
    if k.hip:
        hip_roof(parts, k, rng, bands)
    else:
        roof(parts, k, rng)
    for z in (k.floor - 0.01, k.ceiling):
        centre = Matrix.Translation((0, k.l / 2, z))
        box(parts, "interior", (k.w - 0.3, k.l - 0.3, 0.02), centre, rng, bevel=0)


def cabin_materials(output, targets, roofs):
    planks = TEXTURES / "plank_flooring_03/plank_flooring_03_diff_4k.jpg"
    derived = {}
    albedo = {}
    for name, path in (
        ("timber", TEXTURES / "rough_wood/rough_wood_diff_4k.jpg"),
        ("stone", TEXTURES / "stone_wall/stone_wall_diff_4k.jpg"),
        ("deck", planks),
        ("siding", planks),
    ):
        albedo[name], derived[name] = matched(path, targets[name], f"{name}_albedo", output)
    plank_rough = image(TEXTURES / "plank_flooring_03/plank_flooring_03_rough_4k.jpg")
    plank_normal = image(TEXTURES / "plank_flooring_03/plank_flooring_03_nor_gl_4k.jpg")
    wood_normal = image(TEXTURES / "rough_wood/rough_wood_nor_gl_4k.jpg")
    mats = {
        "timber": principled(
            "Weathered timber",
            base_image=albedo["timber"],
            rough_image=image(TEXTURES / "rough_wood/rough_wood_rough_4k.jpg"),
            normal_image=wood_normal,
            strength=1.2,
        ),
        "stone": principled(
            "Granite plinth",
            base_image=albedo["stone"],
            rough_image=image(TEXTURES / "stone_wall/stone_wall_rough_4k.jpg"),
            normal_image=image(TEXTURES / "stone_wall/stone_wall_nor_gl_4k.jpg"),
        ),
        "deck": principled(
            "Porch decking",
            base_image=albedo["deck"],
            rough_image=plank_rough,
            normal_image=plank_normal,
        ),
        "siding": principled(
            "Weathered siding",
            base_image=albedo["siding"],
            rough_image=plank_rough,
            normal_image=plank_normal,
            strength=1.3,
        ),
        "paint": principled(
            "Dark green paint",
            base=lin((44, 50, 41)),
            rough=0.55,
            normal_image=wood_normal,
            strength=0.35,
        ),
        "glass": principled("Window glass", base=(0.9, 0.93, 0.92), rough=0.03, transmission=1.0),
        "interior": principled("Interior", base=lin((38, 32, 27)), rough=0.85),
        "sheathing": principled("Sheathing", base=lin((30, 26, 22)), rough=0.9),
        "iron": principled("Black iron", base=lin((40, 40, 40)), rough=0.45, metallic=1.0),
    }
    for i, (colour, rough) in enumerate(roofs):
        mats[f"roof{i}"] = principled(
            f"Standing-seam roof {i}", base=lin(colour), rough=rough, metallic=0.25
        )
    return mats, plank_bands(albedo["siding"]), derived


def scan(name, asset, target, output):
    """Material from a 2K Poly Haven scan whose albedo is matched to `target`."""

    def path(kind):
        return TEXTURES / asset / f"{asset}_{kind}_2k.jpg"

    albedo, derived = matched(path("diff"), target, f"{name}_albedo", output)
    rough, normal = image(path("rough")), image(path("nor_gl"))
    return principled(name, base_image=albedo, rough_image=rough, normal_image=normal), derived


def collision(render, name, hulls, rng, material):
    """Join the render meshes as `name` and add the box hulls {key: (size, centre)}."""
    with bpy.context.temp_override(active_object=render[0], selected_editable_objects=render):
        assert bpy.ops.object.join() == {"FINISHED"}
    render = render[0]
    render.name = render.data.name = name
    parts = {}
    for i, (size, centre) in enumerate(hulls.values()):
        box(parts, f"UCX_{name}_{i:02d}", size, Matrix.Translation(centre), rng, bevel=0)
    objects = emit(parts, dict.fromkeys(parts, material))
    for obj in objects:
        assert len(obj.data.vertices) == 8, obj.name
        obj.hide_render = True
    return [render, *objects], dict(zip((o.name for o in objects), hulls))


def bolt(parts, centre, normal):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=6, radius=BOLT[0])
    bmesh.ops.scale(bm, vec=(1, 1, BOLT[1]), verts=bm.verts)
    bm.loops.layers.uv.new("UVMap")
    rotation = Vector(normal).to_track_quat("Z", "Y").to_matrix().to_4x4()
    bm.transform(Matrix.Translation(centre) @ rotation)
    add(parts, "iron", bm)


def cylinder(parts, material, base, radius, height, segments=24):
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, segments=segments, radius1=radius, radius2=radius, depth=height
    )
    bm.loops.layers.uv.new("UVMap")
    bm.transform(Matrix.Translation(Vector(base) + Vector((0, 0, height / 2))))
    add(parts, material, bm)


def cabin(rng, output):
    mats, bands, derived = cabin_materials(output, TARGETS, CABIN_ROOF)
    parts = {}
    kit(parts, CABIN, rng, bands)
    objects = emit(parts, mats)
    for obj in objects:
        obj.data.transform(TURN)
    return objects, {"derivedAlbedo": derived, "sidingPlanks": len(bands)}


def hut(rng, output):
    mats, bands, derived = cabin_materials(output, TARGETS, HUT_ROOF)
    parts = {}
    kit(parts, HUT, rng, bands)
    # Two granite block steps up to the door sill.
    door_x = HUT.openings["front"][0][1]
    for i in range(2):
        top = HUT.floor * (2 - i) / 3
        centre = Matrix.Translation((door_x, -0.23 - 0.34 * i, top / 2))
        box(parts, "stone", (1.2, 0.34, top), centre, rng, bevel=0.03, segments=2, tile=3.2)
    render = emit(parts, mats)
    for obj in render:
        obj.data.transform(TURN)
    ridge = roof_under(HUT, 0) + 0.06
    x0, x1 = -HUT.l - 0.06, 0.74
    hulls = {"hut": ((x1 - x0, HUT.w + 0.12, ridge), ((x0 + x1) / 2, 0, ridge / 2))}
    objects, hulls = collision(render, "SM_SnowSurveyHut", hulls, rng, mats["sheathing"])
    return objects, {"derivedAlbedo": derived, "sidingPlanks": len(bands), "collisionHulls": hulls}


# Footbridge build frame: z = 0 at grade on the sill timbers, y along the span, x across the deck.
# T3 terrain at the footbridge: creek bank tops 3.35 m either side of the centreline.
BANK_TOP_M = 3.35
WIDTH, SPAN, SILL_Y = 2.4, 8.6, 4.1
SILL = (0.30, 0.30, 0.05)  # along the span, height, height above grade
FOOT = (0.02, 0.05)  # base plate and bearing block
CHANNEL = (0.305, 0.075, 0.010, 0.013)  # C12x20.7: depth, flange, web, flange thickness
DECK_T, BOARD_W, GAP = 0.076, 0.20, 0.012
CURB, POST_SIZE, RAIL = 0.20, (0.15, 0.20), (0.09, 0.19)
POSTS = 5
RAIL_M = 1.07  # AASHTO pedestrian railing height, 42 in
BOLT = (0.024, 0.45)  # head radius, height over radius
BEARING = SILL[2] + sum(FOOT)
DECK_TOP = BEARING + CHANNEL[0] + DECK_T
EDGE = WIDTH / 2 - BOLT[0] * BOLT[1] - POST_SIZE[0]
POST_Z = (BEARING + 0.02, DECK_TOP + RAIL_M + 0.04)
# Cabin targets rescaled by the footbridge master's measured rail and deck regions over the
# first studio render's.
BRIDGE_TARGETS = {"timber": ((149, 140, 129), 24), "deck": ((162, 154, 145), 30)}


def footbridge(rng, output):
    buried = load("structures")["timber-footbridge"]["buriedM"]
    assert math.isclose(buried, SILL[1] - SILL[2]), buried
    clear = 2 * SILL_Y - SILL[0]
    assert clear > 2 * BANK_TOP_M and SILL_Y + SILL[0] / 2 < SPAN / 2
    mats, bands, derived = cabin_materials(output, TARGETS | BRIDGE_TARGETS, CABIN_ROOF)
    mats["steel"] = principled("Dark painted steel", base=lin((70, 72, 74)), rough=0.5)
    parts = {}

    def piece(material, size, centre, grain=0, **kwargs):
        box(parts, material, size, Matrix.Translation(centre), rng, grain=grain, **kwargs)

    depth, flange, web, flange_t = CHANNEL
    # Outer channels show their webs; flanges point inward.
    stringers = ((-EDGE, 1), (-web / 2, 1), (EDGE, -1))
    flanges = [x + inward * flange / 2 for x, inward in stringers]
    for (x, inward), cx in zip(stringers, flanges):
        centre = (x + inward * web / 2, 0, BEARING + depth / 2)
        piece("steel", (web, SPAN, depth), centre, 1, bevel=0.002)
        for z in (BEARING + flange_t / 2, BEARING + depth - flange_t / 2):
            piece("steel", (flange, SPAN, flange_t), (cx, 0, z), 1, bevel=0.002)
    for sy in (-1, 1):
        centre = (0, sy * SILL_Y, SILL[2] - SILL[1] / 2)
        piece("timber", (WIDTH, SILL[0], SILL[1]), centre, 0, bevel=0.01, segments=2)
        for cx in flanges:
            plate = (cx, sy * SILL_Y, SILL[2] + FOOT[0] / 2)
            piece("steel", (0.30, 0.24, FOOT[0]), plate, bevel=0.003)
            block = (cx, sy * SILL_Y, SILL[2] + FOOT[0] + FOOT[1] / 2)
            piece("steel", (0.14, 0.16, FOOT[1]), block, bevel=0.003)
            for dx in (-0.11, 0.11):
                bolt(parts, (cx + dx, sy * SILL_Y, SILL[2] + FOOT[0]), (0, 0, 1))
        for sx in (-1, 1):
            x, height = sx * (EDGE - 0.1), depth + DECK_T
            corner = (x, sy * (SPAN / 2 + 0.006), BEARING + height / 2)
            piece("steel", (0.2, 0.012, height), corner, bevel=0.002)
            for z in (BEARING + 0.08, BEARING + depth - 0.08):
                bolt(parts, (x, sy * (SPAN / 2 + 0.012), z), (0, sy, 0))
    boards = round((SPAN + GAP) / (BOARD_W + GAP))
    width = (SPAN - (boards - 1) * GAP) / boards
    flat = frame((0, 0, 0), (1, 0, 0), (0, 0, 1))
    for i in range(boards):
        y = -SPAN / 2 + width / 2 + i * (width + GAP)
        centre = (rng.uniform(-0.004, 0.004), y, DECK_TOP - DECK_T / 2 + rng.uniform(-0.002, 0))
        matrix = Matrix.Translation(centre) @ Matrix.Rotation(rng.uniform(-0.004, 0.004), 4, "Z")
        size = (2 * EDGE - rng.uniform(0.01, 0.02), DECK_T, width)
        box(
            parts,
            "deck",
            size,
            matrix @ flat,
            rng,
            bevel=0.005,
            tile=PLANK_TILE,
            band=rng.choice(bands),
        )
        for dy in (-0.05, 0.05):
            spike = (flanges[1] + rng.uniform(-0.01, 0.01), y + dy, DECK_TOP + 0.001)
            piece("iron", (0.014, 0.014, 0.004), spike, bevel=0.001)
    posts = [
        -SPAN / 2 + POST_SIZE[1] / 2 + i * (SPAN - POST_SIZE[1]) / (POSTS - 1) for i in range(POSTS)
    ]
    rails = (DECK_TOP + RAIL_M - RAIL[1] / 2, DECK_TOP + 0.60)
    assert (
        math.isclose(rails[0] + RAIL[1] / 2 - DECK_TOP, RAIL_M)
        and rails[1] - RAIL[1] / 2 > DECK_TOP + CURB
    )
    for sx in (-1, 1):
        curb = (sx * (EDGE - CURB / 2), 0, DECK_TOP + CURB / 2)
        piece("timber", (CURB, SPAN, CURB), curb, 1, bevel=0.012, segments=2)
        for y0, y1 in ((-SPAN / 2, -0.002), (0.002, SPAN / 2)):
            for z in rails:
                rail = (sx * (EDGE - RAIL[0] / 2), (y0 + y1) / 2, z)
                piece("timber", (RAIL[0], y1 - y0, RAIL[1]), rail, 1, bevel=0.01, segments=2)
        for y in posts:
            post = (sx * (EDGE + POST_SIZE[0] / 2), y, sum(POST_Z) / 2)
            size = (*POST_SIZE, POST_Z[1] - POST_Z[0])
            piece("timber", size, post, 2, bevel=0.012, segments=2)
            for z in (*rails, DECK_TOP + CURB / 2, BEARING + depth / 2):
                bolt(parts, (sx * (EDGE + POST_SIZE[0]), y, z), (sx, 0, 0))
    rail_x = (EDGE - CURB, EDGE + POST_SIZE[0])
    hulls = {
        "deck": ((2 * EDGE, SPAN, DECK_TOP - BEARING), (0, 0, (DECK_TOP + BEARING) / 2)),
        **{
            f"rail{sx:+d}": (
                (rail_x[1] - rail_x[0], SPAN, POST_Z[1] - DECK_TOP),
                (sx * sum(rail_x) / 2, 0, (POST_Z[1] + DECK_TOP) / 2),
            )
            for sx in (-1, 1)
        },
        **{
            f"abutment{sy:+d}": ((WIDTH, SILL[0], BEARING), (0, sy * SILL_Y, BEARING / 2))
            for sy in (-1, 1)
        },
    }
    objects, hulls = collision(
        emit(parts, mats), "SM_TimberFootbridge", hulls, rng, mats["sheathing"]
    )
    return objects, {
        "derivedAlbedo": derived,
        "stringerLengthM": SPAN,
        "bearingSpanM": 2 * SILL_Y,
        "clearSpanM": clear,
        "widthM": WIDTH,
        "clearWidthM": 2 * (EDGE - CURB),
        "deckTopM": DECK_TOP,
        "railHeightM": rails[0] + RAIL[1] / 2 - DECK_TOP,
        "deckBoards": boards,
        "boardWidthM": width,
        "boardGapM": GAP,
        "collisionHulls": hulls,
    }


# Fire lookout build frame: the tower centre at grade, the cab front and the stairs facing +X.
# Sizes follow the approved reference at about 110 px/m: 0.36 m legs battered from 2.85 to 2.6 m
# off the centre, one X brace a face over three girts, an 8 m catwalk at 7 m, and a switchback of
# 13, 12 and 12 risers tight against the +X face: the first and last flights stacked in the outer
# band, the middle one in the inner band, the first landing under the head landing.
PIER = (0.70, 0.55, 0.35, 0.25)  # base and top width, height above grade, depth below
LEG, SPREAD, CAP = 0.36, (2.85, 2.6), 6.40  # leg section, centre offset at pier top and cap
GIRTS = (0.9, 4.0, 6.25)
CATWALK = 4.0
RISE, GOING, STAIR_W = DECK / 37, 0.25, 0.90
BANDS = (3.30, 4.33, 4.37, 5.40)  # inner and outer flight bands (x); landings span both
LANDING_Y = ((0.80, 1.85), (-3.00, -1.95), (0.80, 1.85))  # first, second and head landings
LOOKOUT_CONCRETE = ((152, 147, 141), 18)


def spread(z):
    return SPREAD[0] + (SPREAD[1] - SPREAD[0]) * (z - PIER[2]) / (CAP - PIER[2])


def flight(parts, rng, foot, run, risers):
    """`risers` risers climbing along the unit `run` from `foot`, the bottom riser's centre at its
    floor, with treads, stringers and a guard railing on both sides."""
    foot, run = Vector(foot), Vector(run)
    side, up = Vector((-run.y, run.x, 0)), Vector((0, 0, 1))
    top = foot + run * (GOING * (risers - 1)) + up * (RISE * risers)
    for i in range(1, risers):
        centre = foot + run * (GOING * (i - 0.5)) + up * (RISE * i - 0.02)
        size = (GOING + 0.03, STAIR_W, 0.04)
        box(parts, "deck", size, frame(centre, run, side), rng, bevel=0.004, tile=2.25, grain=1)
    for s in (-1, 1):
        edge = side * (s * (STAIR_W / 2 + 0.03))
        start, end = foot + edge - run * 0.1, top + edge - up * 0.15
        beam(parts, "timber", start, end, 0.06, 0.28, up, rng, bevel=0.006)
        railing(parts, rng, foot + edge + up * RISE, top + edge)
    return top


def landing(parts, rng, x, y, z, edges, posts, grounded):
    """Deck on two rim joists with guard posts at the `posts` corners, those in `grounded` carried
    down to pads at grade; `edges` are the railed sides as (start, end) corner indices."""
    # Posts sit 10 mm inside the deck edge, 5 mm clear of the stringer faces: coplanar
    # overlapping faces render black in Cycles.
    i = 0.08
    corners = [
        (x[0] + i, y[0] + i),
        (x[1] - i, y[0] + i),
        (x[1] - i, y[1] - i),
        (x[0] + i, y[1] - i),
    ]
    centre = ((x[0] + x[1]) / 2, (y[0] + y[1]) / 2)
    size = (x[1] - x[0], y[1] - y[0], 0.05)
    box(parts, "deck", size, Matrix.Translation((*centre, z - 0.025)), rng, bevel=0.004, tile=2.25)
    for yr in (y[0] + 0.03, y[1] - 0.03):
        rim = Matrix.Translation((centre[0], yr, z - 0.17))
        box(parts, "timber", (x[1] - x[0], 0.06, 0.24), rim, rng, bevel=0.006)
    for i in posts:
        px, py = corners[i]
        # Grounded posts stand on pads 0.05 m above grade: only the tower sets the ground contact.
        foot = 0.05 if i in grounded else z - 0.29
        post = Matrix.Translation((px, py, (z + 1.0 + foot) / 2))
        box(parts, "timber", (0.14, 0.14, z + 1.0 - foot), post, rng, bevel=0.01, grain=2)
        if i in grounded:
            pad = Matrix.Translation((px, py, -0.05))
            box(parts, "concrete", (0.36, 0.36, 0.20), pad, rng, bevel=0.01, tile=2.71)
    for a, b in edges:
        railing(parts, rng, (*corners[a], z), (*corners[b], z))


def guard(parts, rng, start, end, posts):
    """Catwalk guard from `start` to `end`: a flat cap and two rails on posts at `posts`."""
    start, end = Vector((*start, DECK)), Vector((*end, DECK))
    d = (end - start).normalized()
    side, centre = Vector((-d.y, d.x, 0)), (start + end) / 2
    for p in posts:
        post = Matrix.Translation((*p, DECK + (RAIL_M - 0.05) / 2))
        box(parts, "timber", (0.10, 0.10, RAIL_M - 0.05), post, rng, bevel=0.008, grain=2)
    for z, t, h in ((RAIL_M - 0.025, 0.14, 0.05), (0.62, 0.09, 0.045), (0.22, 0.09, 0.045)):
        rail = frame(centre + Vector((0, 0, z)), d, side)
        box(parts, "timber", ((end - start).length, t, h), rail, rng, bevel=0.006, segments=2)


def tower(parts, rng):
    z = Vector((0, 0, 1))
    faces = [(Vector((c, s, 0)), Vector((-s, c, 0))) for c, s in ((1, 0), (0, 1), (-1, 0), (0, -1))]
    for sx in (-1, 1):
        for sy in (-1, 1):
            corner = Vector((sx, sy, 0))
            foot = corner * SPREAD[0]
            taper = []
            for u, _ in faces:
                normal = u + z * ((PIER[0] - PIER[1]) / 2 / PIER[2])
                taper.append((foot + u * (PIER[0] / 2), normal.normalized()))
            size = (PIER[0], PIER[0], PIER[2] + PIER[3])
            pier = Matrix.Translation(foot + z * ((PIER[2] - PIER[3]) / 2))
            box(parts, "concrete", size, pier, rng, bevel=0.01, tile=2.71, clip=taper)
            plate = Matrix.Translation(foot + z * (PIER[2] + 0.006))
            box(parts, "galv", (0.40, 0.40, 0.012), plate, rng, bevel=0.002)
            start, end = foot + z * (PIER[2] + 0.012), corner * SPREAD[1] + z * CAP
            beam(parts, "timber", start, end, LEG, LEG, (1, 0, 0), rng, bevel=0.012, segments=2)
            for u in (Vector((sx, 0, 0)), Vector((0, sy, 0))):
                strap = foot + u * (LEG / 2 + 0.005) + z * (PIER[2] + 0.2)
                size, matrix = (0.22, 0.01, 0.36), frame(strap, u.cross(z), u)
                box(parts, "galv", size, matrix, rng, bevel=0.002)
                for dz in (-0.08, 0.08):
                    bolt(parts, strap + u * 0.005 + z * dz, u)
    for i, (u, v) in enumerate(faces):
        for zg in GIRTS:
            girt = u * (spread(zg) + LEG / 2 + 0.05) + z * zg
            size = (2 * (spread(zg) + LEG / 2), 0.10, 0.28)
            box(parts, "timber", size, frame(girt, v, u), rng, bevel=0.008, segments=2)
        # Each face carries one X brace from the bottom girt to the top girt, the two
        # diagonals lapped one over the other.
        for k, s in enumerate((1, -1)):
            off = LEG / 2 + 0.14 + 0.08 * k
            ends = []
            for zb, sv in ((GIRTS[0], -s), (GIRTS[-1], s)):
                ends.append(u * (spread(zb) + off) + v * (sv * (spread(zb) - 0.10)) + z * zb)
            beam(parts, "timber", ends[0], ends[1], 0.24, 0.08, u, rng, bevel=0.008)
            for end in ends:
                gusset = end + u * 0.044
                box(parts, "galv", (0.30, 0.008, 0.30), frame(gusset, v, u), rng, bevel=0.002)
                for dv in (-0.08, 0.08):
                    for dz in (-0.08, 0.08):
                        bolt(parts, gusset + u * 0.004 + v * dv + z * dz, u)
        # Caps under the catwalk: along x over the legs, along y lapped below them.
        top = spread(CAP)
        if i % 2:
            size, centre = (2 * CATWALK, 0.15, 0.30), u * top + z * (CAP + 0.15)
        else:
            size, centre = (0.15, 2 * (top + LEG / 2), 0.25), u * top + z * (CAP - 0.125)
        box(parts, "timber", size, Matrix.Translation(centre), rng, bevel=0.01, segments=2)


def catwalk(parts, rng):
    joists = 14
    for i in range(joists):
        x = -CATWALK + 0.03 + (2 * CATWALK - 0.06) * i / (joists - 1)
        joist = Matrix.Translation((x, 0, CAP + 0.42))
        box(parts, "timber", (0.06, 2 * CATWALK - 0.12, 0.24), joist, rng, bevel=0.004)
    for sy in (-1, 1):
        rim = Matrix.Translation((0, sy * (CATWALK - 0.03), CAP + 0.42))
        box(parts, "timber", (2 * CATWALK, 0.06, 0.24), rim, rng, bevel=0.004)
    # Decking around the cab, boards running out from the walls.
    width = CATWALK - CAB.w / 2
    for sx in (-1, 1):
        strip = Matrix.Translation((sx * (CATWALK - width / 2), 0, DECK - 0.025))
        box(parts, "deck", (width, 2 * CATWALK, 0.05), strip, rng, bevel=0.004, tile=2.25, grain=0)
        strip = Matrix.Translation((0, sx * (CATWALK - width / 2), DECK - 0.025))
        box(parts, "deck", (CAB.w, width, 0.05), strip, rng, bevel=0.004, tile=2.25, grain=1)
    c = CATWALK - 0.05
    count = 7
    line = [-c + 2 * c * i / count for i in range(count + 1)]
    for sy in (-1, 1):
        guard(parts, rng, (-c + 0.07, sy * c), (c - 0.07, sy * c), [(x, sy * c) for x in line])
    inner = line[1:-1]
    guard(parts, rng, (-c, -c - 0.07), (-c, c + 0.07), [(-c, y) for y in inner])
    head = LANDING_Y[2]
    for y0, y1, gap in ((-c - 0.07, head[0], head[0]), (head[1], c + 0.07, head[1])):
        posts = [(c, y) for y in (*inner, gap) if y0 <= y <= y1]
        guard(parts, rng, (c, y0), (c, y1), posts)


def stairs(parts, rng):
    inner, outer = (BANDS[0] + BANDS[1]) / 2, (BANDS[2] + BANDS[3]) / 2
    first, second, head = LANDING_Y
    one = flight(parts, rng, (outer, first[0] - 12 * GOING, 0), (0, 1, 0), 13)
    # mathutils vectors are single precision.
    assert math.isclose(one.y, first[0], abs_tol=1e-5)
    wide = (BANDS[0], BANDS[3])
    # The first landing hangs on its inner posts and the head landing's outer posts above it.
    landing(parts, rng, wide, first, one.z, ((1, 2), (2, 3), (3, 0)), (0, 3), (0, 3))
    two = flight(parts, rng, (inner, first[0], one.z), (0, -1, 0), 12)
    assert math.isclose(two.y, second[1], abs_tol=1e-5)
    landing(parts, rng, wide, second, two.z, ((3, 0), (0, 1), (1, 2)), range(4), (0, 1, 3))
    # Its corner over the first flight sits on a joist knee-braced to the outer post.
    x = BANDS[3] - 0.07
    joist = Matrix.Translation((x, sum(second) / 2, two.z - 0.17))
    box(parts, "timber", (0.06, second[1] - second[0] - 0.12, 0.24), joist, rng, bevel=0.006)
    start, end = (x, second[0] + 0.14, two.z - 1.0), (x, second[1] - 0.15, two.z - 0.29)
    beam(parts, "timber", start, end, 0.10, 0.14, (0, 0, 1), rng, bevel=0.006)
    three = flight(parts, rng, (outer, second[1], two.z), (0, 1, 0), 12)
    assert math.isclose(three.y, head[0], abs_tol=1e-5)
    assert math.isclose(three.z, DECK, abs_tol=1e-5)
    landing(parts, rng, (CATWALK, BANDS[3]), head, DECK, ((1, 2), (2, 3)), (1, 2, 3), (1, 2))
    pad = Matrix.Translation((outer, first[0] - 12 * GOING - 0.05, -0.025))
    box(parts, "concrete", (1.2, 0.5, 0.15), pad, rng, bevel=0.01, tile=2.71)


def lookout(rng, output):
    mats, bands, derived = cabin_materials(output, TARGETS | LOOKOUT_TARGETS, LOOKOUT_ROOF)
    mats["paint"] = principled("Window frames", base=lin((96, 82, 70)), rough=0.6)
    mats["glass"] = principled("Cab glass", base=(0.5, 0.53, 0.53), rough=0.03, transmission=1.0)
    mats["concrete"], derived["concrete"] = scan(
        "concrete", "concrete_wall_008", LOOKOUT_CONCRETE, output
    )
    mats["galv"] = principled(
        "Galvanised steel", base=lin((150, 152, 150)), rough=0.4, metallic=0.8
    )
    cab = {}
    kit(cab, CAB, rng, bands)
    # The Osborne fire finder stand at the centre of the cab.
    stand = Matrix.Translation((0, CAB.l / 2, DECK + 0.525))
    box(cab, "timber", (0.7, 0.7, 1.05), stand, rng, bevel=0.01)
    render = emit(cab, mats)
    for obj in render:
        obj.data.transform(TURN @ Matrix.Translation((0, -CAB.l / 2, 0)))
    parts = {}
    tower(parts, rng)
    catwalk(parts, rng)
    stairs(parts, rng)
    top = CAB.plate + FASCIA + (CAB.w / 2 + CAB.eave) * math.tan(CAB.pitch) + 0.12
    half = SPREAD[0] + PIER[0] / 2
    x0, x1, y0, y1 = BANDS[0], BANDS[3], LANDING_Y[1][0], LANDING_Y[2][1]
    hulls = {
        "tower": ((2 * half, 2 * half, CAP), (0, 0, CAP / 2)),
        "cab": ((2 * CATWALK + 0.1, 2 * CATWALK + 0.1, top - CAP), (0, 0, (top + CAP) / 2)),
        "stair": (
            (x1 - x0, y1 - y0, DECK + RAIL_M - 0.05),
            ((x0 + x1) / 2, (y0 + y1) / 2, (DECK + RAIL_M + 0.05) / 2),
        ),
    }
    render += emit(parts, mats)
    objects, hulls = collision(render, "SM_FireLookout", hulls, rng, mats["sheathing"])
    return objects, {
        "derivedAlbedo": derived,
        "deckM": DECK,
        "roofTopM": top,
        "riserM": RISE,
        "goingM": GOING,
        "collisionHulls": hulls,
    }


# Trailhead kiosk build frame: the notice board in the y-z plane facing +X between posts at
# y = +-0.83. Sizes follow the approved reference at about 430 px/m: 0.25 m posts and a 36 degree
# shake roof 1.58 m deep and 2.36 m wide under a ridge flashing near 3.0 m.
K_POST, K_POST_Y, K_HALF = 0.25, 0.83, (0.79, 1.18)
K_PITCH, K_RIDGE = math.radians(36), 2.92  # roof deck underside at the ridge line
K_BOARD = (0.94, 2.08)  # board bottom and top
# Post and shake regions of the kiosk master; the shakes rescaled by the first studio render's.
KIOSK_TARGETS = {"timber": ((170, 162, 152), 26), "deck": ((126, 124, 115), 30)}


def shakes(parts, rng, material, eave, up, along, length, width, exposure, thickness, bands, tile):
    """Courses of split shakes `exposure` apart from the eave line centre `eave` to `length` up
    the slope `up`, `width` wide along `along`; each course laps the one below."""
    eave, up, along = Vector(eave), Vector(up), Vector(along)
    normal = along.cross(up)
    assert normal.z > 0 and abs(up.dot(along)) < 1e-9
    for course in range(round(length / exposure)):
        s0 = course * exposure
        size = min(1.4 * exposure, length - s0)
        a = -width / 2
        while a < width / 2 - 0.01:
            w = min(rng.uniform(0.16, 0.34), width / 2 - a)
            centre = eave + up * (s0 + size / 2) + along * (a + w / 2) + normal * thickness
            # Butts stand one shake thickness proud of the course below.
            angle = rng.uniform(-0.01, 0.01) - math.atan(thickness / size)
            matrix = Matrix.Translation(centre) @ Matrix.Rotation(angle, 4, along)
            matrix = matrix @ frame((0, 0, 0), up, normal)
            size3, band = (size, thickness, w - 0.006), rng.choice(bands)
            box(parts, material, size3, matrix, rng, bevel=0.002, tile=tile, band=band)
            a += w


def kiosk(rng, output):
    mats, bands, derived = cabin_materials(output, TARGETS | KIOSK_TARGETS, CABIN_ROOF)
    mats["board"] = principled("Notice board", base=lin((80, 70, 62)), rough=0.85)
    mats["zinc"] = principled("Ridge flashing", base=lin((96, 106, 104)), rough=0.45, metallic=0.7)
    mats["iron"] = principled("Black powder coat", base=lin((34, 34, 33)), rough=0.6)
    parts = {}
    sin, cos, tan = math.sin(K_PITCH), math.cos(K_PITCH), math.tan(K_PITCH)
    ridge = Vector((0, 0, K_RIDGE))
    clip = tuple((ridge, Vector((s * sin, 0, cos))) for s in (-1, 1))

    def under(x):
        return K_RIDGE - abs(x) * tan

    def timber(size, centre, **kwargs):
        box(parts, "timber", size, Matrix.Translation(centre), rng, clip=clip, **kwargs)

    plate = under(0.55)  # outer top edge of the eave plates
    cross = plate - 0.14  # top of the crossbeams through the posts
    for y in (-K_POST_Y, K_POST_Y):
        centre = (0, y, (K_RIDGE + 0.012) / 2)
        timber((K_POST, K_POST, K_RIDGE - 0.012), centre, bevel=0.012, segments=2, grain=2)
        timber((1.24, 0.12, 0.16), (0, y, cross - 0.08), bevel=0.008, segments=2)
        base = Matrix.Translation((0, y, 0.006))
        box(parts, "iron", (0.32, 0.32, 0.012), base, rng, bevel=0.002)
        for s in (-1, 1):
            start, end = Vector((s * K_POST / 2, y, 1.85)), Vector((s * 0.45, y, cross - 0.12))
            beam(parts, "timber", start, end, 0.08, 0.10, (0, 0, 1), rng, bevel=0.006)
            # Flat black straps on both faces of the brace foot, let into the post and bolted
            # through the brace; a base strap on the post face.
            d = (end - start).normalized()
            for sy in (-1, 1):
                side = Vector((0, sy * 0.043, 0))
                p0, p1 = start - d * 0.08 + side, start + d * 0.22 + side
                beam(parts, "iron", p0, p1, 0.006, 0.07, (0, 0, 1), rng, bevel=0.002)
                for along in (0.06, 0.15):
                    bolt(parts, start + d * along + side * 1.07, (0, sy, 0))
            strap = Matrix.Translation((s * (K_POST / 2 + 0.004), y, 0.132))
            box(parts, "iron", (0.008, 0.16, 0.24), strap, rng, bevel=0.002)
            bolt(parts, (s * (K_POST / 2 + 0.008), y, 0.13), (s, 0, 0))
    timber((0.10, 2 * K_HALF[1], 0.14), (0, 0, K_RIDGE - 0.07), bevel=0.006)
    for s in (-1, 1):
        timber((0.10, 2 * K_HALF[1], 0.14), (s * 0.50, 0, plate - 0.07), bevel=0.006)
        for y in (-1.12, -0.45, 0.45, 1.12):
            x0, x1 = s * 0.001, s * (K_HALF[0] - 0.02)
            under_beam(parts, under, K_PITCH, x0, x1, y, 0.06, 0.12, rng, segments=2)
        a, b = Vector((0, 0, K_RIDGE)), Vector((s * K_HALF[0], 0, under(K_HALF[0])))
        down, normal = (b - a).normalized(), Vector((s * sin, 0, cos))
        deck = frame((a + b) / 2 + normal * 0.01, down, (0, 1, 0))
        size = ((b - a).length, 2 * K_HALF[1], 0.02)
        box(parts, "timber", size, deck, rng, bevel=0.002, tile=1.2)
        eave, run = b + normal * 0.02, (b - a).length
        width, along = 2 * K_HALF[1], (0, s, 0)
        shakes(parts, rng, "deck", eave, -down, along, run, width, 0.46, 0.016, bands, PLANK_TILE)
        # The two flashing leaves cross above the ridge line.
        flashing = frame(a + normal * 0.07 + down * 0.05, down, (0, 1, 0))
        box(parts, "zinc", (0.20, width + 0.04, 0.006), flashing, rng, bevel=0.002)
    rail_y = K_POST_Y - K_POST / 2
    for z0, z1 in ((K_BOARD[1], K_BOARD[1] + 0.15), (K_BOARD[0] - 0.14, K_BOARD[0])):
        timber((0.10, 2 * rail_y, z1 - z0), (0, 0, (z0 + z1) / 2), bevel=0.008, segments=2, grain=1)
        for sy in (-1, 1):
            for sx in (-1, 1):
                bolt(parts, (sx * 0.05, sy * (rail_y - 0.06), (z0 + z1) / 2), (sx, 0, 0))
    height, middle = K_BOARD[1] - K_BOARD[0], sum(K_BOARD) / 2
    for sy in (-1, 1):
        timber((0.06, 0.06, height), (0, sy * (rail_y - 0.03), middle), bevel=0.004, grain=2)
    board = Matrix.Translation((0, 0, middle))
    box(parts, "board", (0.03, 2 * (rail_y - 0.06), height), board, rng, bevel=0.003)
    roof = (cross - 0.16, K_RIDGE + 0.06)
    hulls = {
        "posts": ((K_POST + 0.07, 2 * (K_POST_Y + K_POST / 2), cross), (0, 0, cross / 2)),
        "roof": ((2 * K_HALF[0], 2 * K_HALF[1], roof[1] - roof[0]), (0, 0, sum(roof) / 2)),
    }
    objects, hulls = collision(
        emit(parts, mats), "SM_TrailheadKiosk", hulls, rng, mats["sheathing"]
    )
    return objects, {"derivedAlbedo": derived, "collisionHulls": hulls}


# Vault toilet build frame: the CXT Gunnison precast single-vault toilet (drawings G-02 to G-04,
# 2016) without its privacy alcove: 4 in board-formed walls 6'6" x 7'11.5" outside on a slab 5 in
# above grade, a shake-cast roof from 7'4" at the eaves to 9'5" with 1 ft rakes, a 3068 steel door
# in the +X gable wall, and the 12 in vent stack behind the -X wall to 12'3".
VT_X, VT_Y, VT_T = 1.98, 2.42, 0.10
VT_SLAB, VT_EAVE, VT_RIDGE = 0.127, 2.234, 2.87
VT_RAKE, VT_OVERHANG, VT_ROOF_T = 0.3048, 0.08, 0.10
VT_DOOR = (0.914, 2.032, 0.30)  # width, height and centre y
VT_VENT = (0.152, 3.734, 0.55)  # radius, top and centre y
VT_STAIN, WALL_TILE = (138, 120, 100), 1.8  # wall stain sRGB, scan tile width (m)


def saved(name, values, output, colour):
    """An 8-bit PNG image written from float RGBA rows (bottom row first)."""
    img = bpy.data.images.new(name, values.shape[1], values.shape[0])
    img.colorspace_settings.name = "sRGB" if colour else "Non-Color"
    img.pixels.foreach_set(values.astype(np.float32).ravel())
    img.filepath_raw = str(output / f"{name}.png")
    img.file_format = "PNG"
    img.save()
    img.source = "FILE"
    img.reload()
    return img


def blur(a, sigma):
    """Gaussian blur of a 2D array, periodic in both axes, `sigma` in pixels."""
    fy, fx = np.fft.fftfreq(a.shape[0])[:, None], np.fft.rfftfreq(a.shape[1])[None, :]
    kernel = np.exp(-2 * (np.pi * sigma) ** 2 * (fx**2 + fy**2))
    return np.fft.irfft2(np.fft.rfft2(a) * kernel, s=a.shape)


def weathered_walls(output, rng, eave, top):
    """Board-formed concrete maps one scan tile (1.8 m) wide and `top` m tall for `facade` UVs:
    the scan's relief and fine grain under an even stain with subtle mottling, soft drip stains
    below the eave line `eave` and splash darkening above the slab."""
    maps = {}
    for kind in ("diff", "nor_gl", "rough"):
        path = TEXTURES / "wood_textured_concrete" / f"wood_textured_concrete_{kind}_2k.jpg"
        img = image(path, kind == "diff")
        maps[kind] = pixels(img)
        bpy.data.images.remove(img)
    size = maps["diff"].shape[1]
    rows = np.arange(round(size * top / WALL_TILE)) % size
    z = (np.arange(len(rows)) + 0.5) * WALL_TILE / size
    noise = np.random.default_rng(rng.randrange(2**32))
    gray = maps["diff"][rows, :, :3].mean(axis=2)
    grain = np.clip(gray - blur(gray, 24), -0.06, 0.06)
    mottle = blur(noise.standard_normal(gray.shape), 90)
    mottle /= mottle.std()
    x, drips = np.arange(size), np.zeros(gray.shape)
    for _ in range(36):
        centre, width = noise.uniform(0, size), noise.uniform(0.01, 0.05) * size / WALL_TILE
        start, length = eave - noise.uniform(0, 0.06), noise.uniform(0.3, 1.4)
        dx = (x - centre + size / 2) % size - size / 2
        fall = np.where(z < start, np.exp(-(start - z) / length), 0)
        drips += noise.uniform(0.05, 0.16) * np.outer(fall, np.exp(-((dx / width) ** 2)))
    splash = np.clip(1 - (z - VT_SLAB) / 0.35, 0, 1)[:, None] ** 1.5 * (0.18 + 0.04 * mottle)
    shade = 1 + 0.05 * mottle - blur(drips, 3) - splash
    albedo = np.ones((*gray.shape, 4))
    albedo[..., :3] = np.clip(np.array(VT_STAIN)[None, None] / 255 * shade[..., None], 0, 1)
    albedo[..., :3] = np.clip(albedo[..., :3] + 0.8 * grain[..., None], 0, 1)
    material = principled(
        "Board-formed concrete",
        base_image=saved("wall_albedo", albedo, output, True),
        rough_image=saved("wall_rough", maps["rough"][rows], output, False),
        normal_image=saved("wall_normal", maps["nor_gl"][rows], output, False),
    )
    return material, {"source": "wood_textured_concrete 2k", "stainSrgb": list(VT_STAIN)}


def facade(parts, material, top):
    """World-space UVs for `material`: U along each face's wall every WALL_TILE m, V = z / top."""
    verts, faces, uvs = parts[material]
    uvs.clear()
    for face in faces:
        a, b, c = (verts[i] for i in face[:3])
        normal = (b - a).cross(c - a)
        along = 1 if abs(normal.x) >= abs(normal.y) else 0
        uvs.extend((verts[i][along] / WALL_TILE, verts[i].z / top) for i in face)


def toilet(rng, output):
    mats, derived = {}, {}
    for name, asset, target in (
        ("roofing", "wood_textured_concrete", ((96, 84, 72), 12)),
        ("slab", "concrete_wall_008", ((156, 150, 140), 14)),
    ):
        mats[name], derived[name] = scan(name, asset, target, output)
    mats["door"] = principled("Door paint", base=lin((84, 64, 50)), rough=0.45, metallic=0.3)
    mats["iron"] = principled("Black steel", base=lin((40, 40, 40)), rough=0.45, metallic=1.0)
    mats["pipe"] = principled("Vent stack", base=lin((26, 26, 26)), rough=0.6)
    mats["sign"] = principled("Sign", base=lin((32, 72, 140)), rough=0.4)
    mats["label"] = principled("Sign symbol", base=lin((228, 228, 222)), rough=0.4)
    mats["steel"] = principled("Hatch steel", base=lin((112, 112, 108)), rough=0.5, metallic=0.8)
    parts = {}
    hx, hy = VT_X / 2, VT_Y / 2
    pitch = math.atan((VT_RIDGE - VT_EAVE) / (hy + VT_OVERHANG))
    under = VT_RIDGE - VT_ROOF_T / math.cos(pitch)
    planes = (
        (Vector((0, 0, under)), Vector((0, s * math.sin(pitch), math.cos(pitch)))) for s in (-1, 1)
    )
    clip = tuple(planes)

    def piece(material, size, centre, **kwargs):
        box(parts, material, size, Matrix.Translation(centre), rng, **kwargs)

    def wall(size, centre):
        piece("wall", size, centre, bevel=0.006, tile=1.8, grain=2, clip=clip)

    door = (VT_DOOR[2] - VT_DOOR[0] / 2 - 0.05, VT_DOOR[2] + VT_DOOR[0] / 2 + 0.05)
    head, height, middle = VT_SLAB + VT_DOOR[1] + 0.05, under - VT_SLAB, (VT_SLAB + under) / 2
    for sx, spans in ((1, ((-hy, door[0]), (door[1], hy))), (-1, ((-hy, hy),))):
        for y0, y1 in spans:
            wall((VT_T, y1 - y0, height), (sx * (hx - VT_T / 2), (y0 + y1) / 2, middle))
    wall((VT_T, door[1] - door[0], under - head), (hx - VT_T / 2, VT_DOOR[2], (head + under) / 2))
    for sy in (-1, 1):
        wall((VT_X - 2 * VT_T, VT_T, height), (0, sy * (hy - VT_T / 2), middle))
    piece(
        "slab",
        (VT_X + 0.1, VT_Y + 0.1, VT_SLAB + 0.1),
        (0, 0, (VT_SLAB - 0.1) / 2),
        bevel=0.01,
        tile=2.71,
    )
    # Steel door in a 2 in frame: louvre at the foot, lever, and the sign beside it.
    for y in (door[0] + 0.025, door[1] - 0.025):
        piece(
            "door", (0.12, 0.05, head - VT_SLAB), (hx - 0.05, y, (VT_SLAB + head) / 2), bevel=0.004
        )
    piece(
        "door", (0.12, door[1] - door[0], 0.05), (hx - 0.05, VT_DOOR[2], head - 0.025), bevel=0.004
    )
    piece(
        "door",
        (0.045, VT_DOOR[0], VT_DOOR[1]),
        (hx - 0.06, VT_DOOR[2], VT_SLAB + VT_DOOR[1] / 2),
        bevel=0.004,
    )
    piece("iron", (0.004, 0.46, 0.30), (hx - 0.036, VT_DOOR[2], VT_SLAB + 0.30), bevel=0)
    for i in range(6):
        piece(
            "door",
            (0.02, 0.44, 0.012),
            (hx - 0.03, VT_DOOR[2], VT_SLAB + 0.18 + 0.048 * i),
            bevel=0.002,
        )
    lever = (hx - 0.01, VT_DOOR[2] + VT_DOOR[0] / 2 - 0.12, VT_SLAB + 0.97)
    piece("iron", (0.05, 0.14, 0.022), lever, bevel=0.006, segments=2)
    piece("sign", (0.012, 0.23, 0.30), (hx + 0.006, door[1] + 0.30, VT_SLAB + 1.52), bevel=0.003)
    piece("label", (0.004, 0.15, 0.15), (hx + 0.013, door[1] + 0.30, VT_SLAB + 1.56), bevel=0)
    # Wall vent grille low on the front wall.
    piece("iron", (0.012, 0.32, 0.32), (hx + 0.006, -hy + 0.40, VT_SLAB + 0.36), bevel=0.003)
    for i in range(5):
        piece(
            "iron",
            (0.03, 0.30, 0.012),
            (hx + 0.012, -hy + 0.40, VT_SLAB + 0.24 + 0.06 * i),
            bevel=0.002,
        )
    for sy in (-1, 1):
        a = Vector((0, 0, VT_RIDGE - VT_ROOF_T / 2 / math.cos(pitch)))
        b = a + Vector((0, sy * (hy + VT_OVERHANG), -(hy + VT_OVERHANG) * math.tan(pitch)))
        down, along = (b - a).normalized(), Vector((-sy, 0, 0))
        normal = down.cross(along)
        size = ((b - a).length, VT_X + 2 * VT_RAKE, VT_ROOF_T)
        box(parts, "roofing", size, frame((a + b) / 2, down, along), rng, bevel=0.01, tile=1.8)
        eave, run, width = b + normal * (VT_ROOF_T / 2), (b - a).length, VT_X + 2 * VT_RAKE
        shakes(parts, rng, "roofing", eave, -down, along, run, width, 0.25, 0.02, (None,), 1.8)
    cap = (VT_X + 2 * VT_RAKE + 0.02, 0.24, 0.07)
    piece("roofing", cap, (0, 0, VT_RIDGE + 0.04), bevel=0.02, segments=2, tile=1.8)
    # Vent stack against the rear wall through the rake, strapped twice; vault hatch behind.
    vent = (-(hx + VT_VENT[0] + 0.005), VT_VENT[2])
    cylinder(parts, "pipe", (*vent, -0.1), VT_VENT[0], VT_VENT[1] + 0.05)
    cylinder(parts, "pipe", (*vent, VT_VENT[1] - 0.05), VT_VENT[0] + 0.012, 0.05)
    for zb in (1.1, 2.0):
        cylinder(parts, "iron", (*vent, zb), VT_VENT[0] + 0.006, 0.04)
        piece("iron", (0.10, 0.04, 0.04), (-hx - 0.05, vent[1], zb + 0.02), bevel=0.004)
    pad = (-(hx + 0.45), -0.20)
    piece("slab", (0.80, 1.10, 0.15), (*pad, -0.025), bevel=0.01, tile=2.71)
    piece("steel", (0.62, 0.62, 0.012), (*pad, 0.056), bevel=0.003)
    piece("steel", (0.04, 0.16, 0.025), (pad[0] + 0.24, pad[1], 0.074), bevel=0.005)
    roof = (VT_EAVE - VT_ROOF_T - 0.05, VT_RIDGE + 0.08)
    hulls = {
        "building": ((VT_X + 0.1, VT_Y + 0.1, roof[0]), (0, 0, roof[0] / 2)),
        "roof": (
            (VT_X + 2 * VT_RAKE, VT_Y + 2 * VT_OVERHANG, roof[1] - roof[0]),
            (0, 0, sum(roof) / 2),
        ),
        "vent": (
            (2 * VT_VENT[0], 2 * VT_VENT[0], VT_VENT[1] - roof[1]),
            (*vent, (VT_VENT[1] + roof[1]) / 2),
        ),
    }
    wall_top = under - hy * math.tan(pitch)
    mats["wall"], derived["wall"] = weathered_walls(output, rng, wall_top, under)
    facade(parts, "wall", under)
    objects, hulls = collision(emit(parts, mats), "SM_VaultToilet", hulls, rng, mats["slab"])
    return objects, {
        "derivedAlbedo": derived,
        "roofPitchDegrees": math.degrees(pitch),
        "collisionHulls": hulls,
    }


if __name__ == "__main__":
    main(
        Path(__file__).resolve(),
        {
            "ranger-cabin": cabin,
            "timber-footbridge": footbridge,
            "fire-lookout": lookout,
            "trailhead-kiosk": kiosk,
            "snow-survey-hut": hut,
            "vault-toilet": toilet,
        },
    )

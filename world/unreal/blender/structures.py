"""Build valley structures from real-world dimensions and CC0 materials.

blender -b --factory-startup --python world/unreal/blender/structures.py -- <asset> --output <dir>
Writes <asset>.blend, <asset>.glb, derived albedo PNGs and build.json into a new .local directory.
The footbridge spans along y with its downstream elevation facing +X; UCX_SM_TimberFootbridge_##
are the convex hulls that Unreal Interchange imports as collision for SM_TimberFootbridge.
"""

import math
import sys
from pathlib import Path

import bmesh
import bpy
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

# Cabin build frame: -Y is the porch side, y = 0 the front wall, x = 0 the ridge line. Body
# outside the siding and the opening layout come from the camera/proportion fit of the approved
# reference; heights follow standard framing and stair practice.
W, L = 5.72, 6.90
PLINTH, FLOOR, PLATE = 0.50, 0.65, 3.20
SEAT = 0.12
PITCH, PORCH_PITCH = math.radians(40), math.radians(30)
EAVE, RAKE = 0.40, 0.30
POST, POST_Y, DECK_Y = 0.30, -1.40, -1.58
HEADER = (2.87, 3.12)
PORCH_POSTS = (-2.735, 2.02, 2.735)
NEWEL_X = -0.50
STAIR = (-0.42, 1.90)
RISER, TREAD = FLOOR / 4, 0.28
EXPOSURE, BOARD_T = 0.19, 0.022
SEAM_SPACING = 0.40
OPENINGS = {
    # wall: [(kind, centre along the wall, rough width, sill height, rough height)]
    "front": [("door", 0.26, 0.97, FLOOR, 2.16), ("window", -1.47, 0.96, 1.70, 0.98)],
    "right": [("window", 2.07, 0.74, 1.60, 1.15), ("window", 4.72, 0.74, 1.60, 1.15)],
    "rear": [("window", 0.0, 0.74, 1.60, 1.15)],
    "left": [("window", L - 2.07, 0.74, 1.60, 1.15), ("window", L - 4.72, 0.74, 1.60, 1.15)],
}
# Reference-region sRGB mean and luminance standard deviation on the approved master.
TARGETS = {
    "timber": ((172, 157, 143), 24),
    "siding": ((123, 116, 110), 22),
    "stone": ((168, 154, 142), 37),
    "deck": ((138, 127, 119), 32),
}
PLANK_TILE = 3.06  # metres per texture width so a 0.14 m scanned plank spans a 0.19 m board


def roof_under(x):
    return PLATE + SEAT + (W / 2 - abs(x)) * math.tan(PITCH)


def porch_under(x):
    return HEADER[1] + 0.03 + (W / 2 - abs(x)) * math.tan(PORCH_PITCH)


def under_beam(parts, under, pitch, x0, x1, y, width, depth, rng, **kwargs):
    """Timber in the x-z plane at `y` whose top face lies on the underside `under`."""
    sx = 1 if x1 + x0 >= 0 else -1
    normal = Vector((sx * math.sin(pitch), 0, math.cos(pitch)))
    a = Vector((x0, y, under(x0))) - normal * depth / 2
    b = Vector((x1, y, under(x1))) - normal * depth / 2
    beam(parts, "timber", a, b, width, depth, normal, rng, **kwargs)


def walls():
    """Outer siding planes: origin at a = 0, unit along-wall axis, outward normal, extent."""
    return {
        "front": (Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((0, -1, 0)), (-W / 2, W / 2)),
        "right": (Vector((W / 2, 0, 0)), Vector((0, 1, 0)), Vector((1, 0, 0)), (0, L)),
        "rear": (Vector((0, L, 0)), Vector((-1, 0, 0)), Vector((0, 1, 0)), (-W / 2, W / 2)),
        "left": (Vector((-W / 2, L, 0)), Vector((0, -1, 0)), Vector((-1, 0, 0)), (0, L)),
    }


def siding(parts, rng, bands):
    for name, (origin, along, out, (a0, a1)) in walls().items():
        lo, hi = a0 + 0.13, a1 - 0.13
        holes = [(c - w / 2, c + w / 2, z, z + h) for _, c, w, z, h in OPENINGS[name]]
        z0 = FLOOR if name == "front" else PLINTH + 0.18
        gable = name in ("front", "rear")
        top = roof_under(0) if gable else PLATE - 0.14
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
                        apex = Vector((0, 0, roof_under(0) - 0.01))
                        clip = tuple(
                            (apex, Vector((s * math.sin(PITCH), 0, math.cos(PITCH))))
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
        sheathing(parts, name, origin, along, out, a0, a1, holes, rng)


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


def sheathing(parts, name, origin, along, out, a0, a1, holes, rng):
    depth = BOARD_T + 0.006
    panel(parts, "sheathing", origin, along, out, a0, a1, PLINTH, PLATE, holes, depth, rng)
    panel(
        parts, "interior", origin, along, -out, a0 + 0.15, a1 - 0.15, FLOOR, 3.0, holes, -0.145, rng
    )
    if name in ("front", "rear"):
        bm = bmesh.new()
        uv = bm.loops.layers.uv.new("UVMap")
        corners = [(-W / 2, PLATE), (W / 2, PLATE), (0, roof_under(0))]
        verts = [bm.verts.new(Vector((x, origin.y, z)) - out * depth) for x, z in corners]
        face = bm.faces.new(verts if name == "front" else verts[::-1])
        for loop in face.loops:
            loop[uv].uv = (loop.vert.co.x, loop.vert.co.z)
        add(parts, "sheathing", bm)


def openings(parts, rng):
    for name, (origin, along, out, _) in walls().items():
        for kind, c, w, z, h in OPENINGS[name]:

            def at(a, zz, d):
                return Vector((*(origin + along * a + out * d).xy, zz))

            def trim(material, size, a, zz, d, **kwargs):
                box(parts, material, size, frame(at(a, zz, d), along, out), rng, **kwargs)

            # Jambs line the 0.145 m wall reveal; timber casing sits proud of the siding.
            for a in (c - w / 2 + 0.015, c + w / 2 - 0.015):
                trim("paint", (0.03, 0.15, h), a, z + h / 2, -0.07, bevel=0.002)
            trim("paint", (w, 0.15, 0.03), c, z + h - 0.015, -0.07, bevel=0.002)
            casing = 0.11
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


def stone_plinth(parts, height, rng):
    """One continuous ring, displaced outward from the scanned stone height map."""
    x0, x1, y0, y1 = -W / 2 - 0.06, W / 2 + 0.06, DECK_Y + 0.06, L + 0.06
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edges = [(Vector((*corners[i], 0)), Vector((*corners[(i + 1) % 4], 0))) for i in range(4)]
    perimeter = sum((b - a).length for a, b in edges)
    step, rows, tile, relief = 0.012, int(round(PLINTH / 0.0125)), 3.2, 0.035
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
            z = PLINTH * j / rows
            v = z / tile
            sample = height[int((1 - v % 1) * (hh - 1)), int((u % 1) * (hw - 1))]
            taper = min(1.0, (PLINTH - z) / 0.04)
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
    outer = [Vector((x, y, PLINTH)) for x, y in corners]
    inner = [
        Vector((x - math.copysign(inset, x), y - math.copysign(inset, y - (y0 + y1) / 2), PLINTH))
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


def frame_timbers(parts, rng):
    def timber(size, centre, **kwargs):
        box(parts, "timber", size, Matrix.Translation(centre), rng, **kwargs)

    # Corner posts, sill beam and frieze of the body.
    for sx in (-1, 1):
        for y in (0.055, L - 0.055):
            centre = (sx * (W / 2 - 0.055), y, (PLINTH + PLATE) / 2)
            timber((0.16, 0.16, PLATE - PLINTH), centre, segments=2, grain=2)
        timber((0.07, L + 0.06, 0.18), (sx * (W / 2 + 0.03), L / 2, PLINTH + 0.09), grain=1)
        timber((0.05, L - 0.2, 0.14), (sx * (W / 2 + 0.025), L / 2, PLATE - 0.07), grain=1)
    timber((W + 0.06, 0.07, 0.18), (0, L + 0.03, PLINTH + 0.09), grain=0)
    # Main roof: rafter tails, fascia and barge rafters.
    for sx in (-1, 1):
        y = 0.25
        while y < L - 0.15:
            x0, x1 = sx * (W / 2 - 0.02), sx * (W / 2 + EAVE - 0.05)
            under_beam(parts, roof_under, PITCH, x0, x1, y, 0.05, 0.14, rng)
            y += 0.61
        xf = sx * (W / 2 + EAVE - 0.03)
        timber((0.04, L + 2 * RAKE, 0.2), (xf, L / 2, roof_under(xf) - 0.09), grain=1)
        for yb in (-RAKE + 0.04, L + RAKE - 0.04):
            x0, x1 = sx * 0.001, sx * (W / 2 + EAVE)
            under_beam(parts, roof_under, PITCH, x0, x1, yb, 0.08, 0.24, rng, segments=2)
        # Purlins carrying the rake overhang.
        for frac in (0.35, 0.75):
            x = sx * (W / 2) * frac
            for y0, y1 in ((-RAKE + 0.08, 0.2), (L - 0.2, L + RAKE - 0.08)):
                timber((0.1, y1 - y0, 0.14), (x, (y0 + y1) / 2, roof_under(x) - 0.115), grain=1)
    # Front gable king post and collar visible above the porch roof.
    zc = roof_under(0) - 0.32
    xc = W / 2 - (zc - PLATE - SEAT) / math.tan(PITCH)
    timber((2 * xc, 0.12, 0.14), (0, -0.12, zc), segments=2)
    size = (0.16, 0.14, roof_under(0) - zc - 0.05)
    timber(size, (0, -0.12, (zc + roof_under(0)) / 2 - 0.02), segments=2, grain=2)


def roof(parts, rng):
    def slope(under, pitch, x_end, y0, y1):
        for sx in (-1, 1):
            a = Vector((0, 0, under(0)))
            b = Vector((sx * x_end, 0, under(x_end)))
            down = (b - a).normalized()
            normal = Vector((sx * math.sin(pitch), 0, math.cos(pitch)))
            length = (b - a).length
            mid = (a + b) / 2

            def sheet(material, size, centre, y, **kwargs):
                matrix = frame(Vector((centre.x, y, centre.z)), down, (0, 1, 0))
                box(parts, material, size, matrix, rng, **kwargs)

            deck = mid + normal * 0.0125
            sheet("timber", (length, y1 - y0, 0.025), deck, (y0 + y1) / 2, bevel=0.002, tile=1.2)
            panels = int(round((y1 - y0) / SEAM_SPACING))
            for k in range(panels):
                py0 = y0 + (y1 - y0) * k / panels
                py1 = y0 + (y1 - y0) * (k + 1) / panels
                centre = mid + normal * 0.031 + down * 0.02
                size = (length + 0.04, py1 - py0, 0.012)
                sheet(f"roof{rng.randrange(3)}", size, centre, (py0 + py1) / 2, bevel=0.002)
                if k:
                    seam = mid + normal * 0.05 + down * 0.02
                    size = (length + 0.04, 0.022, 0.03)
                    sheet("roof0", size, seam, py0, bevel=0.006, segments=2)
            cap = a + normal * 0.075 + down * 0.08
            sheet("roof0", (0.17, y1 - y0 + 0.02, 0.012), cap, (y0 + y1) / 2, bevel=0.003)

    slope(roof_under, PITCH, W / 2 + EAVE, -RAKE, L + RAKE)
    slope(porch_under, PORCH_PITCH, W / 2 + 0.30, POST_Y - POST / 2 - 0.30, -0.005)


def porch(parts, rng):
    def timber(size, centre, material="timber", **kwargs):
        box(parts, material, size, Matrix.Translation(centre), rng, **kwargs)

    # Deck boards run across the porch; rim boards close the edge above the stone.
    deck = (W + 0.08, -DECK_Y + 0.02, 0.04)
    timber(deck, (0, DECK_Y / 2, FLOOR - 0.02), "deck", bevel=0.002, tile=2.25, grain=1)
    timber((W + 0.08, 0.035, 0.15), (0, DECK_Y + 0.0175, FLOOR - 0.115), grain=0)
    for sx in (-1, 1):
        timber((0.035, -DECK_Y, 0.15), (sx * (W / 2 + 0.055), DECK_Y / 2, FLOOR - 0.115), grain=1)
    for x in PORCH_POSTS:
        size, centre = (POST, POST, HEADER[0] - FLOOR), (x, POST_Y, (FLOOR + HEADER[0]) / 2)
        timber(size, centre, bevel=0.012, segments=2, grain=2)
    hz, hh = (HEADER[0] + HEADER[1]) / 2, HEADER[1] - HEADER[0]
    timber((W + 0.26, 0.22, hh), (0, POST_Y, hz), bevel=0.01, segments=2)
    for sx in (-1, 1):
        centre = (sx * PORCH_POSTS[2], POST_Y / 2 + 0.05, hz)
        timber((0.22, -POST_Y - 0.1, hh), centre, bevel=0.01, segments=2, grain=1)
    # King-post truss on the header and exposed rafters under the porch roof.
    apex = porch_under(0)
    size, centre = (0.2, 0.2, apex - HEADER[1] - 0.02), (0, POST_Y, (apex + HEADER[1]) / 2 - 0.01)
    timber(size, centre, bevel=0.01, segments=2, grain=2)
    for sx in (-1, 1):
        for y, width, depth in ((POST_Y, 0.2, 0.2), (-0.72, 0.07, 0.17), (-0.12, 0.07, 0.17)):
            x0, x1 = sx * 0.001, sx * (W / 2 + 0.28)
            under_beam(parts, porch_under, PORCH_PITCH, x0, x1, y, width, depth, rng, segments=2)
        x0, x1, yb = sx * 0.001, sx * (W / 2 + 0.30), POST_Y - POST / 2 - 0.30 + 0.04
        under_beam(parts, porch_under, PORCH_PITCH, x0, x1, yb, 0.08, 0.22, rng, segments=2)

    # Railings: 0.91 m guard height, balusters at under 0.1 m clear spacing.
    def railing(p0, p1):
        p0, p1 = Vector((*p0, 0)), Vector((*p1, 0))
        length = (p1 - p0).length
        d = (p1 - p0).normalized()
        side = Vector((-d.y, d.x, 0))
        for z, t, h in ((FLOOR + 0.885, 0.09, 0.05), (FLOOR + 0.10, 0.07, 0.045)):
            c = (p0 + p1) / 2
            matrix = frame(Vector((c.x, c.y, z)), d, side)
            box(parts, "timber", (length, t, h), matrix, rng, bevel=0.006, segments=2)
        count = int(length / 0.135)
        for i in range(count):
            p = p0 + d * (length * (i + 0.5) / count)
            timber((0.04, 0.04, 0.74), (p.x, p.y, FLOOR + 0.495), bevel=0.004, grain=2)

    inner = POST / 2
    railing((PORCH_POSTS[0] + inner, POST_Y), (NEWEL_X - 0.075, POST_Y))
    railing((PORCH_POSTS[1] + inner, POST_Y), (PORCH_POSTS[2] - inner, POST_Y))
    for x in (PORCH_POSTS[0], PORCH_POSTS[2]):
        railing((x, POST_Y + inner), (x, -0.03))
    timber((0.15, 0.15, 1.0), (NEWEL_X, POST_Y, FLOOR + 0.5), bevel=0.01, segments=2, grain=2)
    timber((0.19, 0.19, 0.04), (NEWEL_X, POST_Y, FLOOR + 1.02), bevel=0.008, segments=2)
    # Stairs: three treads and four risers between grade and the deck.
    x0, x1 = STAIR
    width = x1 - x0
    for k in range(1, 4):
        top = FLOOR - k * RISER
        ya, yb = DECK_Y - TREAD * k - 0.025, DECK_Y - TREAD * (k - 1)
        size, centre = (width, yb - ya, 0.04), ((x0 + x1) / 2, (ya + yb) / 2, top - 0.02)
        timber(size, centre, "deck", bevel=0.004, segments=2, tile=2.25, grain=1)
        for x in (x0 + 0.02, x1 - 0.02):
            size, centre = (
                (0.04, yb - ya - 0.03, top - 0.04),
                (x, (ya + yb) / 2 + 0.015, top / 2 - 0.02),
            )
            timber(size, centre, grain=1)
    for k in range(2, 5):
        top = FLOOR - (k - 1) * RISER - 0.04
        bottom = FLOOR - k * RISER if k < 4 else 0
        y = DECK_Y - TREAD * (k - 1)
        timber((width - 0.08, 0.025, top - bottom), ((x0 + x1) / 2, y + 0.0125, (top + bottom) / 2))


def cabin_materials(output, targets):
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
    for i, (colour, rough) in enumerate(
        (((84, 92, 85), 0.42), ((80, 89, 82), 0.46), ((88, 95, 88), 0.39))
    ):
        mats[f"roof{i}"] = principled(
            f"Standing-seam roof {i}", base=lin(colour), rough=rough, metallic=0.25
        )
    return mats, plank_bands(albedo["siding"]), derived


def cabin(rng, output):
    mats, bands, derived = cabin_materials(output, TARGETS)
    disp = image(TEXTURES / "stone_wall/stone_wall_disp_4k.png")
    # Flip so row 0 is the image top, as the plinth's UV lookup expects.
    height = pixels(disp)[::-1, :, 0].copy()
    bpy.data.images.remove(disp)
    parts = {}
    stone_plinth(parts, height, rng)
    siding(parts, rng, bands)
    openings(parts, rng)
    frame_timbers(parts, rng)
    porch(parts, rng)
    roof(parts, rng)
    for z in (FLOOR - 0.01, 3.0):
        box(
            parts,
            "interior",
            (W - 0.3, L - 0.3, 0.02),
            Matrix.Translation((0, L / 2, z)),
            rng,
            bevel=0,
        )
    objects = emit(parts, mats)
    for obj in objects:
        obj.data.transform(Matrix.Rotation(math.pi / 2, 4, "Z"))
    return objects, {"derivedAlbedo": derived, "sidingPlanks": len(bands)}


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


def bolt(parts, centre, normal):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=6, radius=BOLT[0])
    bmesh.ops.scale(bm, vec=(1, 1, BOLT[1]), verts=bm.verts)
    bm.loops.layers.uv.new("UVMap")
    rotation = Vector(normal).to_track_quat("Z", "Y").to_matrix().to_4x4()
    bm.transform(Matrix.Translation(centre) @ rotation)
    add(parts, "iron", bm)


def footbridge(rng, output):
    buried = load("structures")["timber-footbridge"]["buriedM"]
    assert math.isclose(buried, SILL[1] - SILL[2]), buried
    clear = 2 * SILL_Y - SILL[0]
    assert clear > 2 * BANK_TOP_M and SILL_Y + SILL[0] / 2 < SPAN / 2
    mats, bands, derived = cabin_materials(output, TARGETS | BRIDGE_TARGETS)
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
    render = emit(parts, mats)
    with bpy.context.temp_override(active_object=render[0], selected_editable_objects=render):
        assert bpy.ops.object.join() == {"FINISHED"}
    render = render[0]
    render.name = render.data.name = "SM_TimberFootbridge"
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
    collision = {}
    for i, (size, centre) in enumerate(hulls.values()):
        name = f"UCX_SM_TimberFootbridge_{i:02d}"
        box(collision, name, size, Matrix.Translation(centre), rng, bevel=0)
    objects = emit(collision, dict.fromkeys(collision, mats["sheathing"]))
    for obj in objects:
        assert len(obj.data.vertices) == 8, obj.name
        obj.hide_render = True
    return [render, *objects], {
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
        "collisionHulls": dict(zip((o.name for o in objects), hulls)),
    }


if __name__ == "__main__":
    main(Path(__file__).resolve(), {"ranger-cabin": cabin, "timber-footbridge": footbridge})

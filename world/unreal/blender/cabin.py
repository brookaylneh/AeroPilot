"""Build the small Sierra ranger cabin in Blender from real-world dimensions and CC0 materials.

Run headless: blender -b --factory-startup --python world/unreal/cabin.py -- --output <new dir>
Writes cabin.blend, ranger-cabin.glb (4K textures, for Unreal), ranger-cabin-web.glb (2K, Draco)
and build.json. Metres, +Z up, -Y is the porch side; the origin is the footprint centre at grade.
"""

import argparse
import hashlib
import json
import math
import random
import sys
import time
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
TEXTURES = ROOT / ".local/vendor/polyhaven"

# Body outside the siding and opening layout come from the camera/proportion fit of the approved
# reference (24 Small Sierra ranger cabin); heights follow standard framing and stair practice.
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
RISER, TREAD = (FLOOR) / 4, 0.28
EXPOSURE, BOARD_T = 0.19, 0.022
SEAM_SPACING = 0.40
OPENINGS = {
    # wall: [(kind, centre along the wall, rough width, sill height, rough height)]
    "front": [("door", 0.26, 0.97, FLOOR, 2.16), ("window", -1.47, 0.96, 1.70, 0.98)],
    "right": [("window", 2.07, 0.74, 1.60, 1.15), ("window", 4.72, 0.74, 1.60, 1.15)],
    "rear": [("window", 0.0, 0.74, 1.60, 1.15)],
    "left": [("window", L - 2.07, 0.74, 1.60, 1.15), ("window", L - 4.72, 0.74, 1.60, 1.15)],
}
# Reference-region sRGB mean and standard deviation on the approved master (cabin-reference.json).
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


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Parts:
    """Geometry accumulated per material and emitted as one mesh object each."""

    def __init__(self):
        self.data = {}

    def add(self, material, bm):
        verts, faces, uvs = self.data.setdefault(material, ([], [], []))
        uv = bm.loops.layers.uv.active
        base = len(verts)
        verts.extend(v.co.copy() for v in bm.verts)
        bm.verts.index_update()
        for face in bm.faces:
            faces.append([base + loop.vert.index for loop in face.loops])
            uvs.extend(tuple(loop[uv].uv) for loop in face.loops)
        bm.free()

    def build(self, materials):
        objects = []
        for name, (verts, faces, uvs) in self.data.items():
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata([tuple(v) for v in verts], [], faces)
            layer = mesh.uv_layers.new(name="UVMap")
            layer.data.foreach_set("uv", np.array(uvs, dtype=np.float32).ravel())
            mesh.validate()
            mesh.set_sharp_from_angle(angle=math.radians(35))
            mesh.materials.append(materials[name])
            obj = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            objects.append(obj)
        return objects


def frame(origin, x_axis, y_axis):
    x = Vector(x_axis).normalized()
    y = Vector(y_axis).normalized()
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
    across the local z height and image U runs along local x.
    """
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
                loop[uv].uv = (co[0] / PLANK_TILE + offset[0], v)
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
        if edges:
            bmesh.ops.holes_fill(bm, edges=edges)
    if bm.faces:
        parts.add(material, bm)
    else:
        bm.free()


def beam(parts, material, start, end, width, depth, up, rng, **kwargs):
    """Timber from `start` to `end`; `depth` is measured along `up`, grain along the length."""
    start, end, up = Vector(start), Vector(end), Vector(up)
    axis = end - start
    side = up.cross(axis).normalized()
    up = axis.normalized().cross(side)
    matrix = frame((start + end) / 2, axis, side)
    if up.dot(matrix.col[2].xyz) < 0:
        matrix = frame((start + end) / 2, axis, -side)
    box(parts, material, (axis.length, width, depth), matrix, rng, grain=0, **kwargs)


def under_beam(parts, under, pitch, x0, x1, y, width, depth, rng, **kwargs):
    """Timber in the x-z plane at `y` whose top face lies on the underside `under`."""
    sx = 1 if x1 + x0 >= 0 else -1
    normal = Vector((sx * math.sin(pitch), 0, math.cos(pitch)))
    a = Vector((x0, y, under(x0))) - normal * depth / 2
    b = Vector((x1, y, under(x1))) - normal * depth / 2
    beam(parts, "timber", a, b, width, depth, normal, rng, **kwargs)


def walls():
    """Outer siding planes: origin at a = 0, unit along-wall axis, outward normal."""
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
                        n_left = Vector((-math.sin(PITCH), 0, math.cos(PITCH)))
                        n_right = Vector((math.sin(PITCH), 0, math.cos(PITCH)))
                        apex = Vector((0, 0, roof_under(0) - 0.01))
                        clip = ((apex, n_left), (apex, n_right))
                    box(
                        parts,
                        "siding",
                        (s1 - s0, BOARD_T, height),
                        matrix,
                        rng,
                        bevel=0.003,
                        clip=clip,
                        band=rng.choice(bands),
                    )
        sheathing(parts, name, origin, along, out, holes, rng)


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


def sheathing(parts, name, origin, along, out, holes, rng):
    a0, a1 = walls()[name][3]
    panel(
        parts, "sheathing", origin, along, out, a0, a1, PLINTH, PLATE, holes, BOARD_T + 0.006, rng
    )
    panel(
        parts, "interior", origin, along, -out, a0 + 0.15, a1 - 0.15, FLOOR, 3.0, holes, -0.145, rng
    )
    if name in ("front", "rear"):
        bm = bmesh.new()
        uv = bm.loops.layers.uv.new("UVMap")
        d = -out * (BOARD_T + 0.006)
        corners = [
            Vector((-W / 2, 0, PLATE)),
            Vector((W / 2, 0, PLATE)),
            Vector((0, 0, roof_under(0))),
        ]
        verts = [bm.verts.new(Vector((c.x, origin.y, c.z)) + d) for c in corners]
        face = bm.faces.new(verts if name == "front" else verts[::-1])
        for loop in face.loops:
            loop[uv].uv = (loop.vert.co.x, loop.vert.co.z)
        parts.add("sheathing", bm)


def openings(parts, rng):
    for name, (origin, along, out, _) in walls().items():
        for kind, c, w, z, h in OPENINGS[name]:

            def at(a, zz, d):
                return Vector((*(origin + along * a + out * d).xy, zz))

            # Jambs line the 0.145 m wall reveal; timber casing sits proud of the siding.
            paint = "paint"
            for a in (c - w / 2 + 0.015, c + w / 2 - 0.015):
                box(
                    parts,
                    paint,
                    (0.03, 0.15, h),
                    frame(at(a, z + h / 2, -0.07), along, out),
                    rng,
                    bevel=0.002,
                )
            box(
                parts,
                paint,
                (w, 0.15, 0.03),
                frame(at(c, z + h - 0.015, -0.07), along, out),
                rng,
                bevel=0.002,
            )
            casing = 0.11
            for a in (c - w / 2 - casing / 2, c + w / 2 + casing / 2):
                bottom = z if kind == "door" else z - 0.05
                box(
                    parts,
                    "timber",
                    (casing, 0.03, h + 0.05 + (z - bottom)),
                    frame(at(a, (bottom + z + h + 0.05) / 2, BOARD_T + 0.015), along, out),
                    rng,
                    segments=2,
                )
            box(
                parts,
                "timber",
                (w + 2 * casing + 0.06, 0.035, 0.15),
                frame(at(c, z + h + 0.075, BOARD_T + 0.0175), along, out),
                rng,
                segments=2,
            )
            box(
                parts,
                "timber",
                (w + 2 * casing + 0.10, 0.07, 0.03),
                frame(at(c, z + h + 0.165, BOARD_T + 0.035), along, out),
                rng,
                segments=2,
            )
            if kind == "door":
                door(parts, at, along, out, c, w - 0.06, z, h - 0.03, rng)
                continue
            box(
                parts,
                "timber",
                (w + 2 * casing + 0.08, 0.10, 0.045),
                frame(at(c, z - 0.0225, 0.02), along, out),
                rng,
                segments=2,
            )
            box(
                parts,
                "timber",
                (w + 2 * casing, 0.025, 0.08),
                frame(at(c, z - 0.085, BOARD_T + 0.0125), along, out),
                rng,
            )
            window(parts, at, along, out, c, w - 0.06, z, h - 0.03, rng)


def sash(parts, at, along, out, c, w, z0, h, d, cols, rows, rng):
    stile, rail, t = 0.05, 0.05, 0.04
    for a in (c - w / 2 + stile / 2, c + w / 2 - stile / 2):
        box(
            parts, "paint", (stile, t, h), frame(at(a, z0 + h / 2, d), along, out), rng, bevel=0.003
        )
    for zz in (z0 + rail / 2, z0 + h - rail / 2):
        box(parts, "paint", (w, t, rail), frame(at(c, zz, d), along, out), rng, bevel=0.003)
    inner_w, inner_h = w - 2 * stile, h - 2 * rail
    for i in range(1, cols):
        a = c - inner_w / 2 + inner_w * i / cols
        box(
            parts,
            "paint",
            (0.02, 0.025, inner_h),
            frame(at(a, z0 + h / 2, d), along, out),
            rng,
            bevel=0.002,
        )
    for j in range(1, rows):
        zz = z0 + rail + inner_h * j / rows
        box(
            parts,
            "paint",
            (inner_w, 0.025, 0.02),
            frame(at(c, zz, d), along, out),
            rng,
            bevel=0.002,
        )
    box(
        parts,
        "glass",
        (inner_w, 0.006, inner_h),
        frame(at(c, z0 + h / 2, d - 0.005), along, out),
        rng,
        bevel=0,
    )


def window(parts, at, along, out, c, w, z, h, rng):
    half = h / 2
    sash(parts, at, along, out, c, w, z + half - 0.02, half + 0.02, -0.075, 2, 2, rng)
    sash(parts, at, along, out, c, w, z, half + 0.02, -0.035, 2, 1, rng)


def door(parts, at, along, out, c, w, z, h, rng):
    d, t = -0.05, 0.045
    stile, top, lock, bottom = 0.12, 0.12, 0.16, 0.22
    for a in (c - w / 2 + stile / 2, c + w / 2 - stile / 2):
        box(parts, "paint", (stile, t, h), frame(at(a, z + h / 2, d), along, out), rng, bevel=0.003)
    lock_z = z + 1.0
    for zz, rh in ((z + h - top / 2, top), (lock_z, lock), (z + bottom / 2, bottom)):
        box(
            parts,
            "paint",
            (w - 2 * stile, t, rh),
            frame(at(c, zz, d), along, out),
            rng,
            bevel=0.003,
        )
    inner = w - 2 * stile
    glass_z0, glass_z1 = lock_z + lock / 2, z + h - top
    sash(parts, at, along, out, c, inner, glass_z0, glass_z1 - glass_z0, d, 2, 3, rng)
    boards, low0, low1 = 5, z + bottom, lock_z - lock / 2
    for i in range(boards):
        a = c - inner / 2 + inner * (i + 0.5) / boards
        box(
            parts,
            "paint",
            (inner / boards - 0.006, 0.03, low1 - low0),
            frame(at(a, (low0 + low1) / 2, d - 0.004), along, out),
            rng,
            bevel=0.004,
            segments=2,
        )
    knob = at(c - w / 2 + 0.075, z + 0.95, -0.0215)
    box(parts, "iron", (0.05, 0.012, 0.16), frame(knob, along, out), rng, bevel=0.004, segments=2)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=0.028)
    bm.loops.layers.uv.new("UVMap")
    bm.transform(Matrix.Translation(knob + out * 0.03))
    parts.add("iron", bm)


def stone_plinth(parts, images, rng):
    """One continuous ring, displaced outward from the scanned stone height map."""
    x0, x1, y0, y1 = -W / 2 - 0.06, W / 2 + 0.06, DECK_Y + 0.06, L + 0.06
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edges = [(Vector((*corners[i], 0)), Vector((*corners[(i + 1) % 4], 0))) for i in range(4)]
    perimeter = sum((b - a).length for a, b in edges)
    step, rows, tile, relief = 0.012, int(round(PLINTH / 0.0125)), 3.2, 0.035
    columns = int(perimeter / step)
    height = images["stone_disp"]
    hh, hw = height.shape
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    grid = []
    for i in range(columns):
        s = i * perimeter / columns
        for a, b in edges:
            if s <= (b - a).length:
                p = a + (b - a).normalized() * s
                break
            s -= (b - a).length
        grid.append(p)
    normals = []
    for i, p in enumerate(grid):
        a, b = grid[i - 1], grid[(i + 1) % columns]
        tangent = (b - a).normalized()
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
            for loop, coord in zip(face.loops, (a[j][1], b[j][1], b[j + 1][1], a[j + 1][1])):
                u, v = coord
                if i == columns - 1 and loop.vert in (b[j][0], b[j + 1][0]):
                    u = perimeter / tile
                loop[uv].uv = (u, v)
    bm.normal_update()
    bm.faces.ensure_lookup_table()
    if bm.faces[0].normal.dot(normals[0]) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    parts.add("stone", bm)
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
    parts.add("stone", cap)


def frame_timbers(parts, rng):
    z_plate = PLATE
    # Corner posts, sill beam and frieze of the body.
    for sx in (-1, 1):
        for y in (0.055, L - 0.055):
            centre = Vector((sx * (W / 2 - 0.055), y, (PLINTH + z_plate) / 2))
            box(
                parts,
                "timber",
                (0.16, 0.16, z_plate - PLINTH),
                Matrix.Translation(centre),
                rng,
                segments=2,
                grain=2,
            )
        box(
            parts,
            "timber",
            (0.07, L + 0.06, 0.18),
            Matrix.Translation((sx * (W / 2 + 0.03), L / 2, PLINTH + 0.09)),
            rng,
            grain=1,
        )
        box(
            parts,
            "timber",
            (0.05, L - 0.2, 0.14),
            Matrix.Translation((sx * (W / 2 + 0.025), L / 2, z_plate - 0.07)),
            rng,
            grain=1,
        )
    box(
        parts,
        "timber",
        (W + 0.06, 0.07, 0.18),
        Matrix.Translation((0, L + 0.03, PLINTH + 0.09)),
        rng,
        grain=0,
    )
    # Main roof: rafter tails, fascia and barge rafters.
    tan = math.tan(PITCH)
    for sx in (-1, 1):
        y = 0.25
        while y < L - 0.15:
            under_beam(
                parts,
                roof_under,
                PITCH,
                sx * (W / 2 - 0.02),
                sx * (W / 2 + EAVE - 0.05),
                y,
                0.05,
                0.14,
                rng,
            )
            y += 0.61
        xf = sx * (W / 2 + EAVE - 0.03)
        zf = roof_under(xf)
        box(
            parts,
            "timber",
            (0.04, L + 2 * RAKE, 0.2),
            Matrix.Translation((xf, L / 2, zf - 0.09)),
            rng,
            grain=1,
        )
        for yb in (-RAKE + 0.04, L + RAKE - 0.04):
            under_beam(
                parts,
                roof_under,
                PITCH,
                sx * 0.001,
                sx * (W / 2 + EAVE),
                yb,
                0.08,
                0.24,
                rng,
                segments=2,
            )
        # Purlins carrying the rake overhang.
        for frac in (0.35, 0.75):
            x = sx * (W / 2) * frac
            for y0, y1 in ((-RAKE + 0.08, 0.2), (L - 0.2, L + RAKE - 0.08)):
                box(
                    parts,
                    "timber",
                    (0.1, y1 - y0, 0.14),
                    Matrix.Translation((x, (y0 + y1) / 2, roof_under(x) - 0.115)),
                    rng,
                    grain=1,
                )
    # Front gable king post and collar visible above the porch roof.
    zc = roof_under(0) - 0.32
    xc = W / 2 - (zc - PLATE - SEAT) / tan
    box(parts, "timber", (2 * xc, 0.12, 0.14), Matrix.Translation((0, -0.12, zc)), rng, segments=2)
    box(
        parts,
        "timber",
        (0.16, 0.14, roof_under(0) - zc - 0.05),
        Matrix.Translation((0, -0.12, (zc + roof_under(0)) / 2 - 0.02)),
        rng,
        segments=2,
        grain=2,
    )


def roof(parts, rng):
    def slope(under, pitch, x_end, y0, y1, seams, ridge_name):
        for sx in (-1, 1):
            a = Vector((0, 0, under(0)))
            b = Vector((sx * x_end, 0, under(x_end)))
            down = (b - a).normalized()
            normal = Vector((sx * math.sin(pitch), 0, math.cos(pitch)))
            length = (b - a).length
            mid = (a + b) / 2
            deck = mid + normal * 0.0125
            box(
                parts,
                "timber",
                (length, y1 - y0, 0.025),
                frame(Vector((deck.x, (y0 + y1) / 2, deck.z)), down, (0, 1, 0)),
                rng,
                bevel=0.002,
                tile=1.2,
                grain=1,
            )
            panels = int(round((y1 - y0) / SEAM_SPACING))
            for k in range(panels):
                py0 = y0 + (y1 - y0) * k / panels
                py1 = y0 + (y1 - y0) * (k + 1) / panels
                sheet = mid + normal * 0.031 + down * 0.02
                box(
                    parts,
                    f"roof{rng.randrange(3)}",
                    (length + 0.04, py1 - py0, 0.012),
                    frame(Vector((sheet.x, (py0 + py1) / 2, sheet.z)), down, (0, 1, 0)),
                    rng,
                    bevel=0.002,
                )
                if k:
                    seam = mid + normal * 0.05 + down * 0.02
                    box(
                        parts,
                        "roof0",
                        (length + 0.04, 0.022, 0.03),
                        frame(Vector((seam.x, py0, seam.z)), down, (0, 1, 0)),
                        rng,
                        bevel=0.006,
                        segments=2,
                    )
            cap = a + normal * 0.075 + down * 0.08
            box(
                parts,
                "roof0",
                (0.17, y1 - y0 + 0.02, 0.012),
                frame(Vector((cap.x, (y0 + y1) / 2, cap.z)), down, (0, 1, 0)),
                rng,
                bevel=0.003,
            )

    slope(roof_under, PITCH, W / 2 + EAVE, -RAKE, L + RAKE, True, "main")
    front = POST_Y - POST / 2 - 0.30
    slope(porch_under, PORCH_PITCH, W / 2 + 0.30, front, -0.005, True, "porch")


def porch(parts, rng):
    # Deck boards run across the porch; rim boards close the edge above the stone.
    box(
        parts,
        "deck",
        (W + 0.08, -DECK_Y + 0.02, 0.04),
        Matrix.Translation((0, DECK_Y / 2 - 0.01 + 0.01, FLOOR - 0.02)),
        rng,
        bevel=0.002,
        tile=2.25,
        grain=1,
    )
    box(
        parts,
        "timber",
        (W + 0.08, 0.035, 0.15),
        Matrix.Translation((0, DECK_Y + 0.0175, FLOOR - 0.115)),
        rng,
        grain=0,
    )
    for sx in (-1, 1):
        box(
            parts,
            "timber",
            (0.035, -DECK_Y, 0.15),
            Matrix.Translation((sx * (W / 2 + 0.055), DECK_Y / 2, FLOOR - 0.115)),
            rng,
            grain=1,
        )
    for x in PORCH_POSTS:
        box(
            parts,
            "timber",
            (POST, POST, HEADER[0] - FLOOR),
            Matrix.Translation((x, POST_Y, (FLOOR + HEADER[0]) / 2)),
            rng,
            bevel=0.012,
            segments=2,
            grain=2,
        )
    hz = (HEADER[0] + HEADER[1]) / 2
    box(
        parts,
        "timber",
        (W + 0.26, 0.22, HEADER[1] - HEADER[0]),
        Matrix.Translation((0, POST_Y, hz)),
        rng,
        bevel=0.01,
        segments=2,
    )
    for sx in (-1, 1):
        box(
            parts,
            "timber",
            (0.22, -POST_Y - 0.1, HEADER[1] - HEADER[0]),
            Matrix.Translation((sx * PORCH_POSTS[2], POST_Y / 2 + 0.05, hz)),
            rng,
            bevel=0.01,
            segments=2,
            grain=1,
        )
    # King-post truss on the header and exposed rafters under the porch roof.
    apex = porch_under(0)
    box(
        parts,
        "timber",
        (0.2, 0.2, apex - HEADER[1] - 0.02),
        Matrix.Translation((0, POST_Y, (apex + HEADER[1]) / 2 - 0.01)),
        rng,
        bevel=0.01,
        segments=2,
        grain=2,
    )
    for sx in (-1, 1):
        for y, width, depth in ((POST_Y, 0.2, 0.2), (-0.72, 0.07, 0.17), (-0.12, 0.07, 0.17)):
            under_beam(
                parts,
                porch_under,
                PORCH_PITCH,
                sx * 0.001,
                sx * (W / 2 + 0.28),
                y,
                width,
                depth,
                rng,
                segments=2,
            )
        yb = POST_Y - POST / 2 - 0.30 + 0.04
        under_beam(
            parts,
            porch_under,
            PORCH_PITCH,
            sx * 0.001,
            sx * (W / 2 + 0.30),
            yb,
            0.08,
            0.22,
            rng,
            segments=2,
        )

    # Railings: 0.91 m guard height, balusters at under 0.1 m clear spacing.
    def railing(p0, p1):
        p0, p1 = Vector((*p0, 0)), Vector((*p1, 0))
        axis = p1 - p0
        length = axis.length
        d = axis.normalized()
        side = Vector((-d.y, d.x, 0))
        for z, t, h in ((FLOOR + 0.885, 0.09, 0.05), (FLOOR + 0.10, 0.07, 0.045)):
            c = (p0 + p1) / 2
            box(
                parts,
                "timber",
                (length, t, h),
                frame(Vector((c.x, c.y, z)), d, side),
                rng,
                bevel=0.006,
                segments=2,
            )
        count = int(length / 0.135)
        for i in range(count):
            p = p0 + d * (length * (i + 0.5) / count)
            box(
                parts,
                "timber",
                (0.04, 0.04, 0.74),
                Matrix.Translation((p.x, p.y, FLOOR + 0.495)),
                rng,
                bevel=0.004,
                grain=2,
            )

    inner = POST / 2
    railing((PORCH_POSTS[0] + inner, POST_Y), (NEWEL_X - 0.075, POST_Y))
    railing((PORCH_POSTS[1] + inner, POST_Y), (PORCH_POSTS[2] - inner, POST_Y))
    for x in (PORCH_POSTS[0], PORCH_POSTS[2]):
        railing((x, POST_Y + inner), (x, -0.03))
    box(
        parts,
        "timber",
        (0.15, 0.15, 1.0),
        Matrix.Translation((NEWEL_X, POST_Y, FLOOR + 0.5)),
        rng,
        bevel=0.01,
        segments=2,
        grain=2,
    )
    box(
        parts,
        "timber",
        (0.19, 0.19, 0.04),
        Matrix.Translation((NEWEL_X, POST_Y, FLOOR + 1.02)),
        rng,
        bevel=0.008,
        segments=2,
    )
    # Stairs: three treads and four risers between grade and the deck.
    x0, x1 = STAIR
    width = x1 - x0
    for k in range(1, 4):
        top = FLOOR - k * RISER
        ya, yb = DECK_Y - TREAD * k - 0.025, DECK_Y - TREAD * (k - 1)
        box(
            parts,
            "deck",
            (width, yb - ya, 0.04),
            Matrix.Translation(((x0 + x1) / 2, (ya + yb) / 2, top - 0.02)),
            rng,
            bevel=0.004,
            segments=2,
            tile=2.25,
            grain=1,
        )
        for x in (x0 + 0.02, x1 - 0.02):
            box(
                parts,
                "timber",
                (0.04, yb - ya - 0.03, top - 0.04),
                Matrix.Translation((x, (ya + yb) / 2 + 0.015, (top - 0.04) / 2)),
                rng,
                grain=1,
            )
    for k in range(2, 5):
        top = FLOOR - (k - 1) * RISER - 0.04
        bottom = FLOOR - k * RISER if k < 4 else 0
        y = DECK_Y - TREAD * (k - 1)
        box(
            parts,
            "timber",
            (width - 0.08, 0.025, top - bottom),
            Matrix.Translation(((x0 + x1) / 2, y + 0.0125, (top + bottom) / 2)),
            rng,
        )


def interior(parts, rng):
    box(
        parts,
        "interior",
        (W - 0.3, L - 0.3, 0.02),
        Matrix.Translation((0, L / 2, FLOOR - 0.01)),
        rng,
        bevel=0,
    )
    box(
        parts,
        "interior",
        (W - 0.3, L - 0.3, 0.02),
        Matrix.Translation((0, L / 2, 3.0)),
        rng,
        bevel=0,
    )


def plank_bands(pixels, width, height):
    """Image-V intervals of individual planks, found from the dark gaps between them."""
    rows = pixels.reshape(height, width, 4)[:, :, :3].mean(axis=(1, 2))
    smooth = np.convolve(rows, np.ones(9) / 9, mode="same")
    gaps = np.flatnonzero(smooth < smooth.mean() - 0.8 * smooth.std())
    edges = [g for i, g in enumerate(gaps) if i == 0 or g - gaps[i - 1] > 20]
    spans = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 60]
    typical = np.median([b - a for a, b in spans])
    bands = []
    for a, b in spans:
        if 0.75 * typical < b - a < 1.3 * typical:
            trim = 0.12 * (b - a)
            # Blender pixel rows run bottom-up, so row index maps directly to image V.
            bands.append(((a + trim) / height, (b - trim) / height))
    assert len(bands) >= 8, len(bands)
    return bands


def matched(path, target, name, output):
    """Match a CC0 albedo's sRGB mean and luminance spread to a reference region."""
    image = bpy.data.images.load(str(path))
    pixels = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    # Byte images expose their stored sRGB values, not linear radiance.
    srgb = pixels.reshape(-1, 4)[:, :3]
    mean = srgb.mean(axis=0)
    goal = np.array(target[0]) / 255
    gray = srgb.mean(axis=1, keepdims=True)
    gain = target[1] / 255 / gray.std()
    detail = (srgb - mean) * 0.6 + (gray - gray.mean()) * 0.4
    pixels.reshape(-1, 4)[:, :3] = np.clip(goal + detail * gain, 0, 1)
    bands = plank_bands(pixels, image.size[0], image.size[1]) if "flooring" in path.name else None
    derived = bpy.data.images.new(name, image.size[0], image.size[1])
    derived.pixels.foreach_set(pixels)
    derived.filepath_raw = str(output / f"{name}.png")
    derived.file_format = "PNG"
    derived.save()
    bpy.data.images.remove(image)
    derived.source = "FILE"
    derived.reload()
    return (
        derived,
        bands,
        {
            "source": str(path.relative_to(ROOT)),
            "sourceMeanSrgb": (mean * 255).round(1).tolist(),
            "targetMeanSrgb": list(target[0]),
            "targetLuminanceStd": target[1],
            "planks": len(bands) if bands else None,
            "path": str((output / f"{name}.png").relative_to(ROOT)),
            "sha256": digest(output / f"{name}.png"),
        },
    )


def materials(output):
    def image(path, colour=False):
        img = bpy.data.images.load(str(path))
        img.colorspace_settings.name = "sRGB" if colour else "Non-Color"
        return img

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
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        bsdf = nodes["Principled BSDF"]
        if base_image:
            n = nodes.new("ShaderNodeTexImage")
            n.image = base_image
            links.new(n.outputs["Color"], bsdf.inputs["Base Color"])
        else:
            bsdf.inputs["Base Color"].default_value = (*base, 1)
        if rough_image:
            n = nodes.new("ShaderNodeTexImage")
            n.image = rough_image
            links.new(n.outputs["Color"], bsdf.inputs["Roughness"])
        else:
            bsdf.inputs["Roughness"].default_value = rough
        if normal_image:
            n = nodes.new("ShaderNodeTexImage")
            n.image = normal_image
            nm = nodes.new("ShaderNodeNormalMap")
            nm.inputs["Strength"].default_value = strength
            links.new(n.outputs["Color"], nm.inputs["Color"])
            links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
        bsdf.inputs["Metallic"].default_value = metallic
        if transmission:
            bsdf.inputs["Transmission Weight"].default_value = transmission
            bsdf.inputs["IOR"].default_value = 1.5
        return mat

    def lin(srgb):
        return tuple(((c / 255 + 0.055) / 1.055) ** 2.4 for c in srgb)

    derived = {}
    planks = TEXTURES / "plank_flooring_03/plank_flooring_03_diff_4k.jpg"
    wood, _, derived["timber"] = matched(
        TEXTURES / "rough_wood/rough_wood_diff_4k.jpg", TARGETS["timber"], "timber_albedo", output
    )
    stone, _, derived["stone"] = matched(
        TEXTURES / "stone_wall/stone_wall_diff_4k.jpg", TARGETS["stone"], "stone_albedo", output
    )
    deck, _, derived["deck"] = matched(planks, TARGETS["deck"], "deck_albedo", output)
    boards, bands, derived["siding"] = matched(planks, TARGETS["siding"], "siding_albedo", output)
    plank_rough = image(TEXTURES / "plank_flooring_03/plank_flooring_03_rough_4k.jpg")
    plank_normal = image(TEXTURES / "plank_flooring_03/plank_flooring_03_nor_gl_4k.jpg")
    wood_n = image(TEXTURES / "rough_wood/rough_wood_nor_gl_4k.jpg")
    mats = {
        "timber": principled(
            "Weathered timber",
            base_image=wood,
            rough_image=image(TEXTURES / "rough_wood/rough_wood_rough_4k.jpg"),
            normal_image=wood_n,
            strength=1.2,
        ),
        "stone": principled(
            "Granite plinth",
            base_image=stone,
            rough_image=image(TEXTURES / "stone_wall/stone_wall_rough_4k.jpg"),
            normal_image=image(TEXTURES / "stone_wall/stone_wall_nor_gl_4k.jpg"),
            strength=1.0,
        ),
        "deck": principled(
            "Porch decking",
            base_image=deck,
            rough_image=plank_rough,
            normal_image=plank_normal,
            strength=1.0,
        ),
        "siding": principled(
            "Weathered siding",
            base_image=boards,
            rough_image=plank_rough,
            normal_image=plank_normal,
            strength=1.3,
        ),
        "paint": principled(
            "Dark green paint",
            base=lin((44, 50, 41)),
            rough=0.55,
            normal_image=wood_n,
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
    return mats, derived, bands


def main():
    sites = list((ROOT / ".venv-cosys/lib").glob("python*/site-packages"))
    assert len(sites) == 1
    sys.path.extend([str(ROOT), str(sites[0])])
    from mission_planner.environment import load_env

    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=24)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    output = (ROOT / args.output).resolve()
    assert output.is_relative_to((ROOT / ".local/world/valley").resolve())
    output.mkdir(parents=True)
    started = time.monotonic()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    lock = json.loads((ROOT / "world/unreal/cabin-materials.lock.json").read_text())
    for entry in lock["files"]:
        path = TEXTURES / entry["asset"] / Path(entry["url"]).name
        assert hashlib.md5(path.read_bytes()).hexdigest() == entry["md5"], path
    mats, derived, bands = materials(output)
    disp = bpy.data.images.load(str(TEXTURES / "stone_wall/stone_wall_disp_4k.png"))
    disp.colorspace_settings.name = "Non-Color"
    pixels = np.empty(disp.size[0] * disp.size[1] * 4, dtype=np.float32)
    disp.pixels.foreach_get(pixels)
    # Blender stores rows bottom-up; flip so row 0 is the image top like the UV lookup expects.
    height = pixels.reshape(disp.size[1], disp.size[0], 4)[::-1, :, 0].copy()
    bpy.data.images.remove(disp)
    rng = random.Random(args.seed)
    parts = Parts()
    stone_plinth(parts, {"stone_disp": height}, rng)
    siding(parts, rng, bands)
    openings(parts, rng)
    frame_timbers(parts, rng)
    porch(parts, rng)
    roof(parts, rng)
    interior(parts, rng)
    objects = parts.build(mats)
    for obj in objects:
        obj.data.polygons.foreach_set("use_smooth", [True] * len(obj.data.polygons))
    bpy.context.view_layer.update()
    corners = np.array([obj.matrix_world @ Vector(c) for obj in objects for c in obj.bound_box])
    low, high = corners.min(axis=0), corners.max(axis=0)
    shift = Vector((-(low[0] + high[0]) / 2, -(low[1] + high[1]) / 2, -low[2]))
    root = bpy.data.objects.new("RangerCabin", None)
    bpy.context.scene.collection.objects.link(root)
    for obj in objects:
        obj.parent = root
    root.location = shift
    bpy.context.view_layer.update()
    triangles = 0
    for obj in objects:
        obj.data.calc_loop_triangles()
        triangles += len(obj.data.loop_triangles)
    blend = output / "cabin.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), compress=True)
    glb = output / "ranger-cabin.glb"
    exported = bpy.ops.export_scene.gltf(
        filepath=str(glb),
        export_format="GLB",
        export_image_format="JPEG",
        export_image_quality=92,
        export_apply=True,
        export_cameras=False,
        export_lights=False,
        export_yup=True,
    )
    assert exported == {"FINISHED"}
    for img in bpy.data.images:
        if img.size[0] > 2048:
            img.scale(2048, 2048)
    web = output / "ranger-cabin-web.glb"
    exported = bpy.ops.export_scene.gltf(
        filepath=str(web),
        export_format="GLB",
        export_image_format="JPEG",
        export_image_quality=85,
        export_apply=True,
        export_cameras=False,
        export_lights=False,
        export_yup=True,
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=7,
    )
    assert exported == {"FINISHED"}
    extents = (high - low).tolist()
    report = {
        "asset": "ranger-cabin",
        "script": str(Path(__file__).relative_to(ROOT)),
        "scriptSha256": digest(__file__),
        "blenderVersion": bpy.app.version_string,
        "blenderBuildHash": bpy.app.build_hash.decode(),
        "seed": args.seed,
        "unitsAndAxes": "metres; Blender +Z up, -Y porch side; glTF exported +Y up",
        "originShiftFromBuildFrameM": list(shift),
        "extentsM": {"x": extents[0], "y": extents[1], "z": extents[2]},
        "dimensionsM": {
            "bodyWidth": W,
            "bodyLength": L,
            "plinthTop": PLINTH,
            "floor": FLOOR,
            "wallPlate": PLATE,
            "mainPitchDeg": math.degrees(PITCH),
            "porchPitchDeg": math.degrees(PORCH_PITCH),
            "eaveOverhang": EAVE,
            "rakeOverhang": RAKE,
            "porchPostY": POST_Y,
            "deckEdgeY": DECK_Y,
            "porchHeaderBottom": HEADER[0],
            "stairRiser": RISER,
            "stairTread": TREAD,
            "sidingExposure": EXPOSURE,
            "standingSeamSpacing": SEAM_SPACING,
            "openings": OPENINGS,
            "mainRidgeUnderside": roof_under(0),
            "porchRidgeUnderside": porch_under(0),
        },
        "landmarkEnvelope": {"footprintM": [14, 14], "heightM": 6},
        "withinEnvelope": extents[0] <= 14 and extents[1] <= 14 and extents[2] <= 6,
        "triangles": triangles,
        "objects": {obj.name: len(obj.data.polygons) for obj in objects},
        "materialSources": lock,
        "derivedAlbedo": derived,
        "outputs": {
            p.name: {"bytes": p.stat().st_size, "sha256": digest(p)} for p in (blend, glb, web)
        },
        "elapsedSeconds": time.monotonic() - started,
    }
    assert report["withinEnvelope"], extents
    (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        "CABIN " + json.dumps({"extentsM": report["extentsM"], "triangles": triangles}), flush=True
    )


if __name__ == "__main__":
    main()

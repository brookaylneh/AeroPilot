"""Valley bilinear ground heights: metres north/east and metres UE up."""

import hashlib
import json
import math
import sys
from array import array
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORLD = json.loads((ROOT / "world/cosys/world.json").read_text())
assert (WORLD["coordinateSystem"], WORLD["north"], WORLD["east"], WORLD["moveWorldOrigin"]) == (
    "UE_LH_Z_UP_CENTIMETERS",
    "+X",
    "+Y",
    False,
)
BOUNDS = WORLD["searchBoundsCm"]
FIELD = WORLD["heightfield"]
assert (FIELD["rowAxis"], FIELD["columnAxis"], FIELD["encoding"]) == (
    "+Y",
    "+X",
    "uint16-little-endian",
)
ROWS, COLUMNS = FIELD["rows"], FIELD["columns"]
assert all(isinstance(n, int) and n > 1 for n in (ROWS, COLUMNS))
assert FIELD["zeroSample"] == 32768 and FIELD["samplesPerMetre"] == 128
for axis, count in (("x", COLUMNS), ("y", ROWS)):
    assert math.isclose(
        (BOUNDS["max"][axis] - BOUNDS["min"][axis]) / 100,
        (count - 1) * FIELD["sampleSpacingMetres"],
        abs_tol=1e-10,
    )
NORTH_MIN_M, NORTH_MAX_M = (BOUNDS[end]["x"] / 100 for end in ("min", "max"))
EAST_MIN_M, EAST_MAX_M = (BOUNDS[end]["y"] / 100 for end in ("min", "max"))


@lru_cache(maxsize=1)
def heights():
    values = array("H")
    path = ROOT / FIELD["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == WORLD["heightfieldSha256"]
    with path.open("rb") as stream:
        values.fromfile(stream, ROWS * COLUMNS)
        assert not stream.read(), "Unexpected heightfield size"
    if sys.byteorder != "little":
        values.byteswap()
    assert len(values) == ROWS * COLUMNS
    return values


@lru_cache(maxsize=1)
def domain():
    """Rectangular heightfield bounds and a conservative UE-up minimum, all in metres."""
    low = NORTH_MIN_M, EAST_MIN_M
    high = NORTH_MAX_M, EAST_MAX_M
    minimum = min(
        (min(heights()) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"],
        WORLD["port"]["centerUeCm"]["z"] / 100,
    )
    assert all(math.isfinite(value) for value in (*low, *high, minimum))
    return low, high, minimum


def terrain_height(x, y):
    port = WORLD["port"]
    if all(
        abs(value - port["centerUeCm"][axis] / 100) <= width / 2
        for value, axis, width in zip((x, y), "xy", port["dimensionsMetres"])
    ):
        return port["centerUeCm"]["z"] / 100
    if NORTH_MIN_M - 40 <= x < NORTH_MIN_M and -25 <= y <= 25:
        levels = []
        for a, b, c in launch_triangles():
            denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            u = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
            v = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
            if min(u, v, 1 - u - v) >= -1e-9:
                levels.append(u * a[2] + v * b[2] + (1 - u - v) * c[2])
        assert levels, "Authored apron does not cover launch corridor"
        return max(levels)
    i = (x - NORTH_MIN_M) / (NORTH_MAX_M - NORTH_MIN_M) * (COLUMNS - 1)
    j = (y - EAST_MIN_M) / (EAST_MAX_M - EAST_MIN_M) * (ROWS - 1)
    if not (0 <= i <= COLUMNS - 1 and 0 <= j <= ROWS - 1):
        raise ValueError(
            f"Outside mapped terrain at north {x:.3f} m, east {y:.3f} m; "
            "terrain is available within the world map and launch corridor"
        )
    a, b = min(int(i), COLUMNS - 2), min(int(j), ROWS - 2)
    u, v = i - a, j - b
    values = heights()
    return sum(
        (values[row * COLUMNS + col] - FIELD["zeroSample"]) / FIELD["samplesPerMetre"] * weight
        for row, col, weight in (
            (b, a, (1 - u) * (1 - v)),
            (b, a + 1, u * (1 - v)),
            (b + 1, a, (1 - u) * v),
            (b + 1, a + 1, u * v),
        )
    )


@lru_cache(maxsize=1)
def launch_triangles():
    # These are the same apron triangles and slab top authored by build-open-meadow.py.
    from world.unreal.horizon import apron, crests

    ridges = crests(
        (BOUNDS["min"]["x"], BOUNDS["min"]["y"]),
        (BOUNDS["max"]["x"], BOUNDS["max"]["y"]),
    )
    reach = max(math.hypot(x, y) for vertices, _ in ridges for x, y, _ in vertices)
    vertices, indices = apron(reach + 100000)
    triangles = []
    for indices in indices:
        triangle = tuple(tuple(value / 100 for value in vertices[i]) for i in indices)
        if (
            min(p[0] for p in triangle) <= NORTH_MIN_M
            and max(p[0] for p in triangle) >= NORTH_MIN_M - 40
            and min(p[1] for p in triangle) <= 25
            and max(p[1] for p in triangle) >= -25
        ):
            triangles.append(triangle)
    port = WORLD["port"]
    n, e, z = (port["centerUeCm"][axis] / 100 for axis in "xyz")
    dn, de = (size / 2 for size in port["dimensionsMetres"][:2])
    corners = [(n - dn, e - de, z), (n + dn, e - de, z), (n + dn, e + de, z), (n - dn, e + de, z)]
    triangles.extend(((corners[0], corners[1], corners[2]), (corners[0], corners[2], corners[3])))
    return triangles


def launch_breaks(a, b):
    start = a["north_m"], a["east_m"]
    delta = b["north_m"] - start[0], b["east_m"] - start[1]
    cuts = []
    triangles = launch_triangles()
    if min(a["north_m"], b["north_m"]) >= NORTH_MIN_M:
        triangles = triangles[-2:]
    for triangle in triangles:
        for c, d in zip(triangle, (*triangle[1:], triangle[0])):
            edge = d[0] - c[0], d[1] - c[1]
            denominator = delta[0] * edge[1] - delta[1] * edge[0]
            if denominator == 0:
                continue
            offset = c[0] - start[0], c[1] - start[1]
            t = (offset[0] * edge[1] - offset[1] * edge[0]) / denominator
            u = (offset[0] * delta[1] - offset[1] * delta[0]) / denominator
            if 0 <= t <= 1 and 0 <= u <= 1:
                cuts.append(t)
    return cuts

"""Distant granite crests and the meadow apron from the landscape edge out beneath them.

Pure Python: Unreal's editor Python imports this module.
"""

import math
import random

from world.cosys.terrain import BOUNDS, COLUMNS, ROWS, terrain_height

# Per layer, near to far: metres beyond the rectangle's circumcircle and crest height range (m).
CREST_LAYERS = ((3500, 150, 480), (6500, 300, 750), (11000, 550, 1150), (17000, 800, 1350))
CREST_AZIMUTHS = 720
# Cross-section rows from the valley-facing foot over the crest to the far foot.
CREST_ROWS = tuple(math.copysign(abs(i / 6 - 1) ** 1.25, i / 6 - 1) for i in range(13))
# Apron spokes run through every fourth edge sample; ring steps grow geometrically from about 2 m.
APRON_STRIDE = 4
APRON_RINGS, APRON_GROWTH = 53, 1.15
# The edge blends over 1 km into foothills 2.5 standard deviations above the mean edge, so crest
# feet stay buried; small swells start beyond the drone port. (waves, shortest m, longest m, sd m)
APRON_HILLS = (8, 600, 3000, 12)
APRON_SWELLS = (8, 25, 120, 0.6)
APRON_SKIRT_M = (2, 0.5)


def crests(lower, upper):
    """Seeded overlapping granite crests around a UE-centimetre rectangle, one mesh per layer.

    Each layer is a closed ridge of (vertices, triangles): x north, y east, z up in cm, wound
    with upward normals. Ridged peaks rise toward the south-southeast like the Cathedral Range,
    and farther layers rise higher, so their summits show between nearer crests. Import the
    meshes without collision; each layer can take its own aerial-perspective material.
    """
    assert all(lower[axis] < upper[axis] for axis in (0, 1))
    centre = [(lower[axis] + upper[axis]) / 2 for axis in (0, 1)]
    circumradius = math.hypot(upper[0] - lower[0], upper[1] - lower[1]) / 2
    rng = random.Random(4114)
    layers = []
    for distance, low, high in CREST_LAYERS:
        waves = [(k, rng.uniform(0, math.tau), rng.uniform(0.4, 1) / k**0.8) for k in range(2, 25)]
        bends = [(k, rng.uniform(0, math.tau), rng.uniform(0.3, 1) / k) for k in range(1, 7)]
        azimuths = [math.tau * i / CREST_AZIMUTHS for i in range(CREST_AZIMUTHS)]
        ridged = [
            sum(a * (1 - abs(math.sin(k * t / 2 + p))) ** 2 for k, p, a in waves) for t in azimuths
        ]
        bottom, top = min(ridged), max(ridged)
        vertices = []
        for t, value in zip(azimuths, ridged):
            envelope = 0.75 + 0.25 * math.cos(t - math.radians(150))
            height = 100 * (
                low + (high - low) * ((value - bottom) / (top - bottom)) ** 1.3 * envelope
            )
            wobble = 0.05 * distance * sum(a * math.sin(k * t + p) for k, p, a in bends)
            radius = circumradius + 100 * (distance + wobble)
            for u in CREST_ROWS:
                reach = radius + u * 2.4 * height
                vertices.append(
                    (
                        centre[0] + reach * math.cos(t),
                        centre[1] + reach * math.sin(t),
                        height * (1 - abs(u)) ** 1.3,
                    )
                )
        rows = len(CREST_ROWS)
        triangles = []
        for i in range(CREST_AZIMUTHS):
            a, b = i * rows, (i + 1) % CREST_AZIMUTHS * rows
            for j in range(rows - 1):
                triangles += [(a + j, b + j + 1, b + j), (a + j, a + j + 1, b + j + 1)]
        layers.append((vertices, triangles))
    return layers


def waves(rng, count, shortest, longest, deviation):
    """Seeded plane waves over metres north and east with the given standard deviation."""
    terms = []
    for _ in range(count):
        angle, number = rng.uniform(0, math.tau), math.tau / rng.uniform(shortest, longest)
        terms.append((number * math.cos(angle), number * math.sin(angle), rng.uniform(0, math.tau)))
    scale = deviation * math.sqrt(2 / count)
    return lambda x, y: scale * sum(math.sin(a * x + b * y + p) for a, b, p in terms)


def smooth(value):
    value = min(max(value, 0), 1)
    return value * value * (3 - 2 * value)


def apron(radius):
    """Meadow ground from the landscape edge out to `radius` cm around its centre, as (vertices,
    triangles) wound like crests().

    Radial spokes start on edge samples at their exact heights; a skirt ring under the landscape
    hides LOD cracks at the seam.
    """
    lower, upper = BOUNDS["min"], BOUNDS["max"]
    assert all(lower[axis] == -upper[axis] for axis in "xy")
    columns = range(0, COLUMNS, APRON_STRIDE)
    rows = range(0, ROWS, APRON_STRIDE)
    assert columns[-1] == COLUMNS - 1 and rows[-1] == ROWS - 1
    edge = {(i, j) for i in columns for j in rows if i in (0, COLUMNS - 1) or j in (0, ROWS - 1)}
    starts = []
    for i, j in edge:
        x = lower["x"] + (upper["x"] - lower["x"]) * i / (COLUMNS - 1)
        y = lower["y"] + (upper["y"] - lower["y"]) * j / (ROWS - 1)
        starts.append((math.atan2(y, x), x, y))
    starts.sort()
    assert max(math.hypot(x, y) for _, x, y in starts) < radius
    rng = random.Random(4121)
    hills, swells = waves(rng, *APRON_HILLS), waves(rng, *APRON_SWELLS)
    heights = [terrain_height(x / 100, y / 100) for _, x, y in starts]
    level = sum(heights) / len(heights) + 2.5 * APRON_HILLS[3]
    inward, drop = APRON_SKIRT_M
    fractions = [
        (APRON_GROWTH**k - 1) / (APRON_GROWTH**APRON_RINGS - 1) for k in range(APRON_RINGS + 1)
    ]
    vertices = []
    for (t, x, y), edge_m in zip(starts, heights):
        reach = math.hypot(x, y)
        skirt = 1 - inward * 100 / reach
        vertices.append(
            (x * skirt, y * skirt, 100 * (terrain_height(x * skirt / 100, y * skirt / 100) - drop))
        )
        span = radius - reach
        for fraction in fractions:
            px, py = x + span * fraction * math.cos(t), y + span * fraction * math.sin(t)
            out, north, east = span * fraction / 100, px / 100, py / 100
            far = level + hills(north, east) - edge_m
            z = (
                edge_m
                + smooth((out - 30) / 1000) * far
                + smooth((out - 30) / 70) * swells(north, east)
            )
            vertices.append((px, py, 100 * z))
    spokes, count = len(starts), APRON_RINGS + 2
    triangles = []
    for i in range(spokes):
        a, b = i * count, (i + 1) % spokes * count
        for j in range(count - 1):
            triangles += [(a + j, b + j + 1, b + j), (a + j, a + j + 1, b + j + 1)]
    return vertices, triangles

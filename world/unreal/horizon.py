"""Distant granite crests and clipping of scenery triangles out of the searchable rectangle.

Pure Python: Unreal's editor Python imports this module.
"""

import math
import random

# Per layer, near to far: metres beyond the rectangle's circumcircle and crest height range (m).
CREST_LAYERS = ((3500, 150, 480), (6500, 300, 750), (11000, 550, 1150), (17000, 800, 1350))
CREST_AZIMUTHS = 720
# Cross-section rows from the valley-facing foot over the crest to the far foot.
CREST_ROWS = (-1, -0.62, -0.28, 0, 0.3, 0.65, 1)


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
        waves = [(k, rng.uniform(0, math.tau), rng.uniform(0.4, 1) / k**0.8) for k in range(2, 64)]
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


def outside_rectangle(triangle, lower, upper):
    assert len(triangle) == 3 and len({len(vertex) for vertex in triangle}) == 1
    assert all(lower[axis] < upper[axis] for axis in (0, 1))
    for axis in (0, 1):
        if (
            max(v[axis] for v in triangle) <= lower[axis]
            or min(v[axis] for v in triangle) >= upper[axis]
        ):
            return [triangle]
    remainder = list(triangle)
    outside = []
    for axis, boundary, sign in (
        (0, lower[0], 1),
        (0, upper[0], -1),
        (1, lower[1], 1),
        (1, upper[1], -1),
    ):
        if not remainder:
            break
        inside, rejected = [], []
        for previous, current in zip(remainder[-1:] + remainder[:-1], remainder):
            a = sign * (previous[axis] - boundary)
            b = sign * (current[axis] - boundary)
            if (a >= 0) != (b >= 0):
                weight = a / (a - b)
                intersection = tuple(p + weight * (c - p) for p, c in zip(previous, current))
                intersection = (*intersection[:axis], boundary, *intersection[axis + 1 :])
                inside.append(intersection)
                rejected.append(intersection)
            (inside if b >= 0 else rejected).append(current)
        for index in range(1, len(rejected) - 1):
            a, b, c = rejected[0], rejected[index], rejected[index + 1]
            area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if area != 0:
                outside.append((a, b, c))
        remainder = inside
    return outside

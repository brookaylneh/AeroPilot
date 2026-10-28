"""Nadir image footprints clipped to the measured heightfield domain."""

from functools import lru_cache

import numpy as np

from world.cosys.terrain import COLUMNS, ROWS, WORLD, heights, terrain_height


@lru_cache(maxsize=1)
def domain():
    values = np.asarray(heights(), dtype=np.int32)
    assert ROWS * COLUMNS == len(values)
    values = values.reshape(ROWS, COLUMNS)
    bounds = WORLD["searchBoundsCm"]
    low = np.array([bounds["min"][k] / 100 for k in "xy"])
    high = np.array([bounds["max"][k] / 100 for k in "xy"])
    step = (high - low) / np.array([COLUMNS - 1, ROWS - 1])

    def point(row, column):
        xy = np.minimum(high, low + step * [column, row])
        return np.array([*xy, terrain_height(*xy)])

    minimum = point(*np.unravel_index(values.argmin(), values.shape))[2]
    maximum = point(*np.unravel_index(values.argmax(), values.shape))[2]
    slopes = []
    for axis in (1, 0):
        differences = np.diff(values, axis=axis)
        index = np.unravel_index(np.abs(differences).argmax(), differences.shape)
        adjacent = list(index)
        adjacent[axis] += 1
        slopes.append(abs(point(*index)[2] - point(*adjacent)[2]) / step[1 - axis])
    border = (
        [(0, i) for i in range(COLUMNS - 1)]
        + [(i, COLUMNS - 1) for i in range(ROWS - 1)]
        + [(ROWS - 1, i) for i in range(COLUMNS - 1, 0, -1)]
        + [(i, 0) for i in range(ROWS - 1, 0, -1)]
    )
    return low, high, minimum, maximum, np.array(slopes), np.array([point(*p) for p in border])


def direction(camera, pixel):
    u, v = pixel
    ray = camera.rotation @ np.array(
        [1, (u - camera.width / 2) / camera.focal, (v - camera.height / 2) / camera.focal]
    )
    return ray * [1, 1, -1]


def intersection(camera, ray, terrain):
    low, high, minimum, maximum, slopes, _ = terrain
    start = np.array([camera.world[k] for k in "xyz"])
    assert -ray[2] > slopes @ np.abs(ray[:2]), "Ray must descend faster than the terrain slope"
    enter, leave = 0.0, float("inf")
    for axis, (a, b) in enumerate(zip([*low, minimum], [*high, maximum])):
        if abs(ray[axis]) < 1e-12:
            if not a <= start[axis] <= b:
                return None
        else:
            first, last = sorted(((a - start[axis]) / ray[axis], (b - start[axis]) / ray[axis]))
            enter, leave = max(enter, first), min(leave, last)
    if enter > leave:
        return None

    def sample(distance):
        p = start + distance * ray
        assert np.all(p[:2] >= low - 1e-8) and np.all(p[:2] <= high + 1e-8)
        p[:2] = np.clip(p[:2], low, high)
        return p, p[2] - terrain_height(*p[:2])

    first, first_error = sample(enter)
    last, last_error = sample(leave)
    if abs(first_error) <= 1e-6:
        return first
    if abs(last_error) <= 1e-6:
        return last
    if first_error < 0 or last_error > 0:
        return None
    for _ in range(60):
        midpoint = (enter + leave) / 2
        p, error = sample(midpoint)
        if abs(error) <= 1e-6:
            return p
        if error > 0:
            enter = midpoint
        else:
            leave = midpoint
    raise AssertionError("Bounded terrain intersection did not converge to 1 micrometre")


def clip(vertices, normal):
    if not len(vertices):
        return vertices
    signed = vertices @ normal
    result = []
    for a, b, da, db in zip(vertices, np.roll(vertices, -1, axis=0), signed, np.roll(signed, -1)):
        if (da >= 0) != (db >= 0):
            result.append(a + da / (da - db) * (b - a))
        if db >= 0:
            result.append(b)
    return np.asarray(result).reshape(-1, 3)


def footprint(camera):
    terrain = domain()
    low, high, minimum, _, _, border = terrain
    pixels = [
        (0, 0),
        (camera.width - 1, 0),
        (camera.width - 1, camera.height - 1),
        (0, camera.height - 1),
    ]
    rays = [direction(camera, pixel) for pixel in pixels]
    assert all(-ray[2] > terrain[4] @ np.abs(ray[:2]) for ray in rays), (
        "Image rays must descend faster than the terrain slope"
    )
    origin = np.array([camera.world[k] for k in "xyz"])
    far = np.array([origin + (minimum - origin[2]) / ray[2] * ray for ray in rays])
    if (
        np.all(origin[:2] >= low)
        and np.all(origin[:2] <= high)
        and np.all(far[:, :2] >= low)
        and np.all(far[:, :2] <= high)
    ):
        return [camera.ground(pixel) for pixel in pixels]
    view = ((border - origin) * [1, 1, -1]) @ camera.rotation
    for normal in (
        [camera.width / 2 / camera.focal, 1, 0],
        [(camera.width / 2 - 1) / camera.focal, -1, 0],
        [camera.height / 2 / camera.focal, 0, 1],
        [(camera.height / 2 - 1) / camera.focal, 0, -1],
    ):
        view = clip(view, normal)
    points = []
    for v in view:
        assert v[0] > 0
        ray = camera.rotation @ (v / v[0]) * [1, 1, -1]
        p = intersection(camera, ray, terrain)
        assert p is not None, "Clipped frustum vertex must intersect the heightfield"
        if not points or np.linalg.norm(p[:2] - points[-1][:2]) > 1e-6:
            points.append(p)
    if len(points) > 1 and np.linalg.norm(points[0][:2] - points[-1][:2]) <= 1e-6:
        points.pop()
    keep = []
    for i, p in enumerate(points):
        before, after = points[i - 1], points[(i + 1) % len(points)]
        a, b = p[:2] - before[:2], after[:2] - p[:2]
        if abs(np.cross(a, b)) > 1e-6 * (np.linalg.norm(a) + np.linalg.norm(b)):
            keep.append({k: float(p[j]) for j, k in enumerate("xyz")})
    assert not keep or len(keep) >= 3
    return keep

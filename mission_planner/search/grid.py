"""Polygon-clipped cell areas in mission north/east metres."""

import numpy as np

from mission_planner.gcs.geodesy import BOUND_M, segment_in_polygon

CELL_M = 4.0


def inside(points, polygon):
    result = np.zeros(len(points), dtype=bool)
    n, e = points.T
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        an, ae = a["north_m"], a["east_m"]
        bn, be = b["north_m"], b["east_m"]
        if ae != be:
            result ^= ((ae > e) != (be > e)) & (n < (bn - an) * (e - ae) / (be - ae) + an)
    return result


def area(vertices):
    if len(vertices) < 3:
        return 0.0
    p = np.array(vertices)
    return float(
        abs(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1))) / 2
    )


def clipped_area(polygon, low, high):
    vertices = [(p["north_m"], p["east_m"]) for p in polygon]
    for axis in (0, 1):
        for bound, sign in ((low[axis], 1), (high[axis], -1)):
            output = []
            for a, b in zip(vertices, vertices[1:] + vertices[:1]):
                ain = (a[axis] - bound) * sign >= 0
                bin = (b[axis] - bound) * sign >= 0
                if ain != bin:
                    t = (bound - a[axis]) / (b[axis] - a[axis])
                    output.append(tuple(a[k] + t * (b[k] - a[k]) for k in (0, 1)))
                if bin:
                    output.append(b)
            vertices = output
    return area(vertices)


def make_grid(fence, cell_m=CELL_M):
    assert 0 < cell_m <= CELL_M
    edges = np.append(np.arange(-BOUND_M, BOUND_M, cell_m), BOUND_M)
    lo_n, lo_e = np.meshgrid(edges[:-1], edges[:-1], indexing="ij")
    hi_n, hi_e = np.meshgrid(edges[1:], edges[1:], indexing="ij")
    low = np.column_stack([lo_n.ravel(), lo_e.ravel()])
    high = np.column_stack([hi_n.ravel(), hi_e.ravel()])
    grid = {
        "centers": (low + high) / 2,
        "low": low,
        "high": high,
        "shape": [len(edges) - 1] * 2,
        "cell_m": cell_m,
    }
    grid["areas"] = polygon_areas(grid, fence)
    assert grid["areas"].sum() > 0
    return grid


def polygon_areas(grid, polygon):
    low, high = grid["low"], grid["high"]
    hits = np.stack(
        [
            inside(np.column_stack([n, e]), polygon)
            for n, e in (
                (low[:, 0], low[:, 1]),
                (low[:, 0], high[:, 1]),
                (high[:, 0], low[:, 1]),
                (high[:, 0], high[:, 1]),
            )
        ]
    )
    full = np.all(hits, axis=0)
    partial = np.any(hits, axis=0) & ~full
    # A polygon edge can cross a cell without including any corner.
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        n0, n1 = sorted([a["north_m"], b["north_m"]])
        e0, e1 = sorted([a["east_m"], b["east_m"]])
        partial |= (high[:, 0] >= n0) & (low[:, 0] <= n1) & (high[:, 1] >= e0) & (low[:, 1] <= e1)
    result = np.where(full, np.prod(high - low, axis=1), 0.0)
    for i in np.flatnonzero(partial):
        result[i] = clipped_area(polygon, low[i], high[i])
    assert np.isfinite(result).all() and (result >= 0).all()
    return result


def validate_area(vertices, fence):
    assert 3 <= len(vertices) <= 32 and all(set(p) == {"north_m", "east_m"} for p in vertices)
    assert all(
        np.isfinite(list(p.values())).all() and max(abs(v) for v in p.values()) <= BOUND_M
        for p in vertices
    )
    assert area([(p["north_m"], p["east_m"]) for p in vertices]) > CELL_M**2
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        assert segment_in_polygon(a, b, fence), "Search area leaves read-back fence"
    from mission_planner.gcs.geodesy import segment_distance

    edges = list(zip(vertices, vertices[1:] + vertices[:1]))
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j > i + 1 and (i, j) != (0, len(edges) - 1):
                assert (
                    segment_distance(
                        *[tuple(p[k] for k in ("north_m", "east_m")) + (0.0,) for p in (a, b, c, d)]
                    )
                    > 1e-8
                ), "Search area self-intersects"

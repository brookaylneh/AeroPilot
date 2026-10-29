"""Pure route, fence and spatial-reservation checks for read-back mission state."""

import math

from mission_planner.gcs.geodesy import (
    MAX_ALTITUDE_M,
    MIN_ALTITUDE_M,
    MIN_SEPARATION_M,
    point_in_polygon,
    segment_distance,
    segment_in_polygon,
    to_local,
)
from world.cosys.terrain import COLUMNS, ROWS, WORLD, terrain_height

CLEARANCE_M = 10.0


def fence_polygon(readback):
    return [
        dict(zip(("north_m", "east_m"), to_local(m["x"] / 1e7, m["y"] / 1e7))) for m in readback
    ]


def validate_position(name, state, readback):
    assert point_in_polygon(state, fence_polygon(readback)), (
        f"{name}: current position is outside the search fence; launch position conflicts with required fence"
    )


def validate_ground_column(point, landmarks):
    assert landmarks is not None, "Landmark catalogue required for ground-column clearance"
    for landmark in landmarks:
        if landmark["heightM"] > 0:
            assert not point_in_polygon(
                point,
                [
                    {"north_m": p["x"] / 100, "east_m": p["y"] / 100}
                    for p in landmark["geometry"]["points"]
                ],
            ), f"Ground column intersects raised landmark {landmark['name']}"


def validate_route(route, readback, home_z, landmarks):
    assert route
    polygon = fence_polygon(readback)
    for p in route:
        assert all(math.isfinite(p[k]) for k in ("north_m", "east_m", "altitude_above_home_m"))
        assert point_in_polygon(p, polygon), "Waypoint is outside the search fence"
        assert MIN_ALTITUDE_M <= p["altitude_above_home_m"] <= MAX_ALTITUDE_M, (
            "Altitude must be 10–100 m above home"
        )

    for a, b in zip(route, route[1:]):
        assert segment_in_polygon(a, b, polygon), "Route segment exits the launch corridor fence"
    assert type(home_z) in (int, float) and math.isfinite(home_z), "Invalid home UE-up metres"
    assert landmarks is not None, "Landmark data unavailable; obstacle clearance cannot be checked"
    assert isinstance(landmarks, list)
    for landmark in landmarks:
        if landmark["geometry"]["type"] != "polygon":
            continue
        footprint = [
            {"north_m": p["x"] / 100, "east_m": p["y"] / 100}
            for p in landmark["geometry"]["points"]
        ]
        for a, b in zip(route, route[1:] or route):
            for low, high in footprint_intervals(a, b, footprint):
                clearance = minimum_clearance(a, b, low, high, home_z, landmark["heightM"])
                assert clearance >= CLEARANCE_M, (
                    f"Route clearance over {landmark['name']} is {clearance:.3f} m; "
                    f"requires {CLEARANCE_M:g} m above terrain plus {landmark['heightM']:g} m height"
                )


def footprint_intervals(a, b, polygon):
    start = a["north_m"], a["east_m"]
    delta = b["north_m"] - start[0], b["east_m"] - start[1]
    length2 = sum(v * v for v in delta)
    if length2 == 0:
        return [(0.0, 1.0)] if point_in_polygon(a, polygon) else []
    cuts = {0.0, 1.0}
    for c, d in zip(polygon, [*polygon[1:], polygon[0]]):
        edge = d["north_m"] - c["north_m"], d["east_m"] - c["east_m"]
        offset = c["north_m"] - start[0], c["east_m"] - start[1]
        denominator = delta[0] * edge[1] - delta[1] * edge[0]
        if denominator != 0:
            t = (offset[0] * edge[1] - offset[1] * edge[0]) / denominator
            u = (offset[0] * delta[1] - offset[1] * delta[0]) / denominator
            if 0 <= t <= 1 and 0 <= u <= 1:
                cuts.add(t)
        elif offset[0] * delta[1] - offset[1] * delta[0] == 0:
            for p in (c, d):
                t = (
                    sum(
                        (p[key] - start[k]) * delta[k]
                        for k, key in enumerate(("north_m", "east_m"))
                    )
                    / length2
                )
                if 0 <= t <= 1:
                    cuts.add(t)
    cuts = sorted(cuts)

    def inside(t):
        return point_in_polygon(
            {"north_m": start[0] + delta[0] * t, "east_m": start[1] + delta[1] * t}, polygon
        )

    return [(t, t) for t in cuts if inside(t)] + [
        (low, high) for low, high in zip(cuts, cuts[1:]) if inside((low + high) / 2)
    ]


def minimum_clearance(a, b, low, high, home_z, height):
    assert all(type(count) is int and count > 1 for count in (ROWS, COLUMNS))
    bounds = WORLD["searchBoundsCm"]
    cuts = {low, high}
    for key, axis, count in (("north_m", "x", COLUMNS), ("east_m", "y", ROWS)):
        delta = b[key] - a[key]
        if delta == 0:
            continue
        origin = bounds["min"][axis] / 100
        step = (bounds["max"][axis] - bounds["min"][axis]) / 100 / (count - 1)
        first, last = sorted((a[key] + delta * low, a[key] + delta * high))
        for index in range(
            math.floor((first - origin) / step) + 1, math.ceil((last - origin) / step)
        ):
            t = (origin + index * step - a[key]) / delta
            if low < t < high:
                cuts.add(t)

    def clearance(t):
        north, east, altitude = (
            a[key] + (b[key] - a[key]) * t for key in ("north_m", "east_m", "altitude_above_home_m")
        )
        value = home_z + altitude - terrain_height(north, east) - height
        assert math.isfinite(value)
        return value

    cuts = sorted(cuts)
    result = min(clearance(t) for t in cuts)
    # Bilinear terrain restricted to a straight leg is quadratic inside each heightfield cell.
    for left, right in zip(cuts, cuts[1:]):
        c0, cm, c1 = (clearance(t) for t in (left, (left + right) / 2, right))
        quadratic = 2 * (c0 + c1 - 2 * cm)
        linear = c1 - c0 - quadratic
        if quadratic > 0:
            u = -linear / (2 * quadratic)
            if 0 < u < 1:
                result = min(result, clearance(left + u * (right - left)))
    return result


def validate_separation(name, route, reservations):
    # Routes reserve their entire spatial corridor, independent of timing.
    def segments(points):
        values = [
            tuple(p[k] for k in ("north_m", "east_m", "altitude_above_home_m")) for p in points
        ]
        return list(zip(values, values[1:])) if len(values) > 1 else [(values[0], values[0])]

    for other, reserved in reservations:
        if other != name:
            for a, b in segments(route):
                for c, d in segments(reserved):
                    assert segment_distance(a, b, c, d) >= MIN_SEPARATION_M, (
                        f"Route conflicts with {other}; minimum separation {MIN_SEPARATION_M} m"
                    )

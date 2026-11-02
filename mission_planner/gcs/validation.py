"""Pure route, fence and spatial-reservation checks for read-back mission state."""

import math

from mission_planner.gcs.fences import read_fences, validate_fence_segment
from mission_planner.gcs.geodesy import (
    MAX_ALTITUDE_M,
    MIN_ALTITUDE_M,
    MIN_SEPARATION_M,
    SEPARATION_TOLERANCE_M,
    point_in_polygon,
    segment_distance,
    segment_in_polygon,
)
from mission_planner.gcs.rejection import PlanRejected, separation
from world.cosys.terrain import COLUMNS, ROWS, WORLD, launch_breaks, terrain_height

CLEARANCE_M = 10.0
# Unobserved transit flown before a drone's first observed step is compared by time window;
# observed steps, hovers, holds and launch or landing columns reserve space regardless of time.
SEPARATION_MARGIN_S = 30.0


def timed_apart(window, other):
    if window is None or other is None:
        return False
    return (
        window[1] + SEPARATION_MARGIN_S <= other[0] or other[1] + SEPARATION_MARGIN_S <= window[0]
    )


def fence_polygon(readback):
    return read_fences(readback)["inclusion"]


def validate_position(name, state, readback):
    if not point_in_polygon(state, fence_polygon(readback)):
        raise PlanRejected(
            "fence",
            f"{name}: current position is outside the search fence; "
            "launch position conflicts with required fence",
            drone_id=name,
        )
    validate_fence_segment(state, state, read_fences(readback), name + " position", clearance=0)


def validate_ground_column(point, landmarks):
    assert landmarks is not None, "Landmark catalogue required for ground-column clearance"
    for landmark in landmarks:
        if landmark["heightM"] > 0 and point_in_polygon(
            point,
            [
                {"north_m": p["x"] / 100, "east_m": p["y"] / 100}
                for p in landmark["geometry"]["points"]
            ],
        ):
            raise PlanRejected(
                "obstacle_clearance",
                f"Ground column intersects raised landmark {landmark['name']}",
                detail={"landmark": landmark["id"]},
            )


def validate_route(route, readback, home_z, landmarks):
    assert route
    polygon = fence_polygon(readback)
    for p in route:
        assert all(math.isfinite(p[k]) for k in ("north_m", "east_m", "altitude_above_home_m"))
        if not point_in_polygon(p, polygon):
            raise PlanRejected("fence", "Waypoint is outside the search fence", detail={"point": p})
        if not MIN_ALTITUDE_M <= p["altitude_above_home_m"] <= MAX_ALTITUDE_M:
            raise PlanRejected(
                "altitude",
                f"Altitude must be {MIN_ALTITUDE_M:g}–{MAX_ALTITUDE_M:g} m above home",
                detail={"point": p, "minimum_m": MIN_ALTITUDE_M, "maximum_m": MAX_ALTITUDE_M},
            )

    for a, b in zip(route, route[1:]):
        if not segment_in_polygon(a, b, polygon):
            raise PlanRejected(
                "fence", "Route segment exits the launch corridor fence", detail={"segment": [a, b]}
            )
    fences = read_fences(readback)
    for index, (a, b) in enumerate(zip(route, route[1:] or route), 1):
        validate_fence_segment(a, b, fences, f"Route segment {index}")
    assert type(home_z) in (int, float) and math.isfinite(home_z), "Invalid home UE-up metres"
    assert landmarks is not None, "Landmark data unavailable; obstacle clearance cannot be checked"
    assert isinstance(landmarks, list)
    for a, b in zip(route, route[1:] or route):
        clearance = minimum_clearance(a, b, 0, 1, home_z, 0)
        if clearance < CLEARANCE_M:
            raise PlanRejected(
                "terrain_clearance",
                f"Route clearance above terrain is {clearance:.3f} m; requires {CLEARANCE_M:g} m",
                detail={"segment": [a, b], "clearance_m": clearance, "required_m": CLEARANCE_M},
            )
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
                if clearance < CLEARANCE_M:
                    raise PlanRejected(
                        "obstacle_clearance",
                        f"Route clearance over {landmark['name']} is {clearance:.3f} m; "
                        f"requires {CLEARANCE_M:g} m above terrain plus "
                        f"{landmark['heightM']:g} m height",
                        detail={
                            "segment": [a, b],
                            "landmark": landmark["id"],
                            "clearance_m": clearance,
                            "required_m": CLEARANCE_M,
                        },
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
    cuts = {low, high, *(t for t in launch_breaks(a, b) if low < t < high)}
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


def separation_conflict(name, route, reservations, home_altitudes_msl_m, windows=None):
    def segments(points, home_offset_m):
        assert points, "Separation requires a nonempty route"
        values = [
            (p["north_m"], p["east_m"], p["altitude_above_home_m"] + home_offset_m) for p in points
        ]
        assert all(math.isfinite(v) for point in values for v in point), (
            "Separation coordinates must be finite metres"
        )
        return list(zip(values, values[1:])) if len(values) > 1 else [(values[0], values[0])]

    home = home_altitudes_msl_m[name]
    assert type(home) in (int, float) and math.isfinite(home), (
        f"{name}: home altitude must be finite MSL metres"
    )
    route_segments = segments(route, 0)
    mine = windows[name] if windows is not None and name in windows else None
    assert mine is None or len(mine) == len(route_segments), (
        "Separation windows must match route segments"
    )
    for other, reserved in reservations:
        if other != name:
            other_home = home_altitudes_msl_m[other]
            assert type(other_home) in (int, float) and math.isfinite(other_home), (
                f"{other}: home altitude must be finite MSL metres"
            )
            reserved_segments = segments(reserved, other_home - home)
            theirs = windows[other] if windows is not None and other in windows else None
            assert theirs is None or len(theirs) == len(reserved_segments), (
                "Separation windows must match reserved segments"
            )
            pairs = [
                (segment_distance(a, b, c, d), i, j)
                for i, (a, b) in enumerate(route_segments)
                for j, (c, d) in enumerate(reserved_segments)
                if not timed_apart(
                    mine[i] if mine is not None else None,
                    theirs[j] if theirs is not None else None,
                )
            ]
            if not pairs:
                continue
            distance, index, other_index = min(pairs)
            assert math.isfinite(distance), "Separation distance must be finite metres"
            if distance < MIN_SEPARATION_M - SEPARATION_TOLERANCE_M:
                keys = ("north_m", "east_m", "altitude_above_home_m")
                window = mine[index] if mine is not None else None
                other_window = theirs[other_index] if theirs is not None else None
                return {
                    "drone_id": name,
                    "other_drone_id": other,
                    "distance_m": distance,
                    "minimum_m": MIN_SEPARATION_M,
                    "segment_index": index,
                    "other_segment_index": other_index,
                    "segment": [dict(zip(keys, p)) for p in route_segments[index]],
                    "other_segment": [
                        dict(zip(keys, p)) for p in segments(reserved, 0)[other_index]
                    ],
                    "step_index": None,
                    "other_step_index": None,
                    "step_kind": None,
                    "other_step_kind": None,
                    "timing": {
                        "applied": window is not None and other_window is not None,
                        "windows": [window, other_window],
                    },
                }
    return None


def validate_separation(name, route, reservations, home_altitudes_msl_m, windows=None):
    conflict = separation_conflict(name, route, reservations, home_altitudes_msl_m, windows)
    if conflict is not None:
        raise separation(conflict)

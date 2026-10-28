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


def fence_polygon(readback):
    return [
        dict(zip(("north_m", "east_m"), to_local(m["x"] / 1e7, m["y"] / 1e7))) for m in readback
    ]


def validate_position(name, state, readback):
    assert point_in_polygon(state, fence_polygon(readback)), (
        f"{name}: current position is outside the search fence; launch position conflicts with required fence"
    )


def validate_route(route, readback):
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

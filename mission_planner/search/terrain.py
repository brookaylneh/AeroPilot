"""Relative-home waypoints and measured heightfield clearance along each segment."""

import math

import numpy as np

from mission_planner.gcs.geodesy import MAX_ALTITUDE_M, MIN_ALTITUDE_M
from mission_planner.search.detection import AGL_M
from world.cosys.terrain import terrain_height

WAYPOINT_SPACING_M = 12.0
# Quarter metres survive MAVLink float32 and ArduPilot integer-centimetre storage exactly.
ALTITUDE_STEP_M = 0.25


def waypoint(north, east, home_z):
    return {
        "north_m": float(north),
        "east_m": float(east),
        "altitude_above_home_m": round(
            (terrain_height(north, east) + AGL_M - home_z) / ALTITUDE_STEP_M
        )
        * ALTITUDE_STEP_M,
    }


def terrain_leg(a, b, home_z):
    length = math.hypot(b["north_m"] - a["north_m"], b["east_m"] - a["east_m"])
    assert length > 0
    result = [
        waypoint(
            a["north_m"] + (b["north_m"] - a["north_m"]) * t,
            a["east_m"] + (b["east_m"] - a["east_m"]) * t,
            home_z,
        )
        for t in np.linspace(0, 1, math.ceil(length / WAYPOINT_SPACING_M) + 1)
    ]
    maximum = 0.0
    for start, end in zip(result, result[1:]):
        distance = math.hypot(end["north_m"] - start["north_m"], end["east_m"] - start["east_m"])
        for t in np.linspace(0, 1, math.ceil(distance) + 1):
            n = start["north_m"] + (end["north_m"] - start["north_m"]) * t
            e = start["east_m"] + (end["east_m"] - start["east_m"]) * t
            h = (
                start["altitude_above_home_m"]
                + (end["altitude_above_home_m"] - start["altitude_above_home_m"]) * t
            )
            assert MIN_ALTITUDE_M <= h <= MAX_ALTITUDE_M
            maximum = max(maximum, abs(h + home_z - terrain_height(n, e) - AGL_M))
    assert maximum <= 2, "Terrain-following leg exceeds calibration flight tolerance"
    return result, maximum

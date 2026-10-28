"""Relative-home waypoints and measured heightfield clearance along each segment."""

import math

import numpy as np

from mission_planner.gcs.geodesy import MAX_ALTITUDE_M, MIN_ALTITUDE_M
from mission_planner.search.detection import AGL_M
from world.cosys.terrain import terrain_height

# Quarter metres survive MAVLink float32 and ArduPilot integer-centimetre storage exactly.
ALTITUDE_STEP_M = 0.25
PLANNED_AGL_TOLERANCE_M = 1.0


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
    samples = [
        waypoint(
            a["north_m"] + (b["north_m"] - a["north_m"]) * t,
            a["east_m"] + (b["east_m"] - a["east_m"]) * t,
            home_z,
        )
        for t in np.linspace(0, 1, math.ceil(length) + 1)
    ]
    target = np.array([terrain_height(p["north_m"], p["east_m"]) + AGL_M - home_z for p in samples])
    altitude = np.array([p["altitude_above_home_m"] for p in samples])
    assert ((altitude >= MIN_ALTITUDE_M) & (altitude <= MAX_ALTITUDE_M)).all()
    count = len(samples)
    direct_error = float(np.abs(np.linspace(altitude[0], altitude[-1], count) - target).max())
    if direct_error <= PLANNED_AGL_TOLERANCE_M:
        return [samples[0], samples[-1]], direct_error
    links = np.full(count, count, dtype=int)
    successor = np.full(count, -1, dtype=int)
    links[-1] = 0
    for i in range(count - 2, -1, -1):
        offsets = np.arange(1, count - i)
        slopes = (altitude[i + 1 :] - altitude[i]) / offsets
        lower = np.maximum.accumulate(
            (target[i + 1 :] - PLANNED_AGL_TOLERANCE_M - altitude[i]) / offsets
        )
        upper = np.minimum.accumulate(
            (target[i + 1 :] + PLANNED_AGL_TOLERANCE_M - altitude[i]) / offsets
        )
        eligible = np.flatnonzero((slopes >= lower) & (slopes <= upper)) + i + 1
        assert len(eligible), "No terrain segment satisfies the calibration flight tolerance"
        best = int(eligible[np.flatnonzero(links[eligible] == links[eligible].min())[-1]])
        links[i], successor[i] = links[best] + 1, best
    indices = [0]
    while indices[-1] != count - 1:
        indices.append(int(successor[indices[-1]]))
    assert len(indices) == links[0] + 1
    interpolated = np.interp(np.arange(count), indices, altitude[indices])
    maximum = float(np.abs(interpolated - target).max())
    assert maximum <= PLANNED_AGL_TOLERANCE_M, "Terrain leg consumes the tracking margin"
    return [samples[i] for i in indices], maximum

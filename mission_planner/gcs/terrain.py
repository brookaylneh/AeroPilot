"""Relative-home waypoints and measured heightfield clearance along each segment."""

import math
from collections import deque

import numpy as np

from mission_planner.gcs.geodesy import MAX_ALTITUDE_M, MIN_ALTITUDE_M
from mission_planner.gcs.rejection import PlanRejected
from world.cosys.terrain import terrain_height

# Quarter metres survive MAVLink float32 and ArduPilot integer-centimetre storage exactly.
ALTITUDE_STEP_M = 0.25
PLANNED_AGL_TOLERANCE_M = 1.0


def waypoint(north, east, home_z, height_m):
    return {
        "north_m": float(north),
        "east_m": float(east),
        "altitude_above_home_m": round(
            (terrain_height(north, east) + height_m - home_z) / ALTITUDE_STEP_M
        )
        * ALTITUDE_STEP_M,
    }


def terrain_leg(a, b, home_z, height_m):
    length = math.hypot(b["north_m"] - a["north_m"], b["east_m"] - a["east_m"])
    assert length > 0
    t = np.linspace(0, 1, math.ceil(length) + 1)
    north = a["north_m"] + (b["north_m"] - a["north_m"]) * t
    east = a["east_m"] + (b["east_m"] - a["east_m"]) * t
    target = np.array([terrain_height(n, e) + height_m - home_z for n, e in zip(north, east)])
    altitude = np.rint(target / ALTITUDE_STEP_M) * ALTITUDE_STEP_M
    if not ((altitude >= MIN_ALTITUDE_M) & (altitude <= MAX_ALTITUDE_M)).all():
        raise PlanRejected(
            "altitude",
            f"Terrain-following altitude spans {altitude.min():.3f}–{altitude.max():.3f} m; "
            f"must remain {MIN_ALTITUDE_M:g}–{MAX_ALTITUDE_M:g} m above home",
            detail={
                "segment": [a, b],
                "minimum_m": float(altitude.min()),
                "maximum_m": float(altitude.max()),
            },
        )
    count = len(target)
    direct_error = float(np.abs(np.linspace(altitude[0], altitude[-1], count) - target).max())
    if direct_error <= PLANNED_AGL_TOLERANCE_M:
        indices = [0, count - 1]
        maximum = direct_error
    else:
        indices = minimum_links(altitude, target)
        interpolated = np.interp(np.arange(count), indices, altitude[indices])
        maximum = float(np.abs(interpolated - target).max())
    assert maximum <= PLANNED_AGL_TOLERANCE_M, "Terrain leg consumes the tracking margin"
    return [
        {
            "north_m": float(north[i]),
            "east_m": float(east[i]),
            "altitude_above_home_m": float(altitude[i]),
        }
        for i in indices
    ], maximum


def minimum_links(altitude, target):
    assert altitude.shape == target.shape and altitude.ndim == 1 and len(target) >= 2
    count = len(target)
    predecessor = np.full(count, -1, dtype=int)
    predecessor[0] = 0
    queue = deque([0])
    while predecessor[-1] < 0:
        assert queue, "No terrain segment satisfies the terrain-following tolerance"
        i = queue.popleft()
        offsets = np.arange(1, count - i)
        slopes = (altitude[i + 1 :] - altitude[i]) / offsets
        lower = np.maximum.accumulate(
            (target[i + 1 :] - PLANNED_AGL_TOLERANCE_M - altitude[i]) / offsets
        )
        upper = np.minimum.accumulate(
            (target[i + 1 :] + PLANNED_AGL_TOLERANCE_M - altitude[i]) / offsets
        )
        eligible = np.flatnonzero((slopes >= lower) & (slopes <= upper)) + i + 1
        unseen = eligible[predecessor[eligible] < 0][::-1]
        predecessor[unseen] = i
        # FIFO levels minimize links; descending successors preserve the farthest-first tie-break.
        queue.extend(unseen)
    indices = [count - 1]
    while indices[-1] != 0:
        indices.append(int(predecessor[indices[-1]]))
    return indices[::-1]

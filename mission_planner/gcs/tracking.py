"""Image-derived ground observations and bounded terrain-aware following targets."""

import math

import numpy as np

from mission_planner.gcs.geodesy import ORIGIN, point_in_polygon, segment_in_polygon
from mission_planner.gcs.terrain import waypoint
from mission_planner.gcs.validation import minimum_clearance
from world.cosys.capture import rotation
from world.cosys.terrain import domain, terrain_height


def ground_observation(metadata, box):
    assert len(box) == 4 and all(math.isfinite(v) for v in box)
    width, height = metadata["width"], metadata["height"]
    assert 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height, "Invalid tracked box"
    fov = metadata["horizontal_fov_deg"]
    assert 1 < fov < 179
    focal = width / (2 * math.tan(math.radians(fov) / 2))
    ray = rotation(metadata["camera_pose_local_ned"]["orientation"]).apply(
        [1, ((box[0] + box[2]) / 2 - width / 2) / focal, (box[3] - height / 2) / focal]
    ) * [1, 1, -1]
    if ray[2] >= -0.05:
        return {
            "ground": None,
            "location_error": "Candidate ground-contact ray is too close to the horizon",
        }
    camera = metadata["camera_position_world_m"]
    low, high, minimum = domain()
    if not all(low[i] <= camera[key] <= high[i] for i, key in enumerate("xy")):
        return {"ground": None, "location_error": "Tracking camera is outside the terrain domain"}
    distance = (camera["z"] - minimum + 1) / -ray[2]
    for i, key in enumerate("xy"):
        if ray[i] != 0:
            edge = high[i] if ray[i] > 0 else low[i]
            distance = min(distance, (edge - camera[key]) / ray[i])
    if distance <= 0:
        return {"ground": None, "location_error": "Candidate ray leaves the terrain domain"}
    start = {"north_m": camera["x"], "east_m": camera["y"], "altitude_above_home_m": camera["z"]}
    end = {key: start[key] + distance * ray[i] for i, key in enumerate(start)}
    assert minimum_clearance(start, start, 0, 1, 0, 0) > 0, "Camera must be above terrain"
    if minimum_clearance(start, end, 0, 1, 0, 0) > 0:
        return {
            "ground": None,
            "location_error": "Candidate ray has no ground intersection inside the terrain",
        }
    before, after = 0.0, 1.0
    # The minimum on a growing ray prefix locates the first intersection even across a ridge.
    for _ in range(40):
        middle = (before + after) / 2
        if minimum_clearance(start, end, 0, middle, 0, 0) > 0:
            before = middle
        else:
            after = middle
        if (after - before) * distance * np.linalg.norm(ray) < 0.005:
            break
    north, east = (
        float(start[key] + after * (end[key] - start[key])) for key in ("north_m", "east_m")
    )
    return {
        "ground": {"north_m": north, "east_m": east, "ground_m": terrain_height(north, east)},
        "location_error": None,
    }


def follow_target(state, target, perimeter, stand_off_m, height_m):
    assert all(math.isfinite(v) and v > 0 for v in (stand_off_m, height_m))
    assert point_in_polygon(target, perimeter), "Candidate left the authorized search perimeter"
    north, east = (target[key] - state[key] for key in ("north_m", "east_m"))
    distance = math.hypot(north, east)
    assert distance > 0.1, "Candidate bearing is unresolved directly below the aircraft"
    desired = {
        "north_m": target["north_m"] - north / distance * stand_off_m,
        "east_m": target["east_m"] - east / distance * stand_off_m,
    }
    assert point_in_polygon(desired, perimeter), "Required stand-off leaves the search perimeter"
    assert segment_in_polygon(state, desired, perimeter), (
        "Following path leaves the search perimeter"
    )
    home_z = state["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"]
    point = waypoint(desired["north_m"], desired["east_m"], home_z, height_m)
    return point, math.degrees(math.atan2(east, north)) % 360

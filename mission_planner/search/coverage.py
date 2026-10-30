"""Capture-derived geometry; only explicit processed negatives update belief."""

import numpy as np

from mission_planner.agents.perception.geometry import Camera
from mission_planner.search.detection import AGL_M, positional_probability
from mission_planner.search.footprint import footprint
from world.cosys.terrain import terrain_height


def capture_geometry(frame):
    camera = Camera(frame)
    down = camera.rotation[:, 0]
    assert down[2] > 0.99, "Coverage requires a nadir camera"
    corners = footprint(camera)
    return camera, [{"north_m": p["x"], "east_m": p["y"]} for p in corners]


def capture_pod(frame, grid, model, visible):
    assert visible is not None and visible.shape == grid["areas"].shape and visible.dtype == bool
    camera, footprint = capture_geometry(frame)
    if not footprint:
        return np.zeros(len(grid["centers"])), footprint
    agl = camera.world["z"] - terrain_height(camera.world["x"], camera.world["y"])
    assert abs(agl - AGL_M) <= 2, "Capture outside nominal calibration flight band"
    points = grid["centers"]
    pod = np.zeros(len(points))
    north = [p["north_m"] for p in footprint]
    east = [p["east_m"] for p in footprint]
    ids = np.flatnonzero(
        visible
        & (points[:, 0] >= min(north))
        & (points[:, 0] <= max(north))
        & (points[:, 1] >= min(east))
        & (points[:, 1] <= max(east))
    )
    lateral = camera.rotation[:, 1] * np.array([1, 1, -1])
    along = camera.rotation[:, 2] * np.array([1, 1, -1])
    for i in ids:
        n, e = points[i]
        point = {"x": float(n), "y": float(e), "z": terrain_height(n, e)}
        pixel = camera.project(point)
        if 0 <= pixel[0] < camera.width and 0 <= pixel[1] < camera.height:
            delta = np.array([point[k] - camera.world[k] for k in "xyz"])
            pod[i] = float(positional_probability(delta @ lateral, delta @ along, model))
    return pod, footprint

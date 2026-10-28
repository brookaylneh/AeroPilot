"""Capture-derived geometry; only explicit processed negatives update belief."""

import numpy as np

from mission_planner.agents.perception.geometry import Camera
from mission_planner.search.detection import AGL_M, positional_probability
from world.cosys.terrain import terrain_height


def capture_pod(frame, grid, model):
    camera = Camera(frame)
    agl = camera.world["z"] - terrain_height(camera.world["x"], camera.world["y"])
    assert abs(agl - AGL_M) <= 2, "Capture outside nominal calibration flight band"
    down = camera.rotation[:, 0]
    assert down[2] > 0.99, "Detection model requires a nadir camera"
    corners = [
        camera.ground(p)
        for p in (
            (0, 0),
            (camera.width - 1, 0),
            (camera.width - 1, camera.height - 1),
            (0, camera.height - 1),
        )
    ]
    points = grid["centers"]
    pod = np.zeros(len(points))
    north = [p["x"] for p in corners]
    east = [p["y"] for p in corners]
    ids = np.flatnonzero(
        (points[:, 0] >= min(north))
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
    return pod, [{"north_m": p["x"], "east_m": p["y"]} for p in corners]

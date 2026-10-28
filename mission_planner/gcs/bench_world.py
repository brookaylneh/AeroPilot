"""Measure full-world search planning and one real-frame negative update, without flight."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import time
from pathlib import Path

import numpy as np

from mission_planner.environment import ROOT, load_env
from mission_planner.gcs.geodesy import fence_vertices, homes, to_geodetic
from mission_planner.search.belief import negative_update
from mission_planner.search.coverage import capture_pod
from mission_planner.search.detection import reduce_calibration
from mission_planner.search.grid import make_grid
from mission_planner.search.planner import encode, plan
from mission_planner.search.prior import initialize
from world.cosys.terrain import WORLD


def run(output):
    load_env()
    assert not output.exists(), f"Measurement already exists: {output}"
    assert os.environ["OPENBLAS_NUM_THREADS"] == os.environ["OMP_NUM_THREADS"] == "1"
    bounds = WORLD["searchBoundsCm"]
    vertices = [
        {"north_m": bounds[n]["x"] / 100, "east_m": bounds[e]["y"] / 100}
        for n, e in (("min", "min"), ("max", "min"), ("max", "max"), ("min", "max"))
    ]
    t = time.perf_counter()
    grid = make_grid(fence_vertices())
    grid_seconds = time.perf_counter() - t
    t = time.perf_counter()
    model = reduce_calibration(ROOT / ".local/data/calibration")
    calibration_seconds = time.perf_counter() - t
    t = time.perf_counter()
    probability, retained, prior = initialize(
        grid, {"north_m": -650, "east_m": 0}, "child_td_1_16", "temperate"
    )
    prior_seconds = time.perf_counter() - t
    states = {
        name: {
            "id": name,
            "home": home,
            "north_m": home["north_m"],
            "east_m": home["east_m"],
            "altitude_above_home_m": 0.0,
            "armed": False,
            "landed_state": 1,
            "link_lost": False,
            "ekf_healthy": True,
        }
        for name, home in homes().items()
    }
    fence = [
        dict(zip(("x", "y"), [round(v * 1e7) for v in to_geodetic(p["north_m"], p["east_m"])]))
        for p in fence_vertices()
    ]
    readbacks = {name: fence for name in states}
    t = time.perf_counter()
    result = plan(grid, probability, vertices, states, readbacks, model, {"benchmark": "world"})
    planning_seconds = time.perf_counter() - t
    frame_path = ROOT / ".local/data/calibration/adult-kelvin/frame-00-+00-Drone1.json"
    frame = json.loads(frame_path.read_text())
    previous = np.zeros_like(probability)
    t = time.perf_counter()
    pod, footprint = capture_pod(frame, grid, model)
    projection_seconds = time.perf_counter() - t
    t = time.perf_counter()
    updated, cumulative = negative_update(probability, previous, pod)
    update_seconds = time.perf_counter() - t
    report = {
        "sourceRoot": str(ROOT),
        "world": WORLD,
        "gridShapeNorthEast": grid["shape"],
        "gridCells": len(grid["centers"]),
        "gridAreaM2": float(grid["areas"].sum()),
        "fullWorldVertices": vertices,
        "coldRun": True,
        "timingSeconds": {
            "grid": grid_seconds,
            "calibrationReduction": calibration_seconds,
            "prior": prior_seconds,
            "planning": planning_seconds,
            "frameProjection": projection_seconds,
            "beliefUpdate": update_seconds,
            "captureAndBeliefUpdate": projection_seconds + update_seconds,
        },
        "priorRetainedMass": retained,
        "prior": prior,
        "planSha256": hashlib.sha256(encode(result).encode()).hexdigest(),
        "plan": result,
        "framePath": str(frame_path.relative_to(ROOT)),
        "frameSha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
        "footprint": footprint,
        "nonzeroPodCells": int(np.count_nonzero(pod)),
        "posteriorSum": float(updated.sum()),
        "maximumCumulativePod": float(cumulative.max()),
        "dependencies": {
            name: importlib.metadata.version(name) for name in ("numpy", "scipy", "Pillow")
        },
        "python": platform.python_version(),
        "cpuAffinity": sorted(os.sched_getaffinity(0)),
        "blasThreads": int(os.environ["OPENBLAS_NUM_THREADS"]),
        "sourceSha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ("mission_planner/search", "world/cosys")
            for path in sorted((ROOT / folder).glob("*.py"))
            if not path.name.startswith("test_")
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("gridShapeNorthEast", "gridCells", "timingSeconds", "posteriorSum")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)

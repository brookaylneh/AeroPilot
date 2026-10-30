"""Offline positional calibration and maximum-look capture-spacing comparison."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from mission_planner.agents.perception.reduce_detection import positional_model
from mission_planner.environment import ROOT
from mission_planner.gcs.geodesy import to_local
from mission_planner.search.detection import track_probability
from mission_planner.search.grid import inside, make_grid
from mission_planner.search.prior import initialize


def plan_expectation(state, grid, prior, model, spacing, phases):
    mask = inside(grid["centers"], state["vertices"])
    points = grid["centers"][mask]
    maximum = np.zeros(len(points))
    for leg in state["plan"]["timeline"]:
        start = np.array([leg["start"][key] for key in ("north_m", "east_m")])
        end = np.array([leg["end"][key] for key in ("north_m", "east_m")])
        direction = end - start
        length = float(np.linalg.norm(direction))
        unit = direction / length
        delta = points - start
        along = delta @ unit
        lateral = delta[:, 0] * unit[1] - delta[:, 1] * unit[0]
        maximum = np.maximum(
            maximum, track_probability(lateral, along, length, model, spacing, phases)
        )
    return float(prior[mask] @ maximum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    calibration = json.loads((ROOT / "mission_planner/measurements/commander.json").read_text())
    model = calibration["observation_model"]
    model["position"] = positional_model(
        ROOT / ".local/data/calibration",
        calibration["calibrated_configuration"]["glimmer_threshold"],
    )
    state_bytes = args.search_state.read_bytes()
    state = json.loads(state_bytes)
    grid = make_grid(
        [
            dict(zip(("north_m", "east_m"), to_local(row["x"] / 1e7, row["y"] / 1e7)))
            for row in state["plan"]["inputs"]["readbacks"]["Drone1"]
        ]
    )
    prior, _, _ = initialize(
        grid, state["point"], state["profile"]["category"]["value"], state["domain"]
    )
    lateral = np.linspace(0, model["width_m"] / 2, 401)
    rows = []
    for spacing in (12.0, 16.0, 20.0, model["length_m"], 24.0):
        pod = track_probability(lateral, np.full_like(lateral, 100), 200, model, spacing, 1024)
        pos = plan_expectation(state, grid, prior, model, spacing, 128)
        refined = plan_expectation(state, grid, prior, model, spacing, 512)
        rows.append(
            {
                "capture_spacing_m": spacing,
                "required_four_drone_fps_at_5mps": 20 / spacing,
                "per_drone_seconds": spacing / 5,
                "mean_maximum_look_sweep_width_m": float(2 * np.trapz(pod, lateral)),
                "accepted_routes_expected_pos_lower_bound": refined,
                "128_vs_512_phase_absolute_pos_difference": abs(pos - refined),
            }
        )
    reference = rows[0]
    for row in rows:
        row["pos_change_from_12m"] = (
            row["accepted_routes_expected_pos_lower_bound"]
            / reference["accepted_routes_expected_pos_lower_bound"]
            - 1
        )
    result = {
        "search_state": str(args.search_state),
        "search_state_sha256": hashlib.sha256(state_bytes).hexdigest(),
        "accepted_plan_id": state["plan"]["plan_id"],
        "accepted_flat_along_model_pos": state["plan"]["pos"],
        "spacing_comparison": rows,
        "combination": "Average over uniform capture phase within each leg of maximum single-look POD; maximum of leg averages",
        "expectation_limitation": "Maximum of phase averages is a conservative lower bound on the expectation of maximum across legs; no independent-look credit",
        "support_limitation": "Ground footprint clips partial edge support conservatively; between-knot and phase behavior is interpolated, not measured",
        "selection": "16 m operational tradeoff; no unique optimum or lossless larger spacing is identified by the sparse calibration",
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__),
                ROOT / "mission_planner/agents/perception/reduce_detection.py",
                ROOT / "mission_planner/search/detection.py",
                ROOT / "mission_planner/search/prior.py",
            )
        },
    }
    for name, value in (("position-model", model), ("spacing", result)):
        (args.output / (name + ".json")).write_text(
            json.dumps(value, indent=2, allow_nan=False) + "\n"
        )
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

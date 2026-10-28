"""Sub-cell integration and error propagation on recorded camera metadata."""

import copy
import json
import math
import unittest

import numpy as np

from mission_planner.agents.perception.geometry import Camera
from mission_planner.agents.perception.localization import (
    ELLIPSE_95,
    ellipse_weights,
    gaussian_cells,
    localize,
)
from mission_planner.environment import ROOT


def grid():
    n, e = np.meshgrid(np.arange(-12, 12, 4), np.arange(-12, 12, 4), indexing="ij")
    low = np.column_stack([n.ravel(), e.ravel()]).astype(float)
    return {"low": low, "high": low + 4, "centers": low + 2, "areas": np.full(len(low), 16.0)}


class Localization(unittest.TestCase):
    def test_subcell_gaussian_on_a_vertex_keeps_four_quadrants(self):
        cells = grid()
        mass, retained = gaussian_cells([0, 0], np.eye(2) * 0.01, cells)
        self.assertAlmostEqual(retained, 1)
        self.assertEqual(int((mass > 1e-8).sum()), 4)
        np.testing.assert_allclose(mass[mass > 1e-8], 0.25, atol=1e-10)
        self.assertAlmostEqual(float(mass.sum()), 1)

    def test_world_edge_retains_and_reports_truncated_mass(self):
        mass, retained = gaussian_cells([-12, 0], np.eye(2) * 0.01, grid())
        self.assertAlmostEqual(retained, 0.5)
        self.assertAlmostEqual(float(mass.sum()), 1)

    def test_ellipse_fraction_integrates_its_actual_area(self):
        cells = grid()
        matrix = np.array([[0.2, 0.1], [0.1, 0.3]])
        weights = ellipse_weights(
            {"ground": {"north_m": 0, "east_m": 0}, "covariance_m2": matrix}, cells
        )
        self.assertAlmostEqual(
            float(weights @ cells["areas"]),
            math.pi * ELLIPSE_95 * math.sqrt(np.linalg.det(matrix)),
            places=6,
        )
        self.assertLess(weights.max(), 1)

    def test_bottom_contact_and_pose_error_on_recorded_frame(self):
        frame = json.loads(
            (ROOT / ".local/data/calibration/adult-kelvin/frame-00-+00-Drone1.json").read_text()
        )
        uncertainty = {
            "pixel_second_moment_1080p2": [
                [135.8616956751134, -138.3224078975225],
                [-138.3224078975225, 212.2614978755683],
            ],
            "position_floor_m": 1.4612244709e-8,
            "rotation_floor_deg": 0.02120053547,
        }
        result = localize(frame, [950, 500, 970, 550], uncertainty)
        expected = Camera(frame).ground([1920, 1100])
        np.testing.assert_allclose(
            list(result["ground"].values()), list(expected.values()), atol=1e-8
        )
        changed = copy.deepcopy(frame)
        changed["vehicleStateAfter"]["positionWorldM"]["x"] += 1
        expanded = localize(changed, [950, 500, 970, 550], uncertainty)
        self.assertGreater(np.trace(expanded["covariance_m2"]), np.trace(result["covariance_m2"]))
        self.assertEqual(expanded["pose_bracket"]["position_m"], 1)
        del changed["vehicleStateBefore"]
        with self.assertRaises(KeyError):
            localize(changed, [950, 500, 970, 550], uncertainty)


if __name__ == "__main__":
    unittest.main()

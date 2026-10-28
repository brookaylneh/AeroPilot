"""Minimum waypoint counts and clearance on independently sampled terrain."""

import itertools
import unittest
from unittest.mock import patch

import numpy as np

from mission_planner.search.terrain import terrain_leg


class Terrain(unittest.TestCase):
    def test_minimum_count_against_exhaustive_short_profile(self):
        heights = np.array([0, 0, 1, 4, 5, 3, 0, -1, 0], dtype=float)

        def ground(north, east):
            self.assertEqual(east, 0)
            return float(np.interp(north, np.arange(len(heights)), heights))

        a, b = {"north_m": 0, "east_m": 0}, {"north_m": 8, "east_m": 0}
        with patch("mission_planner.search.terrain.terrain_height", side_effect=ground):
            route, error = terrain_leg(a, b, 0)
        self.assertLessEqual(error, 2)
        for count in range(2, len(route)):
            for middle in itertools.combinations(range(1, 8), count - 2):
                indices = [0, *middle, 8]
                interpolated = np.interp(np.arange(9), indices, heights[indices])
                self.assertGreater(float(np.abs(interpolated - heights).max()), 2)
        self.assertEqual(route[0]["north_m"], 0)
        self.assertEqual(route[-1]["north_m"], 8)

    def test_constant_slope_has_only_endpoints(self):
        with patch("mission_planner.search.terrain.terrain_height", lambda n, e: n / 10):
            route, error = terrain_leg(
                {"north_m": 0, "east_m": 0}, {"north_m": 300, "east_m": 0}, 0
            )
        self.assertEqual(len(route), 2)
        self.assertLess(error, 1e-12)


if __name__ == "__main__":
    unittest.main()

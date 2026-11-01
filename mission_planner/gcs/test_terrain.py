"""Minimum waypoint counts and clearance on independently sampled terrain."""

import itertools
import unittest
from unittest.mock import patch

import numpy as np

from mission_planner.gcs.terrain import (
    ALTITUDE_STEP_M,
    PLANNED_AGL_TOLERANCE_M,
    minimum_links,
    terrain_leg,
)


class Terrain(unittest.TestCase):
    def test_minimum_count_against_exhaustive_short_profile(self):
        heights = np.array([0, 0, 1, 4, 5, 3, 0, -1, 0], dtype=float)

        def ground(north, east):
            self.assertEqual(east, 0)
            return float(np.interp(north, np.arange(len(heights)), heights))

        a, b = {"north_m": 0, "east_m": 0}, {"north_m": 8, "east_m": 0}
        with patch("mission_planner.gcs.terrain.terrain_height", side_effect=ground):
            route, error = terrain_leg(a, b, 0, 30)
        self.assertLessEqual(error, PLANNED_AGL_TOLERANCE_M)
        for count in range(2, len(route)):
            for middle in itertools.combinations(range(1, 8), count - 2):
                indices = [0, *middle, 8]
                interpolated = np.interp(np.arange(9), indices, heights[indices])
                self.assertGreater(
                    float(np.abs(interpolated - heights).max()), PLANNED_AGL_TOLERANCE_M
                )
        self.assertEqual(route[0]["north_m"], 0)
        self.assertEqual(route[-1]["north_m"], 8)

    def test_constant_slope_has_only_endpoints(self):
        with patch("mission_planner.gcs.terrain.terrain_height", lambda n, e: n / 10):
            route, error = terrain_leg(
                {"north_m": 0, "east_m": 0}, {"north_m": 300, "east_m": 0}, 0, 30
            )
        self.assertEqual(len(route), 2)
        self.assertLess(error, 1e-12)

    def test_minimum_links_and_farthest_ties_match_exhaustive_paths(self):
        rng = np.random.default_rng(407)
        profiles = [
            np.array([30, 31, 32, 33, 34, 33, 32, 31, 30], dtype=float),
            np.array([30, 30, 31, 34, 35, 33, 30, 29, 30], dtype=float),
            *[30 + rng.uniform(-3, 3, 9) for _ in range(40)],
        ]
        counts = set()
        for target in profiles:
            altitude = np.rint(target / ALTITUDE_STEP_M) * ALTITUDE_STEP_M
            paths = []
            for count in range(2, len(target) + 1):
                for middle in itertools.combinations(range(1, len(target) - 1), count - 2):
                    indices = (0, *middle, len(target) - 1)
                    interpolated = np.interp(
                        np.arange(len(target)), indices, altitude[list(indices)]
                    )
                    if np.abs(interpolated - target).max() <= PLANNED_AGL_TOLERANCE_M:
                        paths.append(indices)
                if paths:
                    break
            expected = max(paths)
            actual = minimum_links(altitude, target)
            self.assertEqual(tuple(actual), expected)
            counts.add(len(actual))
        self.assertTrue({3, 4, 5}.issubset(counts))

    def test_fractional_leg_length_keeps_samples_at_most_one_metre_apart(self):
        sampled = []

        def ground(north, east):
            sampled.append((north, east))
            return 2 * np.sin(north / 3)

        with patch("mission_planner.gcs.terrain.terrain_height", side_effect=ground):
            route, error = terrain_leg(
                {"north_m": 0, "east_m": 0}, {"north_m": 12.4, "east_m": 9.3}, 0, 30
            )
        self.assertLessEqual(np.linalg.norm(np.diff(sampled, axis=0), axis=1).max(), 1)
        self.assertEqual(route[0]["north_m"], 0)
        self.assertEqual(route[-1]["east_m"], 9.3)
        self.assertLessEqual(error, PLANNED_AGL_TOLERANCE_M)


if __name__ == "__main__":
    unittest.main()

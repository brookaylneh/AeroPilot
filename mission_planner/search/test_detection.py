"""Positional detection, conservative negatives and correlated capture phases."""

import unittest

import numpy as np

from mission_planner.agents.perception.reduce_detection import reduce_calibration
from mission_planner.environment import ROOT
from mission_planner.search.detection import (
    negative_model,
    phase_average,
    positional_probability,
    track_probability,
)


class CaptureSpacing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = reduce_calibration(ROOT / ".local/data/calibration")

    def test_actual_partial_frame_denominators_and_edge_asymmetry(self):
        position = self.model["position"]
        self.assertEqual(np.asarray(position["trials"]).sum(), 490)
        successes = np.asarray(position["qualified_true_successes"])
        self.assertEqual(successes[2].sum(), 9)
        self.assertEqual(successes[3].sum(), 57)
        self.assertEqual(successes[4].sum(), 48)
        self.assertAlmostEqual(float(positional_probability(0, 0, self.model)), 0.8)
        self.assertGreater(
            positional_probability(0, 11, self.model), positional_probability(0, -11, self.model)
        )

    def test_nominal_footprint_clips_partial_views_and_interpolation_is_bounded(self):
        self.assertEqual(float(positional_probability(0, 12, self.model)), 0)
        self.assertEqual(float(positional_probability(22, 0, self.model)), 0)
        side, along = np.meshgrid(np.linspace(-30, 30, 51), np.linspace(-30, 30, 53))
        p = positional_probability(side, along, self.model)
        self.assertTrue(np.isfinite(p).all())
        self.assertTrue(((p >= 0) & (p <= 1)).all())
        np.testing.assert_array_equal(p, positional_probability(-side, along, self.model))

    def test_negative_envelope_never_zeros_prior_for_empirical_ten_of_ten(self):
        lower = negative_model(self.model)
        self.assertEqual(self.model["position"]["p"][3][1], 1)
        self.assertLess(lower["position"]["p"][3][1], 1)
        self.assertGreater(lower["position"]["p"][3][1], 0)
        self.assertEqual(lower["position"]["p"][0][0], 0)
        self.assertTrue((np.asarray(lower["position"]["p"]) <= self.model["position"]["p"]).all())

    def test_triangular_curve_has_analytic_maximum_look_average(self):
        model = {
            "width_m": 20,
            "length_m": 20,
            "position": {
                "along_m": [-10, 0, 10],
                "lateral_m": [0, 10],
                "p": [[0, 0], [0.8, 0.8], [0, 0]],
            },
        }
        for spacing in (4, 8, 10):
            value = track_probability(np.array([0]), np.array([50]), 100, model, spacing)
            self.assertAlmostEqual(float(value[0]), 0.8 * (1 - spacing / 40), places=5)
        start = track_probability(np.array([0]), np.array([0]), 100, model, 10)
        self.assertAlmostEqual(float(start[0]), 0.4, places=12)

    def test_periodic_reduction_matches_all_original_capture_phases(self):
        rng = np.random.default_rng(730)
        for length in (5.0, 30.0, 317.3, 2412.016):
            along = np.r_[np.linspace(0, length, 257), rng.uniform(0, length, 200)]
            side = rng.uniform(-self.model["width_m"] / 2, self.model["width_m"] / 2, len(along))
            for spacing in (12.0, 16.0, 40.0):
                for distance in (along, length - along):
                    with self.subTest(length=length, spacing=spacing):
                        expected = phase_average(side, distance, length, self.model, spacing, 128)
                        actual = track_probability(side, distance, length, self.model, spacing)
                        np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-13)

    def test_extending_same_capture_lattice_does_not_lose_coverage(self):
        along = np.arange(0, 101, dtype=float)
        lateral = np.zeros_like(along)
        before = np.zeros_like(along)
        for length in (1, 5, 10, 16, 32, 50, 100):
            after = track_probability(lateral, along, length, self.model, 16)
            self.assertTrue((after >= before).all())
            before = after
        self.assertTrue((before <= 0.8).all())


if __name__ == "__main__":
    unittest.main()

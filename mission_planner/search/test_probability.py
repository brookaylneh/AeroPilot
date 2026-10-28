"""Real calibration parity, finite priors and correlated-look invariants."""

import json
import subprocess
import unittest

import numpy as np
from scipy.integrate import quad

from mission_planner.agents.perception.geometry import Camera
from mission_planner.environment import ROOT
from mission_planner.gcs.geodesy import fence_vertices
from mission_planner.search.belief import negative_update
from mission_planner.search.coverage import capture_pod
from mission_planner.search.detection import COHORT, reduce_calibration
from mission_planner.search.grid import make_grid
from mission_planner.search.prior import QUANTILES, TABLES, cdf, density, initialize, table


class Probability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = reduce_calibration(ROOT / ".local/data/calibration")
        cls.grid = make_grid(fence_vertices())

    def test_camera_parity_with_previous_model_on_real_metadata(self):
        source = subprocess.check_output(
            ["git", "show", "93e1b07:mission_planner/agents/perception/geometry.py"],
            cwd=ROOT,
            text=True,
        )
        namespace = {}
        exec(compile(source, "previous_camera", "exec"), namespace)
        for name in COHORT:
            path = ROOT / ".local/data/calibration" / name / "frame-00-+00-Drone1.json"
            frame = json.loads(path.read_text())
            old = namespace["Camera"](frame)
            new = Camera(frame)
            for pixel in ((0, 0), (1920, 1080), (3839, 2159)):
                a = old.ground(pixel)
                b = new.ground(pixel)
                np.testing.assert_allclose(list(a.values()), list(b.values()), atol=1e-8, rtol=0)
                np.testing.assert_allclose(old.project(a), new.project(a), atol=1e-8, rtol=0)

    def test_all_sourced_priors_normalize_and_retain_unconditioned_mass(self):
        for category, domain in TABLES:
            p, mass, fit = initialize(self.grid, {"north_m": 0, "east_m": 0}, category, domain)
            self.assertAlmostEqual(float(p.sum()), 1, places=12)
            self.assertTrue(0 < mass < 1)
            np.testing.assert_allclose(fit["fit_residuals"], 0, atol=1e-15, rtol=0)

    def test_radial_cdf_knots_monotonicity_and_continuous_density(self):
        for category, domain in TABLES:
            q, interpolant, _ = table(category, domain)
            np.testing.assert_allclose(cdf(q, category, domain), QUANTILES, atol=1e-15, rtol=0)
            samples = np.unique(np.concatenate([np.linspace(a, b, 101) for a, b in zip(q, q[1:])]))
            self.assertTrue((np.diff(cdf(samples, category, domain)) > 0).all())
            for knot in q[1:]:
                neighboring = density(knot * np.array([1 - 1e-9, 1 + 1e-9]), category, domain)
                self.assertAlmostEqual(float(neighboring[0] / neighboring[1]), 1, places=6)
            for a, b in zip(q, q[1:]):
                self.assertGreater(
                    float(np.ptp(density(np.linspace(a, b, 11), category, domain))), 0
                )
            integrated = sum(
                quad(lambda x: float(density(x, category, domain)) * np.pi, a, b)[0]
                for a, b in zip(q, q[1:])
            )
            scale = 0.05 / float(interpolant(q[-1], 1))
            integrated += quad(
                lambda x: float(density(q[-1] + scale * x, category, domain)) * np.pi * scale,
                0,
                np.inf,
            )[0]
            self.assertAlmostEqual(integrated, 1, places=12)
            self.assertAlmostEqual(float(cdf(q[-1] + scale * 100, category, domain)), 1, places=15)

    def test_real_capture_duplicate_is_not_independent_evidence(self):
        frame = json.loads(
            (ROOT / ".local/data/calibration/adult-kelvin/frame-00-+00-Drone1.json").read_text()
        )
        pod, footprint = capture_pod(frame, self.grid, self.model)
        self.assertEqual(len(footprint), 4)
        self.assertGreater(float(pod.sum()), 0)
        p, _, _ = initialize(self.grid, {"north_m": 0, "east_m": 0}, "hiker", "australian_mixed")
        updated, m = negative_update(p, np.zeros_like(p), pod)
        duplicate, m2 = negative_update(updated, m, pod)
        np.testing.assert_array_equal(updated, duplicate)
        np.testing.assert_array_equal(m, m2)
        self.assertLess(float(updated[pod > 0].sum()), float(p[pod > 0].sum()))

    def test_different_real_captures_follow_maximum_look_rule(self):
        frames = [
            json.loads((ROOT / ".local/data/calibration/adult-kelvin" / name).read_text())
            for name in ("frame-00-+00-Drone1.json", "frame-04-+00-Drone1.json")
        ]
        pods = [capture_pod(f, self.grid, self.model)[0] for f in frames]
        p, _, _ = initialize(self.grid, {"north_m": 0, "east_m": 0}, "hiker", "australian_mixed")
        a, m = negative_update(p, np.zeros_like(p), pods[0])
        a, m = negative_update(a, m, pods[1])
        b, _ = negative_update(p, np.zeros_like(p), np.maximum(*pods))
        np.testing.assert_allclose(a, b, atol=1e-14)

    def test_geometry_has_no_airsim_import(self):
        code = 'import sys; from mission_planner.agents.perception.geometry import Camera; assert "cosysairsim" not in sys.modules; assert "world.cosys.client" not in sys.modules'
        subprocess.run([str(ROOT / ".venv-cosys/bin/python"), "-c", code], cwd=ROOT, check=True)


if __name__ == "__main__":
    unittest.main()

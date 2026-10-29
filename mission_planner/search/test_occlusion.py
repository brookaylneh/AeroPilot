"""Aerial exclusion and operator-confirmed ground footprints preserve existing evidence."""

import copy
import json
import unittest

import numpy as np

from mission_planner.agents.perception.configuration import load_configuration
from mission_planner.agents.perception.geometry import Camera
from mission_planner.environment import ROOT
from mission_planner.search.belief import Evidence
from mission_planner.search.coverage import capture_pod
from mission_planner.search.detections import Detections
from mission_planner.search.occlusion import aerial_visibility, footprint_fraction
from mission_planner.search.test_detections import record


def geometry(n0, n1, e0=0, e1=4):
    return {
        "type": "polygon",
        "points": [
            {"x": n * 100, "y": e * 100, "z": 0}
            for n, e in ((n0, e0), (n1, e0), (n1, e1), (n0, e1))
        ],
    }


def landmark(kind, footprint):
    return {
        "id": kind,
        "name": kind,
        "aliases": [],
        "kind": kind,
        "heightM": 0,
        "geometry": footprint,
    }


class Occlusion(unittest.TestCase):
    def setUp(self):
        low = np.array([[0, 0], [4, 0], [8, 0]], dtype=float)
        self.grid = {"low": low, "high": low + 4, "centers": low + 2, "areas": np.full(3, 16.0)}
        self.prior = np.array([0.5, 0.3, 0.2])
        self.evidence = Evidence(self.prior, self.grid, 0.02)
        self.ledger = Detections()
        self.camera_footprint = [
            {"north_m": n, "east_m": e} for n, e in ((0, 0), (12, 0), (12, 4), (0, 4))
        ]

    def apply(self, frame, records, pod):
        self.ledger.add_frame(frame, records)
        return self.evidence.apply(frame, np.array(pod), self.camera_footprint, self.ledger, True)

    def snapshot(self):
        return (
            [
                getattr(self.evidence, key).copy()
                for key in ("prior", "probability", "maximum_pod", "ground_pod")
            ],
            copy.deepcopy(self.evidence.ground_checks),
        )

    def unchanged(self, before):
        for current, expected in zip(self.snapshot()[0], before[0]):
            np.testing.assert_array_equal(current, expected)
        self.assertEqual(self.evidence.ground_checks, before[1])

    def test_any_positive_area_overlap_protects_whole_structure_and_forest_cells(self):
        structures = [
            landmark("structure", geometry(0.1, 0.2, 0.1, 0.2)),
            landmark("forest", geometry(4.1, 4.2, 0.1, 0.2)),
            landmark("pond", geometry(8, 12)),
        ]
        np.testing.assert_array_equal(
            aerial_visibility(self.grid, structures), [False, False, True]
        )
        fractions = footprint_fraction(self.grid, structures[0]["geometry"])
        self.assertAlmostEqual(fractions[0], 0.01 / 16)
        np.testing.assert_array_equal(fractions[1:], [0, 0])
        self.grid["areas"][2] = 0
        self.assertFalse(aerial_visibility(self.grid, structures).any())

    def test_missing_catalogue_nonpolygon_and_partial_fence_cells_fail(self):
        with self.assertRaisesRegex(AssertionError, "catalogue"):
            aerial_visibility(self.grid, None)
        with self.assertRaisesRegex(AssertionError, "polygon"):
            aerial_visibility(
                self.grid,
                [landmark("forest", {"type": "point", "points": [{"x": 0, "y": 0, "z": 0}]})],
            )
        self.grid["areas"][0] = 8
        with self.assertRaisesRegex(AssertionError, "whole in-world cells"):
            footprint_fraction(self.grid, geometry(0, 2))

    def test_raised_obstacle_does_not_occlude_aerial_evidence(self):
        obstacle = landmark("obstacle", geometry(0, 12))
        obstacle["heightM"] = 2
        visible = aerial_visibility(self.grid, [obstacle])
        np.testing.assert_array_equal(visible, [True, True, True])
        result = self.apply("empty", [], np.array([0.8, 0.6, 0.4]) * visible)
        self.assertEqual(result["kind"], "negative")
        np.testing.assert_array_equal(self.evidence.maximum_pod, [0.8, 0.6, 0.4])

    def test_negative_frame_earns_no_pod_in_protected_cells(self):
        visible = aerial_visibility(
            self.grid, [landmark("structure", geometry(0, 4)), landmark("forest", geometry(4, 8))]
        )
        result = self.apply("empty", [], 0.8 * visible)
        self.assertEqual(result["kind"], "negative")
        np.testing.assert_array_equal(self.evidence.maximum_pod, [0, 0, 0.8])
        self.assertAlmostEqual(
            self.evidence.probability[0] / self.evidence.probability[1],
            self.prior[0] / self.prior[1],
        )
        self.assertLess(self.evidence.probability[2], self.prior[2])

    def test_qualified_protected_detection_is_retained_without_any_factor(self):
        result = self.apply("roof", [record("person", "roof", 2, 2)], [0, 0.8, 0.8])
        self.assertEqual(result["kind"], "no_detection_credit")
        self.assertEqual(result["credited_cluster_ids"], [])
        self.assertFalse(self.evidence.factors)
        self.assertFalse(self.evidence.reserved.any())
        self.assertIn("person", self.ledger.records)
        np.testing.assert_array_equal(self.evidence.maximum_pod, np.zeros(3))
        np.testing.assert_allclose(self.evidence.probability, self.prior)

    def test_mixed_frame_credits_only_the_visible_candidate(self):
        result = self.apply(
            "mixed", [record("roof", "mixed", 2, 2), record("open", "mixed", 10, 2)], [0, 0.8, 0.8]
        )
        self.assertEqual(result["credited_cluster_ids"], ["open"])
        self.assertEqual(set(self.evidence.factors["mixed"]["clusters"]), {"open"})
        self.assertEqual(len(self.ledger.records), 2)
        self.assertFalse(self.evidence.reserved[0])

    def test_full_ground_exclusion_preserves_prior_and_camera_history(self):
        self.evidence.maximum_pod[:] = [0, 0.2, 0.4]
        self.evidence.recompute()
        before = self.evidence.probability.copy()
        effect = self.evidence.ground_negative("task", "cabin", geometry(0, 4))
        self.assertAlmostEqual(effect["removed_mass"], before[0])
        self.assertAlmostEqual(sum(effect.values()), 1)
        np.testing.assert_array_equal(self.evidence.prior, self.prior)
        np.testing.assert_array_equal(self.evidence.maximum_pod, [0, 0.2, 0.4])
        np.testing.assert_allclose(
            self.evidence.probability, [0, before[1] / (1 - before[0]), before[2] / (1 - before[0])]
        )
        self.assertEqual(self.evidence.structure_mass("cabin", geometry(0, 4)), 0)
        self.apply("later-empty", [], [0, 0.5, 0.4])
        self.apply("later-person", [record("visible", "later-person", 10, 2)], [0, 0.8, 0.8])
        self.evidence.recompute()
        self.assertEqual(self.evidence.probability[0], 0)
        self.assertEqual(self.evidence.structure_mass("cabin", geometry(0, 4)), 0)

    def test_partial_cell_removal_keeps_mass_outside_the_footprint(self):
        effect = self.evidence.ground_negative("task", "cabin", geometry(0, 2))
        self.assertAlmostEqual(effect["removed_mass"], 0.25)
        np.testing.assert_allclose(self.evidence.ground_pod, [0.5, 0, 0])
        np.testing.assert_allclose(self.evidence.probability, np.array([0.25, 0.3, 0.2]) / 0.75)
        self.assertEqual(self.evidence.structure_mass("cabin", geometry(0, 2)), 0)
        self.assertGreater(self.evidence.probability[0], 0)
        self.assertAlmostEqual(self.evidence.structure_mass("other", geometry(2.5, 3.5)), 1 / 6)

    def test_disjoint_ground_checks_in_one_cell_add_area_fractions(self):
        self.evidence.ground_negative("first", "cabin", geometry(0, 2))
        effect = self.evidence.ground_negative("second", "shed", geometry(2.5, 3.5))
        self.assertAlmostEqual(effect["removed_mass"], 1 / 6)
        self.assertAlmostEqual(effect["removed_mass"] + effect["retained_mass"], 1)
        np.testing.assert_allclose(self.evidence.ground_pod, [0.75, 0, 0])
        np.testing.assert_allclose(self.evidence.probability, [0.2, 0.48, 0.32])

    def test_duplicate_overlap_touch_and_outside_checks_fail_before_mutation(self):
        self.evidence.ground_negative("first", "cabin", geometry(0, 2))
        for identity, shape in (
            ("cabin", geometry(0, 2)),
            ("overlap", geometry(1, 3)),
            ("touch", geometry(2, 3)),
            ("outside", geometry(100, 101)),
        ):
            before = self.snapshot()
            with self.subTest(identity=identity), self.assertRaises(AssertionError):
                self.evidence.ground_negative("other-task", identity, shape)
            self.unchanged(before)

    def test_whole_belief_contradiction_fails_before_mutation(self):
        self.evidence = Evidence(np.array([1.0, 0, 0]), self.grid, 0.02)
        before = self.snapshot()
        with self.assertRaisesRegex(AssertionError, "contradicts entire belief"):
            self.evidence.ground_negative("task", "cabin", geometry(0, 4))
        self.unchanged(before)

    def test_ground_geometry_is_a_snapshot_and_cannot_be_reinterpreted(self):
        shape = geometry(0, 2)
        self.evidence.ground_negative("task", "cabin", shape)
        shape["points"][0]["x"] = 100
        self.assertEqual(self.evidence.ground_checks["cabin"]["geometry"]["points"][0]["x"], 0)
        with self.assertRaises(AssertionError):
            self.evidence.structure_mass("cabin", shape)

    def test_real_capture_visibility_removes_only_the_protected_cell_credit(self):
        frame = json.loads(
            (ROOT / ".local/data/calibration/adult-kelvin/frame-00-+00-Drone1.json").read_text()
        )
        _, model = load_configuration()
        camera = Camera(frame)
        n, e = np.meshgrid(
            np.floor(camera.world["x"] / 4) * 4 + np.arange(-8, 8, 4),
            np.floor(camera.world["y"] / 4) * 4 + np.arange(-8, 8, 4),
            indexing="ij",
        )
        low = np.column_stack([n.ravel(), e.ravel()])
        grid = {"low": low, "high": low + 4, "centers": low + 2, "areas": np.full(len(low), 16.0)}
        clear, footprint = capture_pod(frame, grid, model, np.ones(len(low), dtype=bool))
        self.assertGreater(np.count_nonzero(clear), 1)
        cell = int(np.flatnonzero(clear)[0])
        north, east = grid["centers"][cell]
        mask = aerial_visibility(
            grid,
            [landmark("structure", geometry(north - 0.1, north + 0.1, east - 0.1, east + 0.1))],
        )
        obscured, same_footprint = capture_pod(frame, grid, model, mask)
        self.assertEqual(obscured[cell], 0)
        np.testing.assert_array_equal(obscured[mask], clear[mask])
        self.assertEqual(footprint, same_footprint)


if __name__ == "__main__":
    unittest.main()

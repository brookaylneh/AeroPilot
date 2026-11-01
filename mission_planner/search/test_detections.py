"""Evidence caps, complete-link association, and operator-only resolution."""

import math
import unittest

import numpy as np
from scipy.integrate import quad
from scipy.special import ndtr

from mission_planner.agents.perception.localization import ELLIPSE_95
from mission_planner.agents.perception.reduce_detection import reduce_calibration
from mission_planner.environment import ROOT
from mission_planner.search.belief import Evidence, region_probability
from mission_planner.search.detection import negative_model, positional_probability
from mission_planner.search.detections import Detections


def record(identity, frame, north, east=0, calibrated=True, decision="escalate", image=None):
    return {
        "id": identity,
        "frame_id": frame,
        "drone_id": "Drone1",
        "replay_lane": None,
        "source": "recorded",
        "prompt": "person",
        "calibrated": calibrated,
        "decision": decision,
        "report": {"decision": decision},
        "sam_confidence": 0.8,
        "input_sha256": frame if image is None else image,
        "localization": {
            "ground": {"north_m": north, "east_m": east, "up_m": 0},
            "covariance_m2": [[0.04, 0], [0, 0.04]],
            "contact_pixel": [0, 0],
            "ellipse95": {"radii_m": [math.sqrt(ELLIPSE_95 * 0.04)] * 2, "angle_rad": 0},
        },
    }


class EvidenceCaps(unittest.TestCase):
    def setUp(self):
        n, e = np.meshgrid(np.arange(-40, 40, 4), np.arange(-40, 40, 4), indexing="ij")
        low = np.column_stack([n.ravel(), e.ravel()]).astype(float)
        self.grid = {
            "low": low,
            "high": low + 4,
            "centers": low + 2,
            "areas": np.full(len(low), 16.0),
        }
        self.prior = np.full(len(low), 1 / len(low))
        self.ledger = Detections()
        self.evidence = Evidence(self.prior, self.grid, 0.01837048315229015)
        self.pod = np.full(len(low), 0.8)
        self.footprint = [
            {"north_m": n, "east_m": e} for n, e in ((-40, -40), (-40, 40), (40, 40), (40, -40))
        ]

    def apply(self, frame, records, calibrated=True):
        self.ledger.add_frame(frame, records)
        return self.evidence.apply(frame, self.pod, self.footprint, self.ledger, calibrated)

    def test_repeated_views_and_image_alias_do_not_recredit(self):
        self.apply("first", [record("a", "first", -8)])
        before = self.evidence.probability.copy()
        self.apply("second", [record("b", "second", -7.9)])
        self.apply("alias", [record("c", "alias", 8, image="first")])
        np.testing.assert_array_equal(before, self.evidence.probability)
        self.assertEqual(len(self.evidence.factors), 1)
        self.assertEqual(self.ledger.clusters["a"]["member_ids"], ["a", "b"])
        with self.assertRaisesRegex(AssertionError, "already committed"):
            self.ledger.add_frame("second", [])

    def test_same_frame_clusters_share_one_factor_and_partial_dismissal(self):
        self.apply("first", [record("a", "first", -8), record("b", "first", 8)])
        self.assertEqual(len(self.evidence.factors), 1)
        self.assertEqual(set(self.evidence.factors["first"]["clusters"]), {"a", "b"})
        self.ledger.resolve("a", "dismissed", "A known bystander", "operator")
        self.evidence.resolve(self.ledger)
        self.assertGreater(
            self.evidence.mass(self.ledger.clusters["b"]["localization"])["posterior_mass"],
            self.evidence.mass(self.ledger.clusters["a"]["localization"])["posterior_mass"],
        )
        self.ledger.resolve("b", "dismissed", "Also a bystander", "operator")
        self.evidence.resolve(self.ledger)
        detail = self.evidence.detail("a")[0]
        self.assertEqual(detail["active_cluster_ids"], [])
        self.assertEqual(detail["active_region_cells"], [])
        self.assertTrue(detail["region_cells"])
        np.testing.assert_allclose(self.evidence.probability, self.prior, atol=1e-15)
        self.assertEqual(float(self.evidence.maximum_pod.sum()), 0)
        self.apply("later", [record("c", "later", -8)])
        np.testing.assert_allclose(self.evidence.probability, self.prior, atol=1e-15)

    def test_mixed_old_new_frame_credits_only_the_new_cluster(self):
        self.apply("first", [record("a", "first", -8)])
        effect = self.apply("mixed", [record("b", "mixed", -7.9), record("c", "mixed", 8)])
        self.assertEqual(effect["credited_cluster_ids"], ["c"])
        self.assertEqual(list(self.evidence.factors["mixed"]["clusters"]), ["c"])
        self.assertFalse(
            set(self.evidence.factors["first"]["region_cells"])
            & set(self.evidence.factors["mixed"]["region_cells"])
        )
        before = self.evidence.probability.copy()
        self.apply("repeat", [record("d", "repeat", -8), record("e", "repeat", 8)])
        np.testing.assert_array_equal(self.evidence.probability, before)

    def test_new_cluster_in_reserved_cell_gets_no_factor(self):
        self.apply("first", [record("a", "first", 1, 1)])
        before = self.evidence.probability.copy()
        result = self.apply("second", [record("b", "second", 3, 3)])
        self.assertEqual(len(self.ledger.clusters), 2)
        self.assertEqual(self.ledger.clusters["b"]["reference_frame_id"], "second")
        self.assertEqual(result["credited_cluster_ids"], [])
        self.assertEqual(len(self.evidence.factors), 1)
        np.testing.assert_array_equal(before, self.evidence.probability)

    def test_duplicate_boxes_keep_members_but_only_one_observation(self):
        self.apply("frame", [record("a", "frame", 1, 1), record("b", "frame", 1.02, 1.03)])
        self.assertEqual(self.ledger.clusters["a"]["member_ids"], ["a", "b"])
        single_ledger = Detections()
        single_ledger.add_frame("frame", [record("a", "frame", 1, 1)])
        single = Evidence(self.prior, self.grid, self.evidence.clutter_rate)
        single.apply("frame", self.pod, self.footprint, single_ledger, True)
        np.testing.assert_array_equal(single.probability, self.evidence.probability)

    def test_actual_lower_curve_allows_a_later_positive_to_restore_mass(self):
        model = reduce_calibration(ROOT / ".local/data/calibration")
        lower = negative_model(model)
        self.pod = positional_probability(self.grid["centers"][:, 1], 0, lower)
        self.assertTrue((self.pod < 1).all())
        self.apply("negative", [])
        location = record("a", "positive", 1, 1)["localization"]
        reduced = self.evidence.mass(location)["posterior_mass"]
        self.assertLess(reduced, self.evidence.mass(location)["prior_mass"])
        self.assertTrue((self.evidence.probability > 0).all())
        self.pod = positional_probability(self.grid["centers"][:, 1], 0, model)
        self.apply("positive", [record("a", "positive", 1, 1)])
        self.assertGreater(self.evidence.mass(location)["posterior_mass"], reduced)

    def test_nonzero_clutter_prevents_one_box_from_collapsing_probability(self):
        self.pod[:] = 1
        self.apply("frame", [record("a", "frame", 1, 1)])
        probability = self.evidence.probability
        self.assertTrue(np.isfinite(probability).all())
        self.assertTrue((probability > 0).all())
        self.assertLess(float(probability.max()), 1)
        self.assertAlmostEqual(float(probability.sum()), 1)
        with self.assertRaises(AssertionError):
            Evidence(self.prior, self.grid, 0)

    def test_headline_sums_region_cells_and_keeps_ellipse_fraction_in_details(self):
        location = record("a", "frame", 2, 2)["localization"]
        self.apply("frame", [record("a", "frame", 2, 2)])
        result = self.evidence.mass(location)
        cell = np.flatnonzero((self.grid["centers"] == [2, 2]).all(axis=1))[0]
        self.assertEqual(result["region_cell_count"], 1)
        self.assertEqual(result["region_area_m2"], 16)
        self.assertEqual(result["prior_mass"], self.prior[cell])
        self.assertEqual(result["posterior_mass"], self.evidence.probability[cell])
        fraction = math.pi * ELLIPSE_95 * 0.04 / 16
        self.assertAlmostEqual(result["ellipse_area_fraction"], fraction)
        self.assertAlmostEqual(result["ellipse_prior_mass"], result["prior_mass"] * fraction)
        self.assertAlmostEqual(
            result["ellipse_posterior_mass"], result["posterior_mass"] * fraction
        )

    def test_headline_includes_all_four_cells_at_grid_vertex(self):
        result = self.evidence.mass(record("a", "frame", 0, 0)["localization"])
        self.assertEqual(result["region_cell_count"], 4)
        self.assertAlmostEqual(result["prior_mass"], 4 / len(self.prior))
        self.assertEqual(result["region_area_m2"], 64)

    def test_region_probability_averages_subcell_target_locations(self):
        low = np.array([[-2, -2], [2, -2]], dtype=float)
        grid = {"low": low, "high": low + 4, "centers": low + 2, "areas": np.full(2, 16.0)}
        result = region_probability(np.array([True, False]), np.eye(2) * 0.04, grid)
        one_axis = [
            quad(lambda x: ndtr((2 - x) / 0.2) - ndtr((-2 - x) / 0.2), a, b, epsabs=1e-12)[0] / 4
            for a, b in ((-2, 2), (2, 6))
        ]
        np.testing.assert_allclose(
            result, [one_axis[0] ** 2, one_axis[0] * one_axis[1]], atol=1e-12, rtol=0
        )
        self.assertAlmostEqual(result[0], 0.9218030933506326, places=12)
        self.assertGreater(result[1], 0)

    def test_world_edge_likelihood_does_not_reassign_outside_kernel_mass(self):
        grid = {
            "low": np.array([[0.0, 0.0]]),
            "high": np.array([[4.0, 4.0]]),
            "centers": np.array([[2.0, 2.0]]),
            "areas": np.array([16.0]),
        }
        ledger = Detections()
        ledger.add_frame("edge", [record("a", "edge", 0, 0)])
        evidence = Evidence(np.ones(1), grid, self.evidence.clutter_rate)
        footprint = [{"north_m": n, "east_m": e} for n, e in ((0, 0), (0, 4), (4, 4), (4, 0))]
        evidence.apply("edge", np.array([0.8]), footprint, ledger, True)
        factor = evidence.factors["edge"]
        kernel = factor["clusters"]["a"]
        self.assertAlmostEqual(float(kernel["kernel"].sum()), 1)
        self.assertAlmostEqual(kernel["retained_mass"], 0.25)
        kappa = -math.log1p(-evidence.clutter_rate) / 16
        expected = 1 - 0.8 * 0.9218030933506326 + 0.8 * (0.25 / 16) / kappa
        self.assertAlmostEqual(float(factor["likelihood"][0]), expected, places=10)

    def test_uncalibrated_members_cannot_credit_or_remove_probability(self):
        self.apply("empty", [], calibrated=False)
        self.apply("clue", [record("a", "clue", 0, calibrated=False)], calibrated=False)
        self.assertFalse(self.evidence.factors)
        self.assertEqual(float(self.evidence.maximum_pod.sum()), 0)
        self.assertIsNone(self.ledger.clusters["a"]["reference_frame_id"])
        self.apply("person", [record("b", "person", 0)])
        self.assertEqual(self.ledger.clusters["a"]["reference_frame_id"], "person")
        self.assertEqual(list(self.evidence.factors), ["person"])

    def test_complete_link_does_not_join_a_chain(self):
        self.ledger.add_frame("one", [record("a", "one", 0)])
        other = record("b", "two", 0.7)
        other["drone_id"] = "Drone2"
        self.ledger.add_frame("two", [other])
        self.ledger.add_frame("three", [record("c", "three", 1.4)])
        self.assertEqual(self.ledger.clusters["a"]["member_ids"], ["a", "b"])
        self.assertEqual(self.ledger.clusters["c"]["member_ids"], ["c"])
        self.assertEqual(self.ledger.records["b"]["drone_id"], "Drone2")

    def test_only_operator_resolves(self):
        self.apply("first", [record("a", "first", 0)])
        with self.assertRaisesRegex(AssertionError, "Only an operator"):
            self.ledger.resolve("a", "confirmed_lead", "Model saw a person", "agent")
        with self.assertRaises(AssertionError):
            self.ledger.resolve("a", "dismissed", "", "operator")
        self.assertEqual(self.ledger.clusters["a"]["status"], "pending")

    def test_invalid_later_record_does_not_partially_commit_frame(self):
        invalid = record("b", "frame", 20)
        invalid["sam_confidence"] = math.nan
        with self.assertRaises(AssertionError):
            self.ledger.add_frame("frame", [record("a", "frame", 0), invalid])
        self.assertFalse(self.ledger.frames)
        self.assertFalse(self.ledger.records)
        self.assertFalse(self.ledger.clusters)


if __name__ == "__main__":
    unittest.main()

"""Quoted geometry approval, line support and plan invalidation boundaries."""

import asyncio
import base64
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from mission_planner.environment import ROOT
from mission_planner.gcs.service import Mission
from mission_planner.search.places import location_support, proposal
from mission_planner.search.prior import initialize
from mission_planner.search.state import Search


class Places(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fence = [
            {"north_m": n, "east_m": e}
            for n, e in ((-100, -100), (-100, 100), (100, 100), (100, -100))
        ]
        self.landmarks = [
            {
                "id": "bridge",
                "name": "Footbridge",
                "aliases": ["crossing"],
                "kind": "bridge",
                "heightM": 0,
                "geometry": {"type": "point", "points": [{"x": 100, "y": 200, "z": 0}]},
            },
            {
                "id": "loop",
                "name": "Creek Loop",
                "aliases": [],
                "kind": "trail",
                "heightM": 0,
                "geometry": {
                    "type": "polyline",
                    "points": [
                        {"x": 0, "y": 0, "z": 0},
                        {"x": 2000, "y": 0, "z": 0},
                        {"x": 2000, "y": 6000, "z": 0},
                    ],
                },
            },
        ]
        self.briefing = {"lastKnownLocation": "Phone ping near the footbridge; on the Creek Loop."}
        self.evidence = [{"field": "lastKnownLocation", "quote": "near the footbridge"}]
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(temporary.cleanup)
        self.mission = object.__new__(Mission)
        self.mission.directory = Path(temporary.name)
        self.mission.approvals = {}
        self.mission.fleet_ready = True
        self.mission.autonomy = "Autonomous"
        self.mission.event = Mock()
        self.mission.case = {"report": self.briefing}
        self.mission.fleet = SimpleNamespace(snapshot=lambda: [{"fence": self.fence}])
        self.mission.search = Search(self.mission)
        self.mission.search.landmarks = self.landmarks
        self.mission.search.landmark_error = None
        self.mission.search.changed = Mock()

    def test_quotes_must_name_the_selected_place(self):
        with self.assertRaisesRegex(AssertionError, "must name"):
            proposal(self.landmarks, "loop", self.evidence, self.briefing, self.fence)
        with self.assertRaisesRegex(AssertionError, "verbatim"):
            proposal(
                self.landmarks,
                "bridge",
                [{"field": "lastKnownLocation", "quote": "at Footbridge"}],
                self.briefing,
                self.fence,
            )
        with self.assertRaisesRegex(AssertionError, "Unknown landmark"):
            proposal(self.landmarks, "unknown", self.evidence, self.briefing, self.fence)

    def test_log_heat_legend_uses_actual_probabilities_and_preserves_transparency(self):
        search = self.mission.search
        probability = np.array([1, 10, 100, 0], dtype=float) / 111
        search.probability = probability.copy()
        search.grid = {"shape": [2, 2], "areas": np.array([4, 4, 4, 0])}
        search.render_heatmap()
        scale = search.heatmap_scale
        self.assertEqual(scale["method"], "log1p")
        self.assertEqual(scale["maximum"], probability.max())
        self.assertEqual(scale["scale_probability"], probability[0])
        self.assertAlmostEqual(
            np.log1p(scale["midpoint"] / scale["scale_probability"])
            / np.log1p(scale["maximum"] / scale["scale_probability"]),
            0.5,
        )
        np.testing.assert_array_equal(search.probability, probability)
        image = np.asarray(Image.open(io.BytesIO(base64.b64decode(search.heatmap.split(",")[1]))))
        np.testing.assert_array_equal(image[::-1, :, 3].ravel(), [110, 110, 110, 0])

    async def test_place_requires_approval_even_in_autonomous_mode(self):
        search = self.mission.search
        search.plan = {"plan_id": "old"}
        task = asyncio.create_task(
            self.mission.execute(
                "set_last_known_place",
                {"place_id": "bridge", "evidence": self.evidence},
                "agent",
                "call",
            )
        )
        await asyncio.sleep(0)
        self.assertIsNone(search.point)
        self.assertEqual(len(self.mission.approvals), 1)
        with self.assertRaisesRegex(AssertionError, "pending approval"):
            search.mark_point(10, 20)
        next(iter(self.mission.approvals.values()))["future"].set_result(True)
        result = await task
        self.assertEqual(result["last_known_point"]["source"], "operator_approved_place")
        self.assertEqual((search.point["north_m"], search.point["east_m"]), (1, 2))
        self.assertEqual(search.point["geometry"], self.landmarks[0]["geometry"])
        self.assertIsNone(search.plan)
        self.assertFalse(self.mission.approvals)

    async def test_decline_leaves_hypothesis_and_plan_untouched(self):
        search = self.mission.search
        search.point = {"north_m": 10, "east_m": 10, "source": "operator", "evidence": []}
        search.plan = {"plan_id": "old"}
        task = asyncio.create_task(
            self.mission.execute(
                "set_last_known_place",
                {"place_id": "bridge", "evidence": self.evidence},
                "agent",
                "call",
            )
        )
        await asyncio.sleep(0)
        next(iter(self.mission.approvals.values()))["future"].set_result(False)
        with self.assertRaisesRegex(AssertionError, "Operator declined"):
            await task
        self.assertEqual(search.point["north_m"], 10)
        self.assertEqual(search.plan["plan_id"], "old")
        self.assertFalse(self.mission.approvals)

    async def test_missing_landmarks_fail_only_place_tools(self):
        self.mission.search.landmarks = None
        self.mission.search.landmark_error = "Landmark data unavailable"
        with self.assertRaisesRegex(AssertionError, "unavailable"):
            await self.mission.search.execute("find_place", {"name": "footbridge"})
        self.assertIsNone(self.mission.search.directions(0, 0))
        self.assertIsNone(self.mission.search.summary()["last_known_point"])

    async def test_cancelled_approval_does_not_leave_a_pending_card(self):
        task = asyncio.create_task(
            self.mission.execute(
                "set_last_known_place",
                {"place_id": "bridge", "evidence": self.evidence},
                "agent",
                "call",
            )
        )
        await asyncio.sleep(0)
        self.assertEqual(len(self.mission.approvals), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.mission.approvals)
        self.assertIsNone(self.mission.search.point)

    async def test_changed_geometry_requires_a_new_approval(self):
        task = asyncio.create_task(
            self.mission.execute(
                "set_last_known_place",
                {"place_id": "bridge", "evidence": self.evidence},
                "agent",
                "call",
            )
        )
        await asyncio.sleep(0)
        self.landmarks[0]["geometry"]["points"][0]["x"] = 500
        next(iter(self.mission.approvals.values()))["future"].set_result(True)
        with self.assertRaisesRegex(AssertionError, "Place changed during approval"):
            await task
        self.assertFalse(self.mission.approvals)
        self.assertIsNone(self.mission.search.point)

    def prepare_behavior(self):
        search = self.mission.search
        search.profile = {
            "category": {"value": "dementia"},
            "person_prompts": [],
            "clue_prompts": [],
        }
        search.point = {"north_m": 2, "east_m": 2, "source": "operator", "evidence": []}
        search.vertices = self.fence
        search.domain = "temperate_mountainous"
        search.render_heatmap = Mock()
        search.landmarks = [
            {
                "id": kind,
                "name": kind,
                "aliases": [],
                "kind": kind,
                "heightM": 0,
                "geometry": {
                    "type": geometry,
                    "points": [{"x": n * 100, "y": e * 100, "z": 0} for n, e in vertices],
                },
            }
            for kind, geometry, vertices in (
                ("structure", "polygon", [(0, 0), (2, 0), (2, 2), (0, 2)]),
                ("forest", "polygon", [(4, 0), (8, 0), (8, 4), (4, 4)]),
                ("pond", "polygon", [(0, 4), (4, 4), (4, 8), (0, 8)]),
                ("road", "polyline", [(0, 0), (8, 0)]),
                ("trail", "polyline", [(0, 4), (8, 4)]),
                ("creek", "polyline", [(0, 8), (8, 8)]),
            )
        ]
        low = np.array([[0, 0], [0, 4], [4, 0], [4, 4]], dtype=float)
        return {"low": low, "high": low + 4, "centers": low + 2, "areas": np.full(4, 16.0)}

    def test_behavior_initialization_retains_radial_outside_world_mass(self):
        grid = self.prepare_behavior()
        search = self.mission.search
        radial, retained, _ = initialize(grid, search.point, "dementia", "temperate_mountainous")
        with patch("mission_planner.search.state.make_grid", return_value=grid):
            search.initialize()
        self.assertEqual(search.retained, retained)
        self.assertEqual(1 - search.retained, 1 - retained)
        self.assertTrue(0 < search.retained < 1)
        self.assertAlmostEqual(search.probability.sum(), 1)
        self.assertFalse(np.allclose(search.probability, radial))
        np.testing.assert_array_equal(search.commander.evidence.prior, search.probability)
        self.assertIsNotNone(search.prior_fit["behavior"])
        search.render_heatmap.assert_called_once()

    def test_missing_landmarks_leave_initialization_state_unchanged(self):
        grid = self.prepare_behavior()
        search = self.mission.search
        search.landmarks = None
        fields = (
            "grid",
            "probability",
            "retained",
            "prior_fit",
            "pod",
            "model",
            "heatmap",
            "heatmap_scale",
            "revision",
            "status",
        )
        before = {key: getattr(search, key) for key in fields}
        with patch("mission_planner.search.state.make_grid", return_value=grid):
            with self.assertRaisesRegex(AssertionError, "requires the public landmark catalogue"):
                search.initialize()
        self.assertEqual({key: getattr(search, key) for key in fields}, before)
        self.assertIsNone(search.commander.evidence)
        search.render_heatmap.assert_not_called()
        search.changed.assert_not_called()

    async def test_unsupported_or_mismatched_domain_rejects_area_before_mutation(self):
        search = self.mission.search
        search.vertices = self.fence
        search.domain = "temperate_mountainous"
        search.plan = {"plan_id": "existing"}
        search.status = "planned"
        for category, domain, error in (
            ("hiker", "australian_mixed", "No sourced Sierra"),
            ("dementia", "dry_mountainous", "Statistical domain differs"),
        ):
            search.profile = {"category": {"value": category}}
            fields = (
                "vertices",
                "domain",
                "plan",
                "status",
                "grid",
                "probability",
                "retained",
                "prior_fit",
                "revision",
            )
            before = {key: getattr(search, key) for key in fields}
            with (
                self.subTest(category=category, domain=domain),
                self.assertRaisesRegex(AssertionError, error),
            ):
                await search.execute(
                    "define_search_area", {"vertices": self.fence[:3], "domain": domain}
                )
            self.assertEqual({key: getattr(search, key) for key in fields}, before)
            search.changed.assert_not_called()

    def test_line_preserves_geometry_and_weights_by_arclength(self):
        location = proposal(
            self.landmarks,
            "loop",
            [{"field": "lastKnownLocation", "quote": "on the Creek Loop"}],
            self.briefing,
            self.fence,
        )
        self.assertEqual((location["north_m"], location["east_m"]), (20, 20))
        points, weights = location_support(location, 4)
        self.assertAlmostEqual(weights.sum(), 1)
        self.assertAlmostEqual(weights[points[:, 1] == 0].sum(), 0.25)
        np.testing.assert_array_equal(points[0], [2, 0])
        np.testing.assert_array_equal(points[-1], [20, 58])
        low = np.array([[-20, -20], [-20, 20], [20, -20], [20, 20]])
        grid = {"low": low, "high": low + 40, "centers": low + 20, "areas": np.full(4, 1600)}
        actual, retained, _ = initialize(grid, location, "dementia", "temperate_mountainous")
        support, w = location_support(location, 40)
        expected = np.zeros(4)
        for position, weight in zip(support, w):
            p, mass, _ = initialize(
                grid,
                {"north_m": position[0], "east_m": position[1]},
                "dementia",
                "temperate_mountainous",
            )
            expected += weight * p * mass
        self.assertAlmostEqual(expected.sum(), retained)
        np.testing.assert_allclose(actual, expected / expected.sum(), rtol=1e-14)


if __name__ == "__main__":
    unittest.main()

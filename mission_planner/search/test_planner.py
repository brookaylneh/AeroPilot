"""Deterministic planning and safety against the real heightfield and calibration."""

import ast
import copy
import unittest

import numpy as np

from mission_planner.environment import ROOT
from mission_planner.gcs.geodesy import fence_vertices, homes, to_geodetic
from mission_planner.search.detection import reduce_calibration
from mission_planner.search.grid import make_grid, polygon_areas
from mission_planner.search.planner import AIRBORNE_SECONDS, encode, plan, validate_batch
from mission_planner.search.prior import initialize, table
from mission_planner.search.subject import PROFILE_FIELDS, explicit_point, validate_profile


class Planner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grid = make_grid(fence_vertices())
        cls.model = reduce_calibration(ROOT / ".local/data/calibration")
        cls.probability, _, _ = initialize(
            cls.grid, {"north_m": -650, "east_m": 0}, "child_td_1_16", "temperate"
        )
        cls.vertices = [
            {"north_m": n, "east_m": e}
            for n, e in ((-790, -120), (-500, -120), (-500, 120), (-790, 120))
        ]
        cls.states = {
            n: {
                "id": n,
                "home": h,
                "north_m": h["north_m"],
                "east_m": h["east_m"],
                "altitude_above_home_m": 0.0,
                "armed": False,
                "landed_state": 1,
                "link_lost": False,
                "ekf_healthy": True,
            }
            for n, h in homes().items()
        }
        fence = [
            {
                "x": round(to_geodetic(p["north_m"], p["east_m"])[0] * 1e7),
                "y": round(to_geodetic(p["north_m"], p["east_m"])[1] * 1e7),
            }
            for p in fence_vertices()
        ]
        cls.readbacks = {n: fence for n in cls.states}

    def make_plan(self):
        return plan(
            self.grid,
            self.probability,
            self.vertices,
            self.states,
            self.readbacks,
            self.model,
            {"test": "determinism"},
        )

    def test_byte_identical_validated_plan_and_endurance(self):
        first = self.make_plan()
        self.assertEqual(encode(first), encode(self.make_plan()))
        self.assertLessEqual(first["max_sampled_agl_error_m"], 2)
        for route in first["routes"].values():
            self.assertLessEqual(
                route["search_ends_s"] + route["reserve_seconds"], AIRBORNE_SECONDS
            )
            for waypoint in route["waypoints"]:
                sent = waypoint["altitude_above_home_m"]
                received = float(
                    np.float32(int(np.float32(sent) * np.float32(100))) * np.float32(0.01)
                )
                self.assertLess(abs(sent - received), 1e-4)
        self.assertEqual(len(first["alternatives"]), 12)
        for name in ("planned", "uniform_lawnmower"):
            curve = first["comparison"][name]
            self.assertTrue(all(a["pos"] <= b["pos"] for a, b in zip(curve, curve[1:])))
            self.assertTrue({300, 600, 900, 1200}.issubset({p["seconds"] for p in curve}))

    def test_batch_collision_is_refused(self):
        p = {"north_m": -600.0, "east_m": 0.0, "altitude_above_home_m": 30.0}
        with self.assertRaisesRegex(AssertionError, "conflicts"):
            validate_batch({n: [p] for n in self.states}, self.states, self.readbacks)

    def test_unsupported_prior_is_explicit(self):
        with self.assertRaisesRegex(AssertionError, "Unsupported prior"):
            table("elderly_non_dementia", "temperate")

    def test_polygon_notch_inside_a_cell_is_not_filled(self):
        grid = {"low": np.array([[0.0, 0.0]]), "high": np.array([[4.0, 4.0]])}
        polygon = [
            {"north_m": n, "east_m": e}
            for n, e in ((-1, -1), (5, -1), (5, 1), (2, 1), (2, 3), (5, 3), (5, 5), (-1, 5))
        ]
        self.assertAlmostEqual(polygon_areas(grid, polygon)[0], 12.0)

    def test_runtime_has_no_truth_or_actor_dependency(self):
        allowed = {
            "asyncio",
            "base64",
            "functools",
            "hashlib",
            "io",
            "json",
            "math",
            "re",
            "time",
            "pathlib",
            "numpy",
            "PIL",
            "scipy.stats",
            "mission_planner.environment",
            "mission_planner.search",
            "mission_planner.gcs.geodesy",
            "mission_planner.gcs.validation",
            "mission_planner.agents.perception.geometry",
            "mission_planner.agents.perception.glimmer_calibration",
            "world.cosys.terrain",
        }
        for path in (ROOT / "mission_planner/search").glob("*.py"):
            if path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                modules = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                for module in modules:
                    self.assertTrue(
                        any(
                            module == prefix or module.startswith(prefix + ".")
                            for prefix in allowed
                        ),
                        (path, module),
                    )
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertFalse(
                        any(
                            term in node.value.lower()
                            for term in (
                                "actorplacement",
                                "actortelemetry",
                                "runtime-actors",
                                "evaluator/",
                                "scenario.json",
                            )
                        ),
                        (path, node.value),
                    )


class Subject(unittest.TestCase):
    def setUp(self):
        self.report = {
            "description": "A hiker is missing.",
            "reportedCircumstances": "",
            "lastKnownLocation": "Beside the old bridge.",
        }
        self.profile = {k: {"value": None, "evidence": []} for k in PROFILE_FIELDS}
        self.profile.update(
            category={
                "value": "hiker",
                "evidence": [{"field": "description", "quote": self.report["description"]}],
            },
            person_prompts=[],
            clue_prompts=[],
        )

    def test_unknown_fields_and_landmark_do_not_invent_coordinates(self):
        self.profile["last_known_location"] = {
            "value": self.report["lastKnownLocation"],
            "evidence": [{"field": "lastKnownLocation", "quote": self.report["lastKnownLocation"]}],
        }
        validate_profile(self.profile, self.report)
        self.assertIsNone(explicit_point(self.profile))

    def test_fabricated_quote_and_age_are_rejected(self):
        p = copy.deepcopy(self.profile)
        p["category"]["evidence"][0]["quote"] = "An elderly hiker is missing."
        with self.assertRaisesRegex(AssertionError, "verbatim"):
            validate_profile(p, self.report)
        self.profile["age_years"] = {"value": 80, "evidence": self.profile["category"]["evidence"]}
        with self.assertRaisesRegex(AssertionError, "explicitly stated"):
            validate_profile(self.profile, self.report)


if __name__ == "__main__":
    unittest.main()

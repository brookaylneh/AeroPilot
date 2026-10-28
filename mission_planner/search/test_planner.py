"""Deterministic planning and safety against the real heightfield and calibration."""

import ast
import copy
import unittest

import numpy as np

from mission_planner.environment import ROOT
from mission_planner.gcs.geodesy import fence_vertices, homes, to_geodetic
from mission_planner.search.detection import reduce_calibration
from mission_planner.search.grid import make_grid, polygon_areas
from mission_planner.search.planner import (
    AIRBORNE_SECONDS,
    candidate,
    encode,
    materialize,
    plan,
    validate_batch,
)
from mission_planner.search.prior import initialize, table
from mission_planner.search.subject import PROFILE_FIELDS, explicit_point, validate_profile
from mission_planner.search.terrain import terrain_leg
from mission_planner.search.timing import travel_seconds
from world.cosys.terrain import WORLD


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

    def test_timing_uses_oriented_legs_and_full_terrain_connectors(self):
        vertices = [
            {"north_m": n, "east_m": e}
            for n, e in ((-700, -400), (-200, -400), (-200, 400), (-700, 400))
        ]
        result = candidate(self.grid, self.probability, vertices, homes(), self.model, 40, 0, True)
        self.assertIsNotNone(result)
        asymmetric_legs = 0
        for name, route in result["routes"].items():
            home_z = WORLD["vehicles"][name]["spawnUeCm"]["z"] / 100
            for leg in route["legs"]:
                forward = travel_seconds(terrain_leg(leg["start"], leg["end"], home_z)[0])
                reverse = travel_seconds(terrain_leg(leg["end"], leg["start"], home_z)[0])
                asymmetric_legs += abs(forward - reverse) > 1e-6
                self.assertAlmostEqual(leg["ends_s"] - leg["starts_s"], forward)
        self.assertGreater(asymmetric_legs, 0)
        materialize(result, self.states, self.readbacks)

    def test_concave_area_connector_blends_preserve_timeline_order(self):
        vertices = [
            {"north_m": n, "east_m": e}
            for n, e in (
                (-750, -400),
                (-100, -400),
                (-100, 400),
                (-250, 400),
                (-250, -390),
                (-600, -390),
                (-600, 400),
                (-750, 400),
            )
        ]
        result = candidate(self.grid, self.probability, vertices, homes(), self.model, 40, 0, True)
        self.assertIsNotNone(result)
        terrain_connectors = 0
        for name, route in result["routes"].items():
            home_z = WORLD["vehicles"][name]["spawnUeCm"]["z"] / 100
            for before, after in zip(route["legs"], route["legs"][1:]):
                terrain_connectors += len(terrain_leg(before["end"], after["start"], home_z)[0]) > 2
                self.assertGreaterEqual(after["starts_s"], before["ends_s"] - 1e-8)
        self.assertGreater(terrain_connectors, 0)
        materialize(result, self.states, self.readbacks)

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
            "hashlib",
            "io",
            "json",
            "math",
            "matplotlib",
            "re",
            "time",
            "pathlib",
            "numpy",
            "PIL",
            "scipy.interpolate",
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
            if path.name.startswith("test_") or path.name == "report_flight.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "mission_planner.search":
                    self.assertNotIn("report_flight", [alias.name for alias in node.names])
                modules = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                for module in modules:
                    self.assertNotEqual(module, "mission_planner.search.report_flight")
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

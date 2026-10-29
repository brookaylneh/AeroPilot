"""Expanding-square order, sector clipping and the shared terrain/endurance clock."""

import copy
import math
import unittest
from unittest.mock import patch

import numpy as np

from mission_planner.agents.perception.configuration import load_configuration
from mission_planner.gcs.geodesy import fence_vertices, homes, to_geodetic
from mission_planner.search.baselines import (
    expanding_square,
    ordered_candidate,
    partition_square,
)
from mission_planner.search.detection import CAPTURE_SPACING_M, track_detection
from mission_planner.search.grid import inside
from mission_planner.search.planner import (
    AIRBORNE_SECONDS,
    RESERVE_SECONDS,
    SECTOR_DRONES,
    SECTOR_INSET_M,
    TRANSIT_HEIGHTS_M,
    materialize,
    point,
    track_pod,
)
from mission_planner.search.timing import landing_seconds, launch_seconds, travel_seconds


def rectangle(n0, n1, e0, e1):
    return [{"north_m": n, "east_m": e} for n, e in ((n0, e0), (n1, e0), (n1, e1), (n0, e1))]


def horizontal(leg):
    return tuple((p["north_m"], p["east_m"]) for p in leg)


class ExpandingSquare(unittest.TestCase):
    def test_starts_at_datum_clockwise_with_each_length_used_twice(self):
        for axis in (0, 1):
            legs = expanding_square(
                {"north_m": 0, "east_m": 0}, 10, axis, rectangle(-20, 20, -20, 20)
            )
            self.assertEqual(len(legs), 8)
            self.assertEqual(horizontal(legs[0]), ((0, 0), (10, 0) if axis == 0 else (0, 10)))
            lengths = [math.dist(*horizontal(leg)) for leg in legs]
            self.assertEqual(lengths, [10, 10, 20, 20, 30, 30, 40, 40])
            for (a, b), (c, d) in zip(legs, legs[1:]):
                self.assertEqual(b, c)
                first = np.subtract(horizontal((a, b))[1], horizontal((a, b))[0])
                second = np.subtract(horizontal((c, d))[1], horizontal((c, d))[0])
                self.assertEqual(first @ second, 0)
                self.assertGreater(first[0] * second[1] - first[1] * second[0], 0)

    def test_nonrectangle_crossed_rectangle_invalid_spacing_and_external_datum_fail(self):
        vertices = rectangle(-20, 20, -20, 20)
        invalid = [vertices[:3], [vertices[i] for i in (0, 2, 1, 3)]]
        shifted = copy.deepcopy(vertices)
        shifted[0]["north_m"] += 1
        invalid.append(shifted)
        for polygon in invalid:
            with self.subTest(polygon=polygon), self.assertRaises(AssertionError):
                expanding_square({"north_m": 0, "east_m": 0}, 10, 0, polygon)
        for spacing in (0, -1, math.inf):
            with self.subTest(spacing=spacing), self.assertRaises(AssertionError):
                expanding_square({"north_m": 0, "east_m": 0}, spacing, 0, vertices)
        with self.assertRaises(AssertionError):
            expanding_square({"north_m": 21, "east_m": 0}, 10, 0, vertices)

    def test_clipping_retains_direction_and_order_with_explicit_sector_gaps(self):
        tracks = partition_square(rectangle(-30, 30, -40, 40), {"north_m": 0, "east_m": 0}, 10, 0)
        self.assertEqual(list(tracks), list(SECTOR_DRONES))
        self.assertEqual(
            [horizontal(leg) for leg in tracks["Drone2"][:3]],
            [((10, 2), (10, 10)), ((10, 10), (-10, 10)), ((-10, 10), (-10, 2))],
        )
        self.assertEqual(
            [horizontal(leg) for leg in tracks["Drone3"][:3]],
            [((-10, -2), (-10, -10)), ((-10, -10), (20, -10)), ((20, -10), (20, -2))],
        )
        self.assertEqual(horizontal(tracks["Drone1"][1]), ((-30, -30), (30, -30)))
        for sector, name in enumerate(SECTOR_DRONES):
            for a, b in tracks[name]:
                self.assertNotEqual(a, b)
                for p in (a, b):
                    self.assertTrue(-30 <= p["north_m"] <= 30)
                    self.assertTrue(
                        -40 + 20 * sector + SECTOR_INSET_M
                        <= p["east_m"]
                        <= -20 + 20 * sector - SECTOR_INSET_M
                    )


class OrderedEvaluation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        n, e = np.meshgrid(np.arange(-788, -550, 4), np.arange(-118, 120, 4), indexing="ij")
        cls.grid = {"centers": np.column_stack([n.ravel(), e.ravel()])}
        cls.probability = np.full(len(n.ravel()), 1 / len(n.ravel()))
        cls.vertices = rectangle(-790, -550, -120, 120)
        cls.homes = homes()
        cls.model = load_configuration()[1]
        cls.states = {
            name: {
                "home": home,
                "north_m": home["north_m"],
                "east_m": home["east_m"],
                "altitude_above_home_m": 0.0,
            }
            for name, home in cls.homes.items()
        }
        fence = [
            dict(zip(("x", "y"), (round(v * 1e7) for v in to_geodetic(p["north_m"], p["east_m"]))))
            for p in fence_vertices()
        ]
        cls.readbacks = {name: fence for name in cls.states}

    def evaluate(self, tracks, vertices):
        return ordered_candidate(
            self.grid, self.probability, vertices, self.homes, self.model, tracks, 40, 0
        )

    def test_materialized_airborne_clock_matches_and_only_search_legs_receive_credit(self):
        tracks = partition_square(self.vertices, {"north_m": -670, "east_m": 0}, 40, 0)
        result = self.evaluate(tracks, self.vertices)
        materialize(result, self.states, self.readbacks, [])
        cumulative = np.zeros(len(self.probability))
        detect = track_detection(self.model, CAPTURE_SPACING_M)
        for name, route in result["routes"].items():
            self.assertLessEqual(
                route["search_ends_s"] + route["reserve_seconds"], AIRBORNE_SECONDS
            )
            self.assertGreaterEqual(route["reserve_seconds"], RESERVE_SECONDS)
            self.assertAlmostEqual(
                route["airborne_seconds"],
                launch_seconds(max(TRANSIT_HEIGHTS_M))
                + travel_seconds(route["waypoints"])
                + landing_seconds(route["waypoints"][-1]["altitude_above_home_m"]),
            )
            self.assertEqual(
                [horizontal((leg["start"], leg["end"])) for leg in route["legs"]],
                [horizontal(leg) for leg in tracks[name][: len(route["legs"])]],
            )
            self.assertGreater(route["legs"][0]["starts_s"], launch_seconds(max(TRANSIT_HEIGHTS_M)))
            for before, after in zip(route["legs"], route["legs"][1:]):
                self.assertGreaterEqual(after["starts_s"], before["ends_s"] - 1e-8)
            for leg in route["legs"]:
                ids, pod = track_pod(
                    self.grid["centers"],
                    leg["start"],
                    leg["end"],
                    self.model,
                    inside(self.grid["centers"], self.vertices),
                    detect,
                )
                cumulative[ids] = np.maximum(cumulative[ids], pod)
        self.assertAlmostEqual(result["pos"], float(self.probability @ cumulative))
        self.assertLessEqual(result["max_sampled_agl_error_m"], 1)

    def test_first_unaffordable_leg_ends_route_without_skipping_to_shorter_later_leg(self):
        tracks = {
            name: [
                (point(-740, east, 0), point(-730, east, 0)),
                (point(-730, east, 0), point(700, east, 0)),
                (point(-730, east, 0), point(-720, east, 0)),
            ]
            for name, east in zip(SECTOR_DRONES, (-75, -25, 25, 75))
        }
        with patch("mission_planner.search.baselines.AIRBORNE_SECONDS", 500):
            result = self.evaluate(tracks, rectangle(-750, 750, -100, 100))
        self.assertIsNotNone(result)
        self.assertTrue(all(len(route["legs"]) == 1 for route in result["routes"].values()))
        materialize(result, self.states, self.readbacks, [])
        shorter = {name: [legs[0], legs[2]] for name, legs in tracks.items()}
        with patch("mission_planner.search.baselines.AIRBORNE_SECONDS", 500):
            both = self.evaluate(shorter, rectangle(-750, 750, -100, 100))
        self.assertTrue(all(len(route["legs"]) == 2 for route in both["routes"].values()))
        tracks["Drone1"] = []
        self.assertIsNone(self.evaluate(tracks, rectangle(-750, 750, -100, 100)))


if __name__ == "__main__":
    unittest.main()

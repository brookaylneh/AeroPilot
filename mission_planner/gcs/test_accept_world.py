"""The acceptance sorties reach both edges and return to the interior launch port."""

import unittest

from mission_planner.gcs.accept_world import routes, validate_sortie
from mission_planner.gcs.geodesy import NAMES, ORIGIN, fence_vertices, homes, rtl_route, to_geodetic
from mission_planner.gcs.validation import validate_route
from world.cosys.landmarks import load
from world.cosys.terrain import BOUNDS


class WorldRoutes(unittest.TestCase):
    def setUp(self):
        self.states = [
            {"id": name, "home": home, "altitude_above_home_m": 0, **home}
            for name, home in homes().items()
        ]
        self.fence = [
            {
                "x": round(to_geodetic(p["north_m"], p["east_m"])[0] * 1e7),
                "y": round(to_geodetic(p["north_m"], p["east_m"])[1] * 1e7),
            }
            for p in fence_vertices()
        ]

    def test_full_sorties_reserve_takeoff_auto_and_rtl_paths(self):
        plans = routes(self.states)
        returns = validate_sortie(self.states, plans, {name: self.fence for name in NAMES}, load())
        for name, boundary in (("Drone1", "min"), ("Drone2", "max")):
            self.assertLessEqual(
                min(abs(p["east_m"] - BOUNDS[boundary]["y"] / 100) for p in plans[name]), 50
            )
        for state in self.states:
            last = returns[state["id"]][-1]
            self.assertEqual(last["altitude_above_home_m"], 0)
            self.assertEqual(last["north_m"], state["home"]["north_m"])
            self.assertEqual(last["east_m"], state["home"]["east_m"])

    def test_direct_rtl_from_far_edges_stays_inside_fence(self):
        plans = routes(self.states)
        for state in self.states[:2]:
            edge = {**state, **plans[state["id"]][2]}
            validate_route(
                rtl_route(edge)[:-1],
                self.fence,
                state["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"],
                load(),
            )

    def test_route_assignment_rejects_reordered_vehicles(self):
        with self.assertRaises(AssertionError):
            routes(list(reversed(self.states)))


if __name__ == "__main__":
    unittest.main()

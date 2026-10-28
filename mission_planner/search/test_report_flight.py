"""Flight reductions reject ambiguous endpoints and exclude ingress telemetry."""

import unittest

from mission_planner.search.report_flight import duration, tracking, waypoint_sequence
from world.cosys.terrain import terrain_height


class FlightReport(unittest.TestCase):
    def test_target_sequence_window_excludes_ingress_and_next_connector(self):
        states = [
            {
                "mode": "AUTO",
                "mission_current": sequence,
                "north_m": -650,
                "east_m": 0,
                "altitude_above_home_m": terrain_height(-650, 0) + height,
                "ground_speed_mps": speed,
            }
            for sequence, height, speed in ((5, 70, 0), (6, 30, 5), (7, 31, 4), (8, 70, 0))
        ]
        result = tracking(states, 5, 7, 0)
        self.assertEqual(result["samples"], 2)
        self.assertAlmostEqual(result["min_agl_m"], 30)
        self.assertAlmostEqual(result["max_agl_m"], 31)
        self.assertEqual(result["speed_p50_mps"], 4.5)
        with self.assertRaisesRegex(AssertionError, "No recorded telemetry"):
            tracking(states, 8, 9, 0)

    def test_duplicate_search_endpoint_is_ambiguous(self):
        point = {"north_m": -650, "east_m": 0, "altitude_above_home_m": 30}
        self.assertEqual(waypoint_sequence([point], point), 1)
        with self.assertRaisesRegex(AssertionError, "exactly one"):
            waypoint_sequence([point, point], point)

    def test_relative_error_checks_airborne_and_leg_durations(self):
        self.assertTrue(duration(100, 109)["within_ten_percent"])
        self.assertTrue(duration(100, 110)["within_ten_percent"])
        self.assertTrue(duration(100, 90)["within_ten_percent"])
        self.assertFalse(duration(100, 111)["within_ten_percent"])
        self.assertFalse(duration(100, 89)["within_ten_percent"])
        with self.assertRaises(AssertionError):
            duration(100, 0)


if __name__ == "__main__":
    unittest.main()

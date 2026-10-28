"""Waypoint previews blend long shallow joins, while short segments remain speed limited."""

import unittest

from mission_planner.search.timing import travel_seconds


def point(north, east, altitude):
    return {"north_m": north, "east_m": east, "altitude_above_home_m": altitude}


class Timing(unittest.TestCase):
    def test_long_straight_preview_does_not_charge_a_second_start(self):
        a, b, c = point(0, 0, 30), point(100, 0, 30), point(300, 0, 30)
        self.assertAlmostEqual(travel_seconds([a, b, c]), travel_seconds([a, c]))

    def test_short_waypoints_cannot_reach_requested_cruise_speed(self):
        route = [point(north, 0, 30) for north in range(0, 121, 12)]
        self.assertGreater(travel_seconds(route), travel_seconds([route[0], route[-1]]))

    def test_right_angle_requires_deceleration(self):
        straight = [point(0, 0, 30), point(100, 0, 30), point(200, 0, 30)]
        corner = [point(0, 0, 30), point(100, 0, 30), point(100, 100, 30)]
        self.assertGreater(travel_seconds(corner), travel_seconds(straight))


if __name__ == "__main__":
    unittest.main()

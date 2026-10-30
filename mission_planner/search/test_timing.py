"""Waypoint previews blend long shallow joins, while short segments remain speed limited."""

import copy
import unittest

from mission_planner.search.timing import extension_seconds, travel_seconds


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

    def test_extension_matches_full_route_with_duplicates_turns_and_verticals(self):
        a, b, c = point(0, 0, 30), point(100, 0, 30), point(200, 0, 30)
        cases = (
            ([a, b], [b, c]),
            ([a, a, b, b], [b, c, c]),
            ([a, {**b, "seq": 1}, {**b, "seq": 2}], [c]),
            ([a, a], [a, b]),
            ([a, b, c], [point(200, 100, 30)]),
            ([a, b, c], [b, a]),
            ([point(0, 0, 10), a], [point(0, 0, 50), point(0, 0, 20)]),
            ([a, point(100, 0, 40)], [point(100, 100, 45), point(100, 100, 25)]),
        )
        for prefix, extension in cases:
            original = copy.deepcopy((prefix, extension))
            with self.subTest(prefix=prefix, extension=extension):
                expected = travel_seconds([*prefix, *extension]) - travel_seconds(prefix)
                self.assertAlmostEqual(extension_seconds(prefix, extension), expected, places=10)
                self.assertEqual((prefix, extension), original)

    def test_partial_extensions_preserve_full_recalculation_at_each_fraction(self):
        prefix = [point(0, 0, 30), point(50, 0, 31), point(100, 0, 32)]
        destinations = (point(200, 0, 35), point(100, 100, 32), point(100, 0, 15))
        for destination in destinations:
            for fraction in (0.0, 0.001, 0.1, 0.5, 1.0):
                endpoint = {
                    key: prefix[-1][key] + fraction * (destination[key] - prefix[-1][key])
                    for key in prefix[-1]
                }
                with self.subTest(destination=destination, fraction=fraction):
                    expected = travel_seconds([*prefix, endpoint]) - travel_seconds(prefix)
                    self.assertAlmostEqual(
                        extension_seconds(prefix, [endpoint]), expected, places=10
                    )

    def test_successive_extensions_equal_one_full_timing_pass(self):
        route = [point(0, 0, 30), point(100, 0, 30)]
        elapsed = travel_seconds(route)
        for extension in (
            [point(100, 0, 30)],
            [point(150, 0, 30), point(200, 0, 32)],
            [point(200, 0, 42), point(200, 100, 42)],
            [point(200, 100, 42), point(200, 100, 30)],
        ):
            elapsed += extension_seconds(route, extension)
            route.extend(extension)
            self.assertAlmostEqual(elapsed, travel_seconds(route), places=10)

    def test_empty_extension_has_no_cost_even_with_repeated_final_point(self):
        a, b = point(0, 0, 30), point(100, 0, 30)
        for prefix in ([a, b], [a, b, b]):
            self.assertEqual(extension_seconds(prefix, []), 0)


if __name__ == "__main__":
    unittest.main()

"""Safety properties for the actual concave fence and planned 3D routes."""

import math
import unittest

from mission_planner.gcs.geodesy import (
    EAST_MAX_M,
    EAST_MIN_M,
    NORTH_MAX_M,
    NORTH_MIN_M,
    fence_vertices,
    homes,
    point_in_polygon,
    rtl_route,
    segment_distance,
    segment_in_polygon,
    to_geodetic,
    to_local,
)


def point(north, east, altitude=30):
    return {"north_m": north, "east_m": east, "altitude_above_home_m": altitude}


class GcsSafety(unittest.TestCase):
    def test_rtl_climb_cruise_and_landing(self):
        state = {**point(20, 0, 10), "home": point(0, 0, 0)}
        route = rtl_route(state)
        self.assertEqual([p["altitude_above_home_m"] for p in route], [10, 80, 80, 0])
        self.assertEqual(route[1]["north_m"], 20)
        self.assertEqual(route[2]["north_m"], 0)
        self.assertEqual(rtl_route({**state, "north_m": 1})[1]["altitude_above_home_m"], 80)
        self.assertEqual(
            rtl_route({**state, "altitude_above_home_m": 90})[1]["altitude_above_home_m"], 90
        )

    def test_spawn_and_fence_round_trip(self):
        for home in homes().values():
            self.assertTrue(point_in_polygon(home, fence_vertices()))
        for n in (NORTH_MIN_M - 40, NORTH_MIN_M, 0, NORTH_MAX_M):
            for e in (EAST_MIN_M, 0, EAST_MAX_M):
                self.assertLess(math.dist((n, e), to_local(*to_geodetic(n, e))), 1e-6)

    def test_closed_boundary_tolerates_roundoff_but_rejects_real_excursions(self):
        polygon = [point(0, 0), point(1000, 0), point(1000, 1000), point(0, 1000)]
        for north, east in ((math.nextafter(1000, math.inf), 1000), (1000, -1e-12)):
            self.assertTrue(point_in_polygon(point(north, east), polygon))
        for north, east in ((1000 + 1e-7, 500), (500, -1e-7)):
            self.assertFalse(point_in_polygon(point(north, east), polygon))
        self.assertTrue(segment_in_polygon(point(1000, 0), point(1000, 1000), polygon))

    def test_concave_fence(self):
        self.assertTrue(point_in_polygon(point(-830, 24), fence_vertices()))
        self.assertFalse(point_in_polygon(point(-830, 26), fence_vertices()))
        self.assertFalse(point_in_polygon(point(-846, 0), fence_vertices()))
        self.assertTrue(segment_in_polygon(point(-830, 0), point(-790, 10), fence_vertices()))
        self.assertFalse(segment_in_polygon(point(-830, 0), point(-790, 100), fence_vertices()))
        self.assertTrue(segment_in_polygon(point(-790, -800), point(800, 800), fence_vertices()))

    def test_segment_distance(self):
        self.assertEqual(segment_distance((-10, 0, 30), (10, 0, 30), (0, -10, 30), (0, 10, 30)), 0)
        self.assertEqual(segment_distance((-10, 0, 30), (10, 0, 30), (0, -10, 40), (0, 10, 40)), 10)
        self.assertEqual(segment_distance((0, 0, 0), (10, 0, 0), (0, 5, 0), (10, 5, 0)), 5)
        self.assertEqual(segment_distance((0, 0, 0), (0, 0, 0), (0, 5, 0), (0, 5, 0)), 5)

    def test_readback_polygon_geometry(self):
        polygon = [
            dict(
                zip(
                    ("north_m", "east_m"),
                    to_local(
                        *[round(v * 1e7) / 1e7 for v in to_geodetic(p["north_m"], p["east_m"])]
                    ),
                )
            )
            for p in fence_vertices()
        ]
        self.assertTrue(point_in_polygon(point(-830, 0), polygon))
        self.assertFalse(point_in_polygon(point(900, 0), polygon))
        self.assertTrue(segment_in_polygon(point(-830, 0), point(-790, 10), polygon))
        self.assertFalse(segment_in_polygon(point(-830, 0), point(-790, 100), polygon))


if __name__ == "__main__":
    unittest.main()

"""Fence and reservation boundaries retained by the fleet's pure validation API."""

import copy
import unittest
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.gcs.fleet import MAV, Fleet
from mission_planner.gcs.geodesy import ORIGIN, fence_vertices, to_geodetic
from mission_planner.gcs.validation import (
    fence_polygon,
    footprint_intervals,
    minimum_clearance,
    validate_ground_column,
    validate_position,
    validate_route,
    validate_separation,
)


def point(north, east, altitude=30):
    return {"north_m": north, "east_m": east, "altitude_above_home_m": altitude}


def readback():
    return [
        dict(zip(("x", "y"), (round(v * 1e7) for v in to_geodetic(p["north_m"], p["east_m"]))))
        for p in fence_vertices()
    ]


def fleet(states, planned):
    value = Fleet("unused", lambda event: None, [])
    value.vehicles = {state["id"]: {"fence": readback()} for state in states}
    value.planned_routes = planned
    value.snapshot = Mock(
        return_value=[
            {
                **state,
                "home": {
                    "north_m": state["north_m"],
                    "east_m": state["east_m"],
                    "altitude_msl_m": ORIGIN["altitude_msl_m"],
                },
            }
            for state in states
        ]
    )
    value._mode = AsyncMock(side_effect=AssertionError("Flight command reached"))
    return value


class RouteValidation(unittest.TestCase):
    def test_readback_uses_integer_geodetic_coordinates(self):
        self.assertEqual(
            fence_polygon([{"x": 378764320, "y": -1193934210}]),
            [{"north_m": 0, "east_m": 0}],
        )

    def test_altitude_and_launch_corridor_boundaries(self):
        for altitude in (10, 100):
            validate_route([point(-830, 0, altitude), point(-790, 10, altitude)], readback(), 0, [])
        for altitude in (9.999, 100.001):
            with (
                self.subTest(altitude=altitude),
                self.assertRaisesRegex(AssertionError, "Altitude must be 10–100 m above home"),
            ):
                validate_route([point(0, 0, altitude)], readback(), 0, [])
        with self.assertRaisesRegex(
            AssertionError, "Route segment exits the launch corridor fence"
        ):
            validate_route([point(-830, 0), point(-790, 100)], readback(), 0, [])

    def test_empty_route_precedes_fence_decoding(self):
        with self.assertRaises(AssertionError) as error:
            validate_route([], [{"x": 0}], 0, [])
        self.assertEqual(error.exception.args, ())

    def test_point_guards_precede_segment_guards(self):
        route = [point(-830, 0), point(-790, 100), point(0, 0, 101)]
        with self.assertRaisesRegex(AssertionError, "Altitude must be 10–100 m above home"):
            validate_route(route, readback(), 0, [])
        route[-1] = point(900, 0, 101)
        with self.assertRaisesRegex(AssertionError, "Waypoint is outside the search fence"):
            validate_route(route, readback(), 0, [])
        route[-1] = point(float("nan"), 0, 101)
        with self.assertRaises(AssertionError) as error:
            validate_route(route, readback(), 0, [])
        self.assertEqual(error.exception.args, ())

    def test_current_position_uses_readback_and_existing_error(self):
        validate_position("Drone1", point(-830, 0), readback())
        with self.assertRaises(AssertionError) as error:
            validate_position("Drone1", point(-830, 26), readback())
        self.assertEqual(
            str(error.exception),
            "Drone1: current position is outside the search fence; launch position conflicts with required fence",
        )

    def test_crossing_reservation_and_exact_separation(self):
        route = [point(-10, 0), point(10, 0)]
        for offset in (3.5, 10):
            validate_separation(
                "Drone1",
                route,
                [("Drone2", [point(0, -10, 30 + offset), point(0, 10, 30 + offset)])],
            )
        for offset in (0, 3.499):
            with (
                self.subTest(offset=offset),
                self.assertRaisesRegex(
                    AssertionError, "Route conflicts with Drone2; minimum separation 3.5 m"
                ),
            ):
                validate_separation(
                    "Drone1",
                    route,
                    [("Drone2", [point(0, -10, 30 + offset), point(0, 10, 30 + offset)])],
                )

    def test_single_points_and_own_reservations(self):
        route = [point(0, 0)]
        validate_separation("Drone1", route, [("Drone1", route), ("Drone2", [point(3.5, 0)])])
        with self.assertRaisesRegex(AssertionError, "Route conflicts with Drone2"):
            validate_separation("Drone1", route, [("Drone2", [point(3.499, 0)])])

    def test_takeoff_and_landing_columns_reserve_intermediate_altitudes(self):
        for route in ([point(0, 0, 0), point(0, 0, 30)], [point(0, 0, 30), point(0, 0, 0)]):
            with (
                self.subTest(route=route),
                self.assertRaisesRegex(AssertionError, "Route conflicts with Drone2"),
            ):
                validate_separation("Drone1", route, [("Drone2", [point(0, 0, 15)])])

    def test_inputs_are_not_modified(self):
        route, polygon = [point(-10, 0), point(10, 0)], readback()
        reservations = [("Drone1", route), ("Drone2", [point(100, 100)])]
        original = copy.deepcopy((route, polygon, reservations))
        validate_route(route, polygon, 0, [])
        validate_position("Drone1", route[0], polygon)
        validate_separation("Drone1", route, reservations)
        self.assertEqual((route, polygon, reservations), original)

    def test_fleet_checks_route_before_commands(self):
        value = fleet([dict(id="Drone1", **point(0, 0))], {})
        with self.assertRaisesRegex(AssertionError, "Waypoint is outside the search fence"):
            value._validate_route("Drone1", [point(900, 0)])
        value._mode.assert_not_called()

    def test_fleet_empty_route_precedes_vehicle_and_fence_lookup(self):
        value = fleet([dict(id="Drone1", **point(0, 0))], {})
        value.vehicles["Drone1"] = {}
        for name in ("Unknown", "Drone1"):
            with self.subTest(name=name), self.assertRaises(AssertionError) as error:
                value._validate_route(name, [])
            self.assertEqual(error.exception.args, ())
        value.snapshot.assert_not_called()

    def test_fleet_planned_reservations_precede_current_positions(self):
        states = [dict(id="Drone1", **point(-10, 0)), dict(id="Drone2", **point(0, 0))]
        planned = {"Drone3": [point(0, -10), point(0, 10)]}
        value = fleet(states, planned)
        with self.assertRaisesRegex(AssertionError, "Route conflicts with Drone3"):
            value._validate_route("Drone1", [point(-10, 0), point(10, 0)])
        value.planned_routes = {}
        with self.assertRaisesRegex(AssertionError, "Route conflicts with Drone2"):
            value._validate_route("Drone1", [point(-10, 0), point(10, 0)])


class FlightGuardIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_vertical_checks_run_before_takeoff_and_land_commands(self):
        for command, altitude, armed, landed in (
            ("take_off", 0, False, MAV.MAV_LANDED_STATE_ON_GROUND),
            ("land", 30, True, MAV.MAV_LANDED_STATE_IN_AIR),
        ):
            state = dict(
                id="Drone1",
                **point(0, 0, altitude),
                armed=armed,
                landed_state=landed,
                link_lost=False,
                ekf_healthy=True,
                gps_fix=3,
            )
            other = dict(
                id="Drone2", **point(0, 0, 15), armed=True, landed_state=MAV.MAV_LANDED_STATE_IN_AIR
            )
            value = fleet([state, other], {})
            with (
                self.subTest(command=command),
                self.assertRaisesRegex(AssertionError, "Route conflicts with Drone2"),
            ):
                await value.execute(command, {"drone_id": "Drone1", "altitude_above_home_m": 30})
            value._mode.assert_not_called()

    async def test_takeoff_land_and_rtl_reject_raised_ground_columns_before_commands(self):
        for command in ("take_off", "land", "return_home"):
            state = dict(
                id="Drone1",
                **point(0, 0, 0 if command == "take_off" else 40),
                armed=command != "take_off",
                landed_state=MAV.MAV_LANDED_STATE_ON_GROUND
                if command == "take_off"
                else MAV.MAV_LANDED_STATE_IN_AIR,
                link_lost=False,
                ekf_healthy=True,
                gps_fix=3,
            )
            value = fleet([state], {})
            if command == "return_home":
                value.snapshot.return_value[0]["home"].update(north_m=5, east_m=5)
                vertices = [(4, 4), (6, 4), (6, 6), (4, 6)]
            else:
                vertices = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
            value.landmarks = [footprint(vertices, 15)]
            with (
                self.subTest(command=command),
                self.assertRaisesRegex(AssertionError, "Ground column intersects"),
            ):
                await value.execute(command, {"drone_id": "Drone1", "altitude_above_home_m": 40})
            value._mode.assert_not_called()


def footprint(vertices, height):
    return {
        "id": "obstacle",
        "name": "Obstacle",
        "aliases": [],
        "kind": "obstacle",
        "heightM": height,
        "geometry": {
            "type": "polygon",
            "points": [{"x": n * 100, "y": e * 100, "z": 0} for n, e in vertices],
        },
    }


class FootprintClearance(unittest.TestCase):
    def setUp(self):
        world = {"searchBoundsCm": {"min": {"x": -1000, "y": -1000}, "max": {"x": 1000, "y": 1000}}}
        for target, replacement in (
            ("WORLD", world),
            ("ROWS", 3),
            ("COLUMNS", 3),
            ("terrain_height", lambda n, e: 0),
        ):
            patcher = patch("mission_planner.gcs.validation." + target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_narrow_crossing_between_waypoints_checks_full_height(self):
        obstacle = footprint([(0.45, -0.1), (0.46, -0.1), (0.46, 0.1), (0.45, 0.1)], 10)
        for altitude in (20, 30):
            validate_route(
                [point(0, 0, altitude), point(2, 0, altitude)], readback(), 0, [obstacle]
            )
        with self.assertRaisesRegex(AssertionError, "Obstacle is 9.999 m"):
            validate_route([point(0, 0, 19.999), point(2, 0, 19.999)], readback(), 0, [obstacle])

    def test_ground_columns_include_footprint_boundary_and_permit_zero_height(self):
        obstacle = footprint([(0, 0), (1, 0), (1, 1), (0, 1)], 1)
        for p in (point(0, 0), point(0.5, 0.5)):
            with self.subTest(point=p), self.assertRaisesRegex(AssertionError, "Ground column"):
                validate_ground_column(p, [obstacle])
        validate_ground_column(point(-0.01, 0), [obstacle])
        obstacle["heightM"] = 0
        validate_ground_column(point(0.5, 0.5), [obstacle])
        with self.assertRaisesRegex(AssertionError, "Landmark catalogue required"):
            validate_ground_column(point(0, 0), None)

    def test_zero_height_polygon_and_home_altitude_are_included(self):
        pond = footprint([(-1, -1), (1, -1), (1, 1), (-1, 1)], 0)
        pond["kind"] = "pond"
        with patch("mission_planner.gcs.validation.terrain_height", lambda n, e: 15):
            validate_route([point(0, 0, 20)], readback(), 5, [pond])
            with self.assertRaisesRegex(AssertionError, "requires 10 m"):
                validate_route([point(0, 0, 20)], readback(), 4.999, [pond])

    def test_vertical_look_and_boundary_tangency_are_checked(self):
        obstacle = footprint([(0, 0), (1, 0), (1, 1), (0, 1)], 10)
        routes = (
            [point(0.5, 0.5, 30), point(0.5, 0.5, 19)],
            [point(-1, 1, 19), point(1, -1, 19)],
            [point(0, -1, 19), point(0, 2, 19)],
        )
        for route in routes:
            with self.subTest(route=route), self.assertRaisesRegex(AssertionError, "clearance"):
                validate_route(route, readback(), 0, [obstacle])

    def test_concave_footprint_preserves_disjoint_occupied_intervals(self):
        polygon = [
            {"north_m": n, "east_m": e}
            for n, e in ((-2, -2), (2, -2), (2, 2), (1, 2), (1, -1), (-1, -1), (-1, 2), (-2, 2))
        ]
        intervals = footprint_intervals(point(-3, 0), point(3, 0), polygon)
        self.assertEqual([(a, b) for a, b in intervals if a != b], [(1 / 6, 2 / 6), (4 / 6, 5 / 6)])

    def test_interior_quadratic_minimum_between_endpoints_and_midpoint(self):
        obstacle = footprint([(0, 0), (1, 0), (1, 1), (0, 1)], 0)
        with patch("mission_planner.gcs.validation.terrain_height", lambda n, e: 16 * n * (1 - e)):
            route = [point(0, 0, 11.4), point(1, 1, 17.8)]
            self.assertAlmostEqual(minimum_clearance(*route, 0, 1, 0, 0), 9.96)
            with self.assertRaisesRegex(AssertionError, "9.960 m"):
                validate_route(route, readback(), 0, [obstacle])

    def test_heightfield_cell_boundaries_split_the_leg(self):
        world = {"searchBoundsCm": {"min": {"x": 0, "y": 0}, "max": {"x": 200, "y": 200}}}
        with (
            patch("mission_planner.gcs.validation.WORLD", world),
            patch("mission_planner.gcs.validation.ROWS", 5),
            patch("mission_planner.gcs.validation.COLUMNS", 5),
            patch(
                "mission_planner.gcs.validation.terrain_height",
                lambda n, e: 10 * max(0, 1 - abs(n - 0.5) / 0.5),
            ),
        ):
            self.assertAlmostEqual(
                minimum_clearance(point(0, 0, 20), point(2, 0, 20), 0, 1, 0, 0), 10
            )

    def test_rectangular_heightfield_splits_north_columns_and_east_rows(self):
        world = {"searchBoundsCm": {"min": {"x": 0, "y": 0}, "max": {"x": 400, "y": 400}}}
        with (
            patch("mission_planner.gcs.validation.WORLD", world),
            patch("mission_planner.gcs.validation.ROWS", 3),
            patch("mission_planner.gcs.validation.COLUMNS", 5),
            patch(
                "mission_planner.gcs.validation.terrain_height",
                lambda n, e: 8 * max(0, 1 - abs(n - 1)) * (1 - e / 4),
            ),
        ):
            self.assertAlmostEqual(
                minimum_clearance(point(0, 0, 20), point(4, 4, 20), 0, 1, 0, 0), 14
            )

    def test_missing_catalogue_blocks_flight_and_nonintersecting_obstacle_does_not(self):
        with self.assertRaisesRegex(AssertionError, "Landmark data unavailable"):
            validate_route([point(0, 0)], readback(), 0, None)
        obstacle = footprint([(1, 1), (2, 1), (2, 2), (1, 2)], 90)
        validate_route([point(0, 0)], readback(), 0, [obstacle])

    def test_fleet_route_guard_rejects_obstacle_before_any_command(self):
        value = fleet([dict(id="Drone1", **point(0, 0, 30))], {})
        value.landmarks = [footprint([(0.45, -0.1), (0.46, -0.1), (0.46, 0.1), (0.45, 0.1)], 15)]
        with self.assertRaisesRegex(AssertionError, "requires 10 m"):
            value._validate_route("Drone1", [point(0, 0, 20), point(2, 0, 20)])
        value._mode.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Fence and reservation boundaries retained by the fleet's pure validation API."""

import copy
import unittest
from unittest.mock import AsyncMock, Mock

from mission_planner.gcs.fleet import MAV, Fleet
from mission_planner.gcs.geodesy import fence_vertices, to_geodetic
from mission_planner.gcs.validation import (
    fence_polygon,
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
    value = Fleet("unused", lambda event: None)
    value.vehicles = {state["id"]: {"fence": readback()} for state in states}
    value.planned_routes = planned
    value.snapshot = Mock(return_value=states)
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
            validate_route([point(-830, 0, altitude), point(-790, 10, altitude)], readback())
        for altitude in (9.999, 100.001):
            with (
                self.subTest(altitude=altitude),
                self.assertRaisesRegex(AssertionError, "Altitude must be 10–100 m above home"),
            ):
                validate_route([point(0, 0, altitude)], readback())
        with self.assertRaisesRegex(
            AssertionError, "Route segment exits the launch corridor fence"
        ):
            validate_route([point(-830, 0), point(-790, 100)], readback())

    def test_empty_route_precedes_fence_decoding(self):
        with self.assertRaises(AssertionError) as error:
            validate_route([], [{"x": 0}])
        self.assertEqual(error.exception.args, ())

    def test_point_guards_precede_segment_guards(self):
        route = [point(-830, 0), point(-790, 100), point(0, 0, 101)]
        with self.assertRaisesRegex(AssertionError, "Altitude must be 10–100 m above home"):
            validate_route(route, readback())
        route[-1] = point(900, 0, 101)
        with self.assertRaisesRegex(AssertionError, "Waypoint is outside the search fence"):
            validate_route(route, readback())
        route[-1] = point(float("nan"), 0, 101)
        with self.assertRaises(AssertionError) as error:
            validate_route(route, readback())
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
        validate_route(route, polygon)
        validate_position("Drone1", route[0], polygon)
        validate_separation("Drone1", route, reservations)
        self.assertEqual((route, polygon, reservations), original)

    def test_fleet_checks_route_before_snapshot(self):
        value = fleet([dict(id="Drone1", **point(0, 0))], {})
        value.snapshot.side_effect = RuntimeError("Snapshot must not be read")
        with self.assertRaisesRegex(AssertionError, "Waypoint is outside the search fence"):
            value._validate_route("Drone1", [point(900, 0)])
        value.snapshot.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()

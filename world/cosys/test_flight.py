"""Calibration frame translation and command-failure semantics."""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from world.cosys import flight


def fleet_at(altitude=10):
    current = {
        "id": "Drone2",
        "north_m": 12,
        "east_m": -23,
        "altitude_above_home_m": altitude,
        "home": {"altitude_msl_m": 2600.23},
        "link_lost": False,
        "ekf_healthy": True,
        "gps_fix": 3,
        "ground_speed_mps": 0,
        "climb_rate_mps": 0,
    }
    fleet = Mock()
    fleet.snapshot.return_value = [current]
    fleet.execute = AsyncMock()
    return fleet


class FlightTests(unittest.IsolatedAsyncioTestCase):
    def test_world_height_uses_this_aircraft_home_msl(self):
        state = fleet_at(50.55).snapshot()[0]
        world = flight.world_position(state)
        self.assertEqual((world["x"], world["y"]), (12, -23))
        self.assertAlmostEqual(world["z"], 29.5)
        with patch.object(flight, "validate_waypoint"):
            target = flight.waypoint(world, state)
        self.assertAlmostEqual(target["altitude_above_home_m"], 50.55)
        state["home"]["altitude_msl_m"] += 8
        with patch.object(flight, "validate_waypoint"):
            target = flight.waypoint(world, state)
        self.assertAlmostEqual(target["altitude_above_home_m"], 42.55)

    async def test_denied_fleet_guard_propagates_without_second_command(self):
        fleet = fleet_at()
        fleet.execute.side_effect = ValueError("Route crosses the fence")
        with patch.object(flight, "validate_waypoint"):
            with self.assertRaisesRegex(ValueError, "crosses the fence"):
                await flight.move(fleet, "Drone2", {"x": 100, "y": -40, "z": 65}, 5)
        fleet.execute.assert_awaited_once()
        action, args = fleet.execute.await_args.args
        self.assertEqual(action, "fly_to")
        self.assertEqual((args["north_m"], args["east_m"]), (100, -40))
        self.assertAlmostEqual(args["altitude_above_home_m"], 86.05)

    async def test_waypoint_timeout_never_reissues_or_claims_arrival(self):
        fleet = fleet_at()
        with (
            patch.object(flight, "validate_waypoint"),
            patch.object(flight.time, "monotonic", side_effect=[0, 121]),
        ):
            with self.assertRaisesRegex(TimeoutError, "stationary waypoint arrival"):
                await flight.move(fleet, "Drone2", {"x": 100, "y": -40, "z": 65}, 5)
        fleet.execute.assert_awaited_once()

    async def test_link_loss_prevents_command(self):
        fleet = fleet_at()
        fleet.snapshot()[0]["link_lost"] = True
        with self.assertRaisesRegex(AssertionError, "link lost"):
            await flight.move(fleet, "Drone2", {"x": 100, "y": -40, "z": 65}, 5)
        fleet.execute.assert_not_awaited()

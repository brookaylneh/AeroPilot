"""Explicit heading changes preserve flight and fail on stale mission state."""

import asyncio
import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.gcs.fleet import MAV, Fleet
from mission_planner.gcs.test_validation import readback


class InspectionHeading(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {
            "id": "Drone1",
            "link_lost": False,
            "ekf_healthy": True,
            "gps_fix": 3,
            "armed": True,
            "camera_available": True,
            "landed_state": MAV.MAV_LANDED_STATE_IN_AIR,
            "mode": "AUTO",
            "ground_speed_mps": 0,
            "climb_rate_mps": 0,
            "heading_deg": 0,
            "north_m": -730,
            "east_m": 0,
            "altitude_above_home_m": 30,
            "mission_current": 1,
        }
        self.vehicle = {
            "fence": readback(),
            "mission": [
                {"command": MAV.MAV_CMD_NAV_WAYPOINT},
                {"command": MAV.MAV_CMD_NAV_LOITER_UNLIM},
            ],
        }
        with patch.dict(os.environ, {"SNR_FENCES_PATH": "/nonexistent/flight-fences.json"}):
            self.fleet = Fleet("unused", Mock(), [])
        self.fleet.fences_verified = True
        self.fleet.vehicles = {"Drone1": self.vehicle}
        self.fleet.snapshot = lambda: [copy.deepcopy(self.state)]
        self.fleet._command = AsyncMock()
        self.fleet._mode = AsyncMock()

    async def test_acknowledgement_is_followed_by_measured_wrapped_heading(self):
        async def until(name, predicate, timeout):
            self.assertFalse(self.fleet.command_lock.locked())
            self.assertFalse(predicate())
            self.state["heading_deg"] = 3.01
            self.assertFalse(predicate())
            self.state["heading_deg"] = 359
            self.assertTrue(predicate())

        self.state["heading_deg"] = 90
        self.fleet._until = AsyncMock(side_effect=until)
        result = await self.fleet.face_heading("Drone1", 1)
        self.fleet._command.assert_awaited_once_with(
            "Drone1", MAV.MAV_CMD_CONDITION_YAW, [1, 0, 0, 0, 0, 0, 0]
        )
        self.assertEqual(result["heading_deg"], 359)
        self.fleet._mode.assert_not_awaited()

    async def test_invalid_requested_heading_never_sends_a_command(self):
        for heading in (True, None, "90", -1, 360, float("nan"), float("inf")):
            with self.subTest(heading=heading), self.assertRaises(AssertionError):
                await self.fleet.face_heading("Drone1", heading)
        with self.assertRaisesRegex(AssertionError, "Unknown drone"):
            await self.fleet.face_heading("Drone5", 90)
        self.fleet._command.assert_not_awaited()

    async def test_heading_change_does_not_stop_moving_aircraft(self):
        self.state.update(ground_speed_mps=8, climb_rate_mps=1.5, mission_current=0)

        async def until(name, predicate, timeout):
            self.assertFalse(self.fleet.command_lock.locked())
            self.state["heading_deg"] = 90
            self.assertTrue(predicate())

        self.fleet._until = AsyncMock(side_effect=until)
        result = await self.fleet.face_heading("Drone1", 90)
        self.assertEqual(result["ground_speed_mps"], 8)
        self.assertEqual(result["climb_rate_mps"], 1.5)
        self.assertEqual(result["mode"], "AUTO")
        self.fleet._command.assert_awaited_once_with(
            "Drone1", MAV.MAV_CMD_CONDITION_YAW, [90, 0, 0, 0, 0, 0, 0]
        )
        self.fleet._mode.assert_not_awaited()

    async def test_normal_hover_motion_is_not_a_heading_failure(self):
        self.state.update(ground_speed_mps=0.25, climb_rate_mps=-0.3, heading_deg=90)
        result = await self.fleet.face_heading("Drone1", 90)
        self.assertEqual(result["heading_deg"], 90)
        self.fleet._command.assert_awaited_once()
        self.fleet._mode.assert_not_awaited()

    async def test_unready_aircraft_never_receives_a_heading_command(self):
        original = copy.deepcopy(self.state)
        cases = [
            ({"link_lost": True}, "link lost"),
            ({"ekf_healthy": False}, "GPS/EKF"),
            ({"gps_fix": 2}, "GPS/EKF"),
            ({"armed": False}, "airborne"),
            ({"landed_state": MAV.MAV_LANDED_STATE_LANDING}, "airborne"),
            ({"mode": "BRAKE"}, "AUTO flight"),
            ({"camera_available": False}, "verified uploaded mission"),
            ({"heading_deg": None}, "heading telemetry"),
            ({"heading_deg": float("nan")}, "heading telemetry"),
            ({"heading_deg": 360}, "heading telemetry"),
            ({"mission_current": None}, "mission item"),
            ({"mission_current": 2}, "mission item"),
            ({"altitude_above_home_m": 9}, "Altitude"),
            ({"north_m": 10000}, "fence"),
        ]
        for fields, error in cases:
            with self.subTest(fields=fields), self.assertRaisesRegex(AssertionError, error):
                self.state = {**original, **fields}
                await self.fleet.face_heading("Drone1", 90)
        self.fleet._command.assert_not_awaited()

    async def test_rejected_command_is_not_retried(self):
        self.fleet._command = Fleet._command.__get__(self.fleet)
        self.fleet._expect = AsyncMock(return_value=SimpleNamespace(result=MAV.MAV_RESULT_DENIED))
        self.fleet._until = AsyncMock()
        with self.assertRaisesRegex(AssertionError, "rejected"):
            await self.fleet.face_heading("Drone1", 90)
        self.assertEqual(self.fleet._expect.await_count, 1)
        self.fleet._until.assert_not_awaited()

    async def test_heading_timeout_is_not_retried(self):
        with (
            patch("mission_planner.gcs.fleet.time.monotonic", side_effect=[0, 31]),
            self.assertRaisesRegex(TimeoutError, "timed out"),
        ):
            await self.fleet.face_heading("Drone1", 90)
        self.assertEqual(self.fleet._command.await_count, 1)
        self.fleet._mode.assert_not_awaited()

    async def test_mode_or_mission_change_interrupts_heading_wait(self):
        for change in ("mode", "sequence", "mission", "link_lost"):
            with self.subTest(change=change):
                self.setUp()

                async def until(name, predicate, timeout):
                    if change == "mode":
                        self.state["mode"] = "RTL"
                    elif change == "sequence":
                        self.state["mission_current"] = 0
                    elif change == "link_lost":
                        self.state["link_lost"] = True
                    else:
                        self.vehicle["mission"] = copy.deepcopy(self.vehicle["mission"])
                    predicate()

                self.fleet._until = AsyncMock(side_effect=until)
                with self.assertRaises(AssertionError):
                    await self.fleet.face_heading("Drone1", 90)
                self.assertEqual(self.fleet._command.await_count, 1)
                self.fleet._mode.assert_not_awaited()

    async def test_cancellation_releases_the_fleet_command_lock(self):
        waiting = asyncio.Event()

        async def until(name, predicate, timeout):
            waiting.set()
            await asyncio.Event().wait()

        self.fleet._until = AsyncMock(side_effect=until)
        task = asyncio.create_task(self.fleet.face_heading("Drone1", 90))
        await waiting.wait()
        async with self.fleet.command_lock:
            task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.fleet._command.await_count, 1)
        self.fleet._mode.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

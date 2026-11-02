"""MAVLink course and verified-mission camera lifetime."""

import asyncio
import copy
import os
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.environment import ROOT
from mission_planner.gcs.fleet import MAV, Fleet, course_degrees
from mission_planner.gcs.geodesy import NAMES, homes
from mission_planner.gcs.planner import mission_home, mission_item, mission_readback


class Course(unittest.TestCase):
    def test_north_east_velocity_gives_clockwise_compass_bearings(self):
        for north, east, degrees in (
            (100, 0, 0),
            (100, 100, 45),
            (0, 100, 90),
            (-100, 100, 135),
            (-100, 0, 180),
            (-100, -100, 225),
            (0, -100, 270),
            (100, -100, 315),
        ):
            with self.subTest(north=north, east=east):
                self.assertEqual(course_degrees(north, east), degrees)

    def test_stopped_threshold_uses_horizontal_speed_in_metres_per_second(self):
        for north, east in ((0, 0), (19, 0), (0, -19), (14, 14)):
            with self.subTest(north=north, east=east):
                self.assertIsNone(course_degrees(north, east))
        self.assertEqual(course_degrees(20, 0), 0)
        self.assertEqual(course_degrees(0, -20), 270)
        self.assertEqual(course_degrees(15, 15), 45)

    def test_velocity_rejects_invalid_mavlink_values(self):
        for value in (0.5, float("nan"), True, 32768, -32769):
            with self.subTest(value=value), self.assertRaisesRegex(AssertionError, "int16"):
                course_degrees(value, 0)


class CameraLifetime(unittest.IsolatedAsyncioTestCase):
    def heartbeat(self, armed, mode="STABILIZE"):
        return SimpleNamespace(
            base_mode=MAV.MAV_MODE_FLAG_SAFETY_ARMED if armed else 0,
            mode=mode,
            get_type=lambda: "HEARTBEAT",
            get_srcSystem=lambda: 1,
            get_srcComponent=lambda: 1,
        )

    def setUp(self):
        home = homes()["Drone1"]
        latitude, longitude = (round(home[k] * 1e7) for k in ("latitude_deg", "longitude_deg"))
        now = time.monotonic()
        self.fleet = Fleet("unused", Mock(), [])
        self.vehicle = {
            "connection": SimpleNamespace(mav=Mock(), recv_match=Mock()),
            "process": SimpleNamespace(returncode=None),
            "sysid": 1,
            "bootloader_preamble_count": 3,
            "messages": {
                "HEARTBEAT": self.heartbeat(False),
                "GLOBAL_POSITION_INT": SimpleNamespace(
                    lat=latitude, lon=longitude, relative_alt=0, hdg=0, vx=0, vy=0, vz=0
                ),
                "SYS_STATUS": SimpleNamespace(battery_remaining=100, voltage_battery=12600),
                "GPS_RAW_INT": SimpleNamespace(fix_type=3, satellites_visible=10),
                "HOME_POSITION": SimpleNamespace(
                    latitude=latitude,
                    longitude=longitude,
                    altitude=home["altitude_msl_m"] * 1000,
                ),
                "EXTENDED_SYS_STATE": SimpleNamespace(landed_state=MAV.MAV_LANDED_STATE_ON_GROUND),
                "EKF_STATUS_REPORT": SimpleNamespace(flags=0),
            },
            "times": {"HEARTBEAT": now, "GLOBAL_POSITION_INT": now},
            "positions": deque([(now, time.time() * 1000)]),
            "mode": "STABILIZE",
            "pending": [],
            "mission": [],
            "mission_speed_mps": 5,
            "fence": [],
            "status_text": [],
            "camera_available": False,
        }
        self.fleet.vehicles = {"Drone1": self.vehicle}
        self.items = [mission_home(home), mission_item(home, 30, MAV.MAV_CMD_NAV_TAKEOFF)]
        self.received = [
            {"seq": index, **mission_readback(item, home=index == 0)}
            for index, item in enumerate(self.items)
        ]
        self.fleet._upload = AsyncMock()
        self.fleet._download = AsyncMock(return_value=self.received)

    async def receive(self, *messages):
        self.vehicle["connection"].recv_match.side_effect = [*messages, None]
        with (
            patch(
                "mission_planner.gcs.fleet.mavutil.mode_string_v10", lambda message: message.mode
            ),
            patch(
                "mission_planner.gcs.fleet.asyncio.sleep",
                AsyncMock(side_effect=asyncio.CancelledError),
            ),
            self.assertRaises(asyncio.CancelledError),
        ):
            await self.fleet._receive("Drone1")

    async def test_verified_upload_enables_before_arm_and_through_flight_modes(self):
        async def download(*args):
            self.assertFalse(self.fleet.snapshot()[0]["camera_available"])
            return self.received

        self.fleet._download.side_effect = download
        await self.fleet.upload_mission("Drone1", self.items)
        self.assertEqual(self.vehicle["mission"], self.received)
        self.assertTrue(self.fleet.snapshot()[0]["camera_available"])
        await self.receive(self.heartbeat(False))
        self.assertTrue(self.fleet.snapshot()[0]["camera_available"])
        for mode in ("AUTO", "BRAKE", "RTL", "LAND"):
            with self.subTest(mode=mode):
                await self.receive(self.heartbeat(True, mode))
                self.assertTrue(self.fleet.snapshot()[0]["camera_available"])

    async def test_disarm_closes_camera_until_another_verified_upload(self):
        await self.fleet.upload_mission("Drone1", self.items)
        await self.receive(self.heartbeat(True, "LAND"), self.heartbeat(False))
        self.assertFalse(self.fleet.snapshot()[0]["camera_available"])
        await self.receive(self.heartbeat(True))
        self.assertFalse(self.fleet.snapshot()[0]["camera_available"])
        await self.fleet.upload_mission("Drone1", self.items)
        self.assertTrue(self.fleet.snapshot()[0]["camera_available"])

    async def test_gimbal_heartbeat_cannot_replace_aircraft_arming_or_flight_mode(self):
        original = self.vehicle["messages"]["HEARTBEAT"]
        gimbal = self.heartbeat(True, "GUIDED")
        gimbal.get_srcComponent = lambda: MAV.MAV_COMP_ID_GIMBAL
        await self.receive(gimbal)
        self.assertIs(self.vehicle["messages"]["HEARTBEAT"], original)
        self.assertFalse(self.fleet.snapshot()[0]["armed"])

    async def test_failed_uploads_preserve_previous_camera_and_mission(self):
        for available in (False, True):
            for failure in ("upload", "download", "count", "field"):
                with self.subTest(available=available, failure=failure):
                    self.vehicle["camera_available"] = available
                    self.vehicle["messages"]["HEARTBEAT"] = self.heartbeat(available)
                    previous = self.vehicle["mission"] = (
                        copy.deepcopy(self.received) if available else []
                    )
                    received = copy.deepcopy(self.received)
                    if failure == "count":
                        received.pop()
                    if failure == "field":
                        received[1]["z"] += 1
                    self.fleet._upload = AsyncMock(
                        side_effect=TimeoutError("upload timed out")
                        if failure == "upload"
                        else None
                    )
                    self.fleet._download = AsyncMock(
                        side_effect=TimeoutError("download timed out")
                        if failure == "download"
                        else None,
                        return_value=received,
                    )
                    error, message = (
                        (TimeoutError, "timed out")
                        if failure in ("upload", "download")
                        else (AssertionError, "mismatch")
                    )
                    with self.assertRaisesRegex(error, message):
                        await self.fleet.upload_mission("Drone1", self.items)
                    self.assertIs(self.vehicle["mission"], previous)
                    self.assertEqual(self.fleet.snapshot()[0]["camera_available"], available)
                    self.assertEqual(self.fleet._upload.await_count, 1)
                    self.assertEqual(self.fleet._download.await_count, failure != "upload")

    async def test_disarm_during_readback_cannot_reopen_camera(self):
        self.vehicle["camera_available"] = True
        self.vehicle["messages"]["HEARTBEAT"] = self.heartbeat(True, "BRAKE")

        async def download(*args):
            await self.receive(self.heartbeat(False))
            self.assertFalse(self.fleet.snapshot()[0]["camera_available"])
            return self.received

        self.fleet._download.side_effect = download
        await self.fleet.upload_mission("Drone1", self.items)
        self.assertFalse(self.fleet.snapshot()[0]["camera_available"])

    async def test_home_item_alone_does_not_enable_camera(self):
        self.fleet._download.return_value = self.received[:1]
        await self.fleet.upload_mission("Drone1", self.items[:1])
        self.assertFalse(self.fleet.snapshot()[0]["camera_available"])

    async def test_camera_clear_requires_acknowledgement_and_empty_readback(self):
        self.vehicle["camera_available"] = True
        self.vehicle["mission"] = self.received
        self.fleet._expect = AsyncMock(return_value=SimpleNamespace(type=MAV.MAV_MISSION_ERROR))
        with self.assertRaisesRegex(AssertionError, "mission clear rejected"):
            await self.fleet.clear_missions()
        self.assertTrue(self.fleet.snapshot()[0]["camera_available"])
        self.fleet._download.assert_not_awaited()
        self.fleet._expect.return_value = SimpleNamespace(type=MAV.MAV_MISSION_ACCEPTED)
        with self.assertRaisesRegex(AssertionError, "mission clear readback is not empty"):
            await self.fleet.clear_missions()
        self.assertTrue(self.fleet.snapshot()[0]["camera_available"])
        self.assertEqual(self.vehicle["mission"], self.received)
        self.fleet._download.return_value = []
        await self.fleet.clear_missions()
        self.assertFalse(self.fleet.snapshot()[0]["camera_available"])
        self.assertEqual(self.vehicle["mission"], [])

    async def test_startup_retained_missions_do_not_enable_cameras(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as directory:
            fleet = Fleet(Path(directory) / "fleet", Mock(), [])
            fleet.fences_path = Path(directory) / "fences.json"
            aircraft = [
                {
                    "name": name,
                    "home": home,
                    "arguments": [],
                    "ports": [],
                    "mavlink_port": 14550 + index,
                    "expected_parameters": {},
                }
                for index, (name, home) in enumerate(homes().items())
            ]

            async def initialize(name):
                fleet.vehicles[name]["mission"] = copy.deepcopy(self.received)

            fleet._initialize = AsyncMock(side_effect=initialize)
            fleet._receive = AsyncMock()
            fleet.snapshot = Mock(
                return_value=[
                    {"id": name, "update_rate_hz": 10, "home": home}
                    for name, home in homes().items()
                ]
            )
            with (
                patch.dict(os.environ, {"SNR_SITL_MODEL": "quad"}),
                patch("mission_planner.gcs.fleet.load_env"),
                patch("mission_planner.gcs.fleet.launches", return_value=aircraft),
                patch("mission_planner.gcs.fleet.check_ports", return_value={}),
                patch("mission_planner.gcs.fleet.mavutil.mavlink_connection"),
                patch(
                    "mission_planner.gcs.fleet.asyncio.create_subprocess_exec",
                    AsyncMock(return_value=SimpleNamespace(pid=1)),
                ),
                patch("mission_planner.gcs.fleet.asyncio.sleep", AsyncMock()),
            ):
                try:
                    await fleet.start()
                    self.assertEqual(set(fleet.vehicles), set(NAMES))
                    for vehicle in fleet.vehicles.values():
                        self.assertEqual(vehicle["mission"], self.received)
                        self.assertFalse(vehicle["camera_available"])
                finally:
                    await asyncio.gather(*fleet.tasks)
                    for vehicle in fleet.vehicles.values():
                        vehicle["logfile"].close()

    def test_snapshot_requires_boolean_camera_availability(self):
        self.vehicle["camera_available"] = 1
        with self.assertRaises(AssertionError):
            self.fleet.snapshot()


if __name__ == "__main__":
    unittest.main()

"""A service retains its fleet across distinct in-memory missions."""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.gcs.fleet import MAV, Fleet
from mission_planner.gcs.host import connection
from mission_planner.gcs.service import Mission, validate_launch
from world.cosys.terrain import BOUNDS


def launch(brief="A hiker, 34 years old, missing for about 3 hours."):
    return {
        "type": "launch",
        "schemaVersion": 2,
        "report": {"brief": brief, "portrait": ""},
        "world": {
            "coordinateSystem": "UE_LH_Z_UP_CENTIMETERS",
            "searchBoundsCm": copy.deepcopy(BOUNDS),
        },
    }


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fleet = Mock()
        self.fleet.snapshot.return_value = [{"id": "Drone1", "armed": False, "landed_state": 1}]
        self.fleet.execute = AsyncMock()
        self.fleet.close = AsyncMock()
        self.fleet.clear_missions = AsyncMock()
        patches = [
            patch("mission_planner.gcs.service.ROOT", Path(self.temporary.name)),
            patch.dict("os.environ", {"SNR_SITL_MODEL": "airsim-copter"}),
            patch("mission_planner.gcs.service.Wonder"),
            patch(
                "mission_planner.gcs.service.Search",
                side_effect=lambda mission: SimpleNamespace(
                    landmarks=[], commander=SimpleNamespace(close=AsyncMock())
                ),
            ),
            patch("mission_planner.gcs.service.Fleet", return_value=self.fleet),
            patch(
                "mission_planner.gcs.service.MissionSensor",
                side_effect=lambda *args: SimpleNamespace(
                    start=AsyncMock(), close=AsyncMock(), check=Mock()
                ),
            ),
            patch("mission_planner.gcs.service.sim.enter_mission"),
            patch("mission_planner.gcs.service.sim.reset_session"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.mission = Mission(None)
        self.mission.fleet_ready = True
        self.addAsyncCleanup(self.mission.close)

    async def test_launch_rejects_second_and_reset_allows_distinct_run(self):
        first = await self.mission.begin(launch())
        directory = self.mission.directory
        self.mission.sensor_frames["Drone1"] = {"private": "old frame"}
        self.mission.agent_busy = True
        self.mission.ground_team.deliveries["old"] = {}
        with self.assertRaisesRegex(AssertionError, "already running"):
            await self.mission.begin(launch("Another visitor"))
        await self.mission.clear()
        self.assertIsNone(self.mission.report)
        self.assertEqual(self.mission.sensor_frames, {})
        self.assertFalse(self.mission.agent_busy)
        self.assertFalse(self.mission.ground_team.deliveries)
        self.fleet.close.assert_not_called()
        second = await self.mission.begin(launch("Second visitor's edited text"))
        self.assertNotEqual(first, second)
        self.assertNotEqual(directory, self.mission.directory)
        self.assertEqual(self.mission.report["brief"], "Second visitor's edited text")
        self.assertIn("Mission ended", (directory / "events.jsonl").read_text())
        self.assertEqual(self.mission.events[0]["text"], "Mission launched")

    async def test_in_flight_reset_returns_home_before_clearing(self):
        await self.mission.begin(launch())
        self.fleet.snapshot.return_value[0]["armed"] = True
        self.fleet.snapshot.return_value[0]["landed_state"] = 2

        async def landed(name, arguments):
            self.assertEqual(name, "return_home")
            self.assertIsNotNone(self.mission.report)
            self.fleet.snapshot.return_value[0].update(armed=False, landed_state=1)

        self.fleet.execute.side_effect = landed
        await self.mission.clear()
        self.fleet.execute.assert_awaited_once_with("return_home", {"drone_id": "Drone1"})
        self.assertIsNone(self.mission.report)

    async def test_idle_connect_and_launch_acknowledgement_use_no_case_identifier(self):
        def socket(message):
            return SimpleNamespace(
                request=SimpleNamespace(path="/"),
                recv=AsyncMock(return_value=json.dumps(message)),
                send=AsyncMock(),
            )

        idle = socket({"type": "connect"})
        await connection(self.mission, idle)
        self.assertEqual(json.loads(idle.send.call_args.args[0]), {"type": "idle"})
        first = socket(launch())
        await connection(self.mission, first)
        self.assertEqual(
            json.loads(first.send.call_args.args[0]),
            {"type": "launched", "run_id": self.mission.id},
        )
        second = socket(launch())
        await connection(self.mission, second)
        self.assertIn("already running", json.loads(second.send.call_args.args[0])["message"])

    async def test_concurrent_launch_has_one_winner(self):
        results = await asyncio.gather(
            self.mission.begin(launch()), self.mission.begin(launch()), return_exceptions=True
        )
        self.assertEqual(sum(isinstance(result, str) for result in results), 1)
        self.assertEqual(sum(isinstance(result, AssertionError) for result in results), 1)

    def test_launch_rejects_private_fields_legacy_schema_and_changed_bounds(self):
        values = []
        for field, value in (("schemaVersion", 1), ("case_id", "saved"), ("position", [1, 2, 3])):
            message = launch()
            message[field] = value
            values.append(message)
        message = launch()
        message["world"]["searchBoundsCm"]["max"]["x"] += 1
        values.append(message)
        message = launch()
        message["report"]["situationId"] = "lost"
        values.append(message)
        for value in values:
            with self.assertRaises(AssertionError):
                validate_launch(value)


class FleetReset(unittest.IsolatedAsyncioTestCase):
    async def test_clear_requires_landing_acknowledgement_and_empty_readback(self):
        fleet = Fleet("unused", Mock(), [])
        state = {"id": "Drone1", "armed": False, "landed_state": MAV.MAV_LANDED_STATE_ON_GROUND}
        fleet.snapshot = Mock(return_value=[state])
        vehicle = {
            "connection": SimpleNamespace(mav=Mock()),
            "sysid": 1,
            "mission": ["old route"],
            "mission_speed_mps": 5,
            "fence": ["unchanged"],
        }
        fleet.vehicles = {"Drone1": vehicle}

        async def expect(name, message, predicate, send):
            self.assertEqual((name, message), ("Drone1", "MISSION_ACK"))
            self.assertFalse(predicate(SimpleNamespace(mission_type=MAV.MAV_MISSION_TYPE_FENCE)))
            reply = SimpleNamespace(
                mission_type=MAV.MAV_MISSION_TYPE_MISSION, type=MAV.MAV_MISSION_ACCEPTED
            )
            self.assertTrue(predicate(reply))
            send()
            return reply

        fleet._expect = AsyncMock(side_effect=expect)
        fleet._download = AsyncMock(return_value=[])
        state["armed"] = True
        with self.assertRaisesRegex(AssertionError, "Land the fleet"):
            await fleet.clear_missions()
        fleet._expect.assert_not_awaited()
        state["armed"] = False
        fleet._download.return_value = ["old route"]
        with self.assertRaisesRegex(AssertionError, "not empty"):
            await fleet.clear_missions()
        self.assertEqual(vehicle["mission"], ["old route"])
        fleet._download.return_value = []
        await fleet.clear_missions()
        self.assertEqual(vehicle["mission"], [])
        self.assertIsNone(vehicle["mission_speed_mps"])
        self.assertEqual(vehicle["fence"], ["unchanged"])
        self.assertEqual(fleet.planned_routes, {"Drone1": [state]})
        vehicle["connection"].mav.mission_clear_all_send.assert_called_with(
            1, 1, MAV.MAV_MISSION_TYPE_MISSION
        )

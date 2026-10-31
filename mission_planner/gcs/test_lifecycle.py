"""Launch seals a saved scenario; returning to Studio cancels without stopping the fleet."""

import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.gcs.fleet import MAV, Fleet
from mission_planner.gcs.host import connection
from mission_planner.gcs.service import Mission


def saved_case(brief="A hiker, 34 years old, missing for about 3 hours."):
    return {
        "id": str(uuid.uuid4()),
        "name": "Saved incident",
        "report": {"brief": brief, "portrait": ""},
    }


def socket(message):
    return Mock(
        request=SimpleNamespace(path="/"),
        recv=AsyncMock(return_value=json.dumps(message)),
        send=AsyncMock(),
        close=AsyncMock(),
    )


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
        self.enter_mission = patches[-2].start()
        self.reset_session = patches[-1].start()
        self.addCleanup(patches[-2].stop)
        self.addCleanup(patches[-1].stop)
        self.cases = [saved_case(), saved_case("Second visitor's edited text")]
        read_case = patch(
            "mission_planner.gcs.service.read_case",
            side_effect=lambda identity: next(
                case for case in self.cases if case["id"] == identity
            ),
        )
        read_case.start()
        self.addCleanup(read_case.stop)
        for item in patches[:-2]:
            item.start()
            self.addCleanup(item.stop)
        self.mission = Mission(None)
        self.mission.fleet_ready = True
        self.addAsyncCleanup(self.mission.close)

    async def test_launch_rejects_second_and_reset_allows_distinct_run(self):
        first = await self.mission.begin(self.cases[0]["id"])
        directory = self.mission.directory
        self.mission.sensor_frames["Drone1"] = {"private": "old frame"}
        self.mission.agent_busy = True
        self.mission.ground_team.deliveries["old"] = {}
        with self.assertRaisesRegex(AssertionError, "already running"):
            await self.mission.begin(self.cases[1]["id"])
        await self.mission.clear()
        self.assertIsNone(self.mission.case)
        self.assertEqual(self.mission.sensor_frames, {})
        self.assertFalse(self.mission.agent_busy)
        self.assertFalse(self.mission.ground_team.deliveries)
        self.fleet.close.assert_not_called()
        second = await self.mission.begin(self.cases[1]["id"])
        self.assertNotEqual(first, second)
        self.assertNotEqual(directory, self.mission.directory)
        self.assertEqual(self.mission.case["report"]["brief"], "Second visitor's edited text")
        self.assertIn("Mission cancelled", (directory / "events.jsonl").read_text())
        self.assertIn("Rescue mission launched", self.mission.events[0]["text"])

    async def test_in_flight_reset_returns_home_before_clearing(self):
        await self.mission.begin(self.cases[0]["id"])
        self.fleet.snapshot.return_value[0]["armed"] = True
        self.fleet.snapshot.return_value[0]["landed_state"] = 2

        async def landed(name, arguments):
            self.assertIn(name, ("hold", "return_home"))
            self.assertIsNotNone(self.mission.case)
            if name == "return_home":
                self.fleet.snapshot.return_value[0].update(armed=False, landed_state=1)

        self.fleet.execute.side_effect = landed
        await self.mission.clear()
        self.assertEqual(
            [call.args[0] for call in self.fleet.execute.await_args_list], ["hold", "return_home"]
        )
        self.reset_session.assert_called_once_with()
        self.assertIsNone(self.mission.case)

    async def test_launch_is_required_and_browser_forward_cannot_revive_cancelled_case(self):
        message = {"type": "connect", "case_id": self.cases[0]["id"]}
        idle = socket(message)
        await connection(self.mission, idle)
        self.assertEqual(json.loads(idle.send.call_args.args[0]), {"type": "idle"})
        first = socket({"type": "launch", "case_id": self.cases[0]["id"]})
        await connection(self.mission, first)
        self.assertEqual(
            json.loads(first.send.call_args.args[0]),
            {
                "type": "launched",
                "case_id": self.cases[0]["id"],
            },
        )
        self.enter_mission.assert_called_once_with()
        self.assertFalse(self.mission.agent_busy)
        self.fleet.execute.assert_not_awaited()
        self.mission.connection = AsyncMock()
        await connection(self.mission, socket(message))
        self.mission.connection.assert_awaited_once()
        back = socket({"type": "studio"})
        await connection(self.mission, back)
        self.assertEqual(json.loads(back.send.call_args.args[0]), {"type": "studio"})
        forward = socket(message)
        await connection(self.mission, forward)
        self.assertEqual(json.loads(forward.send.call_args.args[0]), {"type": "idle"})
        self.mission.connection.assert_awaited_once()
        self.assertIsNone(self.mission.case)

    async def test_visiting_studio_before_launch_preserves_draft(self):
        await connection(self.mission, socket({"type": "studio"}))
        self.reset_session.assert_not_called()
        self.fleet.clear_missions.assert_not_awaited()

    async def test_disconnect_does_not_cancel_but_returning_to_studio_does(self):
        await self.mission.begin(self.cases[0]["id"])
        self.mission.connection = AsyncMock()
        browser = socket({"type": "connect", "case_id": self.cases[0]["id"]})
        await connection(self.mission, browser)
        self.assertFalse(self.mission.sockets)
        self.assertEqual(self.mission.case, self.cases[0])
        self.reset_session.assert_not_called()
        self.mission.sockets.add(browser)
        await self.mission.clear()
        browser.close.assert_awaited_once()
        self.assertEqual(
            self.cases[0]["report"]["brief"], "A hiker, 34 years old, missing for about 3 hours."
        )

    async def test_cancellation_failure_does_not_reset_native_or_accept_another_launch(self):
        await self.mission.begin(self.cases[0]["id"])
        self.fleet.clear_missions.side_effect = RuntimeError("Mission clear rejected")
        with self.assertRaisesRegex(RuntimeError, "Mission clear rejected"):
            await self.mission.clear()
        self.reset_session.assert_not_called()
        self.assertTrue(self.mission.clearing)
        with self.assertRaisesRegex(AssertionError, "already running"):
            await self.mission.begin(self.cases[1]["id"])

    async def test_concurrent_launch_has_one_winner(self):
        results = await asyncio.gather(
            self.mission.begin(self.cases[0]["id"]),
            self.mission.begin(self.cases[0]["id"]),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, str) for result in results), 1)
        self.assertEqual(sum(isinstance(result, AssertionError) for result in results), 1)


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

"""Mission admission and explicit landed reset retain the coupled fleet."""

import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.gcs.fleet import MAV, Fleet
from mission_planner.gcs.geodesy import NAMES
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
        self.fleet = Mock(fence_error=None, fences={"version": 0})
        self.fleet.snapshot.return_value = [
            {"id": name, "armed": False, "landed_state": 1, "link_lost": False, "ekf_healthy": True}
            for name in NAMES
        ]
        self.fleet.execute = AsyncMock(
            return_value={
                "id": "Drone1",
                "mode": "GUIDED",
                "armed": True,
                "landed_state": 2,
                "north_m": -735.5,
                "east_m": -29.5,
                "altitude_above_home_m": 30,
                "mission_current": 0,
                "mission_total": 1,
            }
        )
        self.fleet.close = AsyncMock()
        self.fleet.clear_missions = AsyncMock()
        patches = [
            patch("mission_planner.gcs.service.ROOT", Path(self.temporary.name)),
            patch.dict("os.environ", {"SNR_SITL_MODEL": "airsim-copter"}),
            patch("mission_planner.gcs.service.Wonder"),
            patch(
                "mission_planner.gcs.service.Inspections",
                side_effect=lambda mission: Mock(
                    snapshot=Mock(return_value=[]), close=AsyncMock(), flight_stopped=AsyncMock()
                ),
            ),
            patch("mission_planner.gcs.service.Fleet", return_value=self.fleet),
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
        self.mission.feed_focus = {"drone_id": "Drone1"}
        self.mission.agent_busy = True
        with self.assertRaisesRegex(AssertionError, "already running"):
            await self.mission.begin(self.cases[1]["id"])
        await self.mission.clear()
        self.assertIsNone(self.mission.case)
        self.assertIsNone(self.mission.feed_focus)
        self.assertFalse(self.mission.agent_busy)
        self.fleet.close.assert_not_called()
        second = await self.mission.begin(self.cases[1]["id"])
        self.assertNotEqual(first, second)
        self.assertNotEqual(directory, self.mission.directory)
        self.assertEqual(self.mission.case["report"]["brief"], "Second visitor's edited text")
        self.assertIn("Mission cancelled", (directory / "events.jsonl").read_text())
        self.assertIn("Rescue mission launched", self.mission.events[0]["text"])
        self.assertEqual(
            self.mission.events[0]["result"],
            {"case_id": self.cases[1]["id"], "case_name": self.cases[1]["name"]},
        )

    async def test_airborne_studio_rejection_leaves_session_and_flight_untouched(self):
        await self.mission.begin(self.cases[0]["id"])
        self.fleet.snapshot.return_value[0]["armed"] = True
        self.fleet.snapshot.return_value[0]["landed_state"] = 2
        browser = socket({"type": "connect", "case_id": self.cases[0]["id"]})
        self.mission.sockets.add(browser)
        tasks = set(self.mission.tasks)
        events = list(self.mission.events)
        studio = socket({"type": "studio"})
        await connection(self.mission, studio)
        error = json.loads(studio.send.call_args.args[0])
        self.assertEqual(error["type"], "error")
        self.assertIn("landed and disarmed", error["message"])
        self.assertTrue(self.mission.active)
        self.assertFalse(self.mission.clearing)
        self.assertEqual(self.mission.events, events)
        self.assertEqual(self.mission.tasks, tasks)
        self.assertTrue(all(not task.cancelled() for task in tasks))
        browser.close.assert_not_awaited()
        self.reset_session.assert_not_called()
        self.fleet.execute.assert_not_awaited()
        self.fleet.clear_missions.assert_not_awaited()

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

    async def test_general_entry_and_reload_retain_one_session_without_case_or_flight(self):
        idle_directory = self.mission.directory
        self.mission.connection = AsyncMock()
        message = {"type": "connect", "case_id": None}
        await connection(self.mission, socket(message))
        directory, identity = self.mission.directory, self.mission.id
        self.assertTrue(self.mission.active)
        self.assertIsNone(self.mission.case)
        self.assertNotEqual(directory, idle_directory)
        self.assertFalse(self.mission.agent_busy)
        self.mission.conversation = [{"role": "user", "content": "Previous request"}]
        await connection(self.mission, socket(message))
        self.assertEqual((self.mission.directory, self.mission.id), (directory, identity))
        self.assertEqual(
            self.mission.conversation, [{"role": "user", "content": "Previous request"}]
        )
        self.assertEqual(self.mission.connection.await_count, 2)
        self.assertFalse(self.mission.sockets)
        self.enter_mission.assert_called_once_with()
        self.reset_session.assert_not_called()
        self.fleet.execute.assert_not_awaited()
        self.mission.model.respond.assert_not_called()
        summary = self.mission.summary()
        self.assertEqual(summary["case_name"], "General mission")
        self.assertIsNone(summary["case_id"])
        self.assertIsNone(summary["briefing"])
        self.mission.inspections.start.assert_not_called()
        self.assertFalse(self.mission.tasks)

    async def test_null_connect_preserves_active_rescue_case(self):
        await self.mission.begin(self.cases[0]["id"])
        self.mission.connection = AsyncMock()
        directory, identity = self.mission.directory, self.mission.id
        await connection(self.mission, socket({"type": "connect", "case_id": None}))
        self.assertEqual(self.mission.case, self.cases[0])
        self.assertEqual((self.mission.directory, self.mission.id), (directory, identity))
        self.enter_mission.assert_called_once_with()
        self.reset_session.assert_not_called()

    async def test_preselected_case_enters_world_during_fleet_startup(self):
        mission = Mission(self.cases[0])
        self.addAsyncCleanup(mission.close)
        mission.fleet.start = AsyncMock()
        await mission.start_fleet()
        self.assertTrue(mission.active)
        self.assertTrue(mission.fleet_ready)
        mission.inspections.start.assert_not_called()
        self.enter_mission.assert_called_once_with()

    async def test_general_startup_enters_world_without_inspection(self):
        self.mission.active = True
        self.fleet.start = AsyncMock()
        await self.mission.start_fleet()
        self.assertTrue(self.mission.fleet_ready)
        self.enter_mission.assert_called_once_with()
        self.mission.inspections.start.assert_not_called()
        self.assertFalse(self.mission.tasks)

    async def test_rescue_admission_waits_for_a_plan_before_inspection(self):
        await self.mission.begin(self.cases[0]["id"])
        await asyncio.sleep(0)
        self.mission.inspections.start.assert_not_called()
        self.mission.inspections.start_search.assert_not_called()
        self.assertFalse(self.mission.tasks)

    async def test_manual_takeoff_waits_for_operator_approval(self):
        await self.mission.begin(None)
        arguments = {"drone_id": "Drone1", "altitude_above_home_m": 30}
        for approved in (False, True):
            with self.subTest(approved=approved):
                self.fleet.execute.reset_mock()
                task = asyncio.create_task(
                    self.mission.execute("take_off", arguments, "operator", "takeoff")
                )
                await asyncio.sleep(0)
                self.assertEqual(len(self.mission.approvals), 1)
                self.fleet.execute.assert_not_awaited()
                approval_id = next(iter(self.mission.approvals))
                await self.mission.handle(
                    {"type": "approval", "id": approval_id, "approved": approved}, None
                )
                if approved:
                    result = await asyncio.wait_for(task, 1)
                    self.assertEqual(result["acknowledged_command"], "take_off")
                    self.fleet.execute.assert_awaited_once_with("take_off", arguments)
                else:
                    with self.assertRaisesRegex(AssertionError, "Operator declined"):
                        await asyncio.wait_for(task, 1)
                    self.fleet.execute.assert_not_awaited()
                self.assertFalse(self.mission.approvals)
                self.assertFalse(self.mission.active_flight)

    async def test_manual_resume_cannot_resume_a_versioned_plan_step(self):
        await self.mission.begin(None)
        arguments = {"drone_id": "Drone1"}
        result = await self.mission.execute("resume", arguments, "operator", "resume")
        self.assertEqual(result["acknowledged_command"], "resume")
        self.fleet.execute.assert_awaited_once_with("resume", arguments)
        self.assertFalse(self.mission.approvals)
        self.fleet.execute.reset_mock()
        self.mission.plan_active = {"missions": {"Drone1": {}}}
        with self.assertRaisesRegex(AssertionError, "cannot resume a partial plan step"):
            await self.mission.execute("resume", arguments, "operator", "resume")
        self.fleet.execute.assert_not_awaited()

    async def test_case_rejoin_waits_for_owner_admission(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def enter():
            entered.set()
            await release.wait()

        self.mission.enter_world = enter
        self.mission.connection = AsyncMock()
        opening = asyncio.create_task(self.mission.begin(self.cases[0]["id"]))
        await asyncio.wait_for(entered.wait(), 1)
        joining = asyncio.create_task(
            connection(
                self.mission,
                socket({"type": "connect", "case_id": self.cases[0]["id"]}),
            )
        )
        await asyncio.sleep(0)
        self.mission.connection.assert_not_awaited()
        release.set()
        await asyncio.wait_for(asyncio.gather(opening, joining), 1)
        self.mission.connection.assert_awaited_once()

    async def test_concurrent_general_entry_opens_once(self):
        self.mission.connection = AsyncMock()
        await asyncio.gather(
            *[
                connection(self.mission, socket({"type": "connect", "case_id": None}))
                for _ in range(2)
            ]
        )
        self.assertTrue(self.mission.active)
        self.assertIsNone(self.mission.case)
        self.assertEqual(self.mission.connection.await_count, 2)
        self.enter_mission.assert_called_once_with()

    async def test_status_is_read_only_and_reports_only_safe_studio_admission(self):
        directory = self.mission.directory
        request = socket({"type": "status"})
        await connection(self.mission, request)
        self.assertEqual(
            json.loads(request.send.call_args.args[0]),
            {
                "type": "status",
                "mission": None,
                "can_open_studio": True,
            },
        )
        self.assertFalse(self.mission.active)
        self.assertEqual(self.mission.directory, directory)
        self.assertFalse(self.mission.events)
        self.enter_mission.assert_not_called()
        self.reset_session.assert_not_called()
        await self.mission.begin(None)
        status = self.mission.status()
        self.assertEqual(
            status["mission"],
            {
                "id": self.mission.id,
                "case_id": None,
                "case_name": "General mission",
            },
        )
        vehicle = self.fleet.snapshot.return_value[0]
        for field, value in (
            ("armed", True),
            ("landed_state", 2),
            ("link_lost", True),
            ("ekf_healthy", False),
        ):
            with self.subTest(field=field):
                previous = vehicle[field]
                vehicle[field] = value
                self.assertFalse(self.mission.status()["can_open_studio"])
                vehicle[field] = previous
        self.mission.fleet_ready = False
        self.assertFalse(self.mission.status()["can_open_studio"])
        self.mission.fleet_ready = True
        self.mission.clearing = True
        self.assertFalse(self.mission.status()["can_open_studio"])
        self.mission.clearing = False

    async def test_general_session_can_end_then_launch_a_real_case(self):
        await self.mission.begin(None)
        self.assertTrue(self.mission.active)
        await self.mission.clear()
        self.assertFalse(self.mission.active)
        self.assertIsNone(self.mission.case)
        self.reset_session.assert_called_once_with()
        self.fleet.execute.assert_not_awaited()
        await self.mission.begin(self.cases[0]["id"])
        self.assertTrue(self.mission.active)
        self.assertEqual(self.mission.case, self.cases[0])

    async def test_pending_flight_blocks_studio_even_before_arming(self):
        await self.mission.begin(None)
        events = list(self.mission.events)
        for pending in ("flight", "plan"):
            with self.subTest(pending=pending):
                if pending == "flight":
                    self.mission.active_flight.add("Drone1")
                else:
                    self.mission.plan_task = Mock()
                self.assertFalse(self.mission.status()["can_open_studio"])
                with self.assertRaisesRegex(AssertionError, "landed and disarmed"):
                    await self.mission.clear()
                self.assertEqual(self.mission.events, events)
                self.assertFalse(self.mission.clearing)
                self.mission.active_flight.clear()
                self.mission.plan_task = None
        self.reset_session.assert_not_called()
        self.fleet.execute.assert_not_awaited()

    async def test_clear_cancels_queued_work_before_closing_sockets(self):
        await self.mission.begin(None)
        release = asyncio.Event()
        performed = []

        async def pending():
            await release.wait()
            performed.append("late command")

        work = self.mission.launch(pending())
        browser = socket({"type": "connect", "case_id": None})

        async def closing(**arguments):
            release.set()
            await asyncio.sleep(0)
            self.assertTrue(work.cancelled())
            self.assertFalse(performed)
            with self.assertRaisesRegex(AssertionError, "Mission is ending"):
                await self.mission.handle({"type": "chat", "text": "Launch now"}, browser)

        browser.close.side_effect = closing
        self.mission.sockets.add(browser)
        await self.mission.clear()
        browser.close.assert_awaited_once()
        self.mission.model.respond.assert_not_called()
        self.fleet.execute.assert_not_awaited()

    async def test_failed_native_admission_remains_failed_on_generic_rejoin(self):
        self.enter_mission.side_effect = TimeoutError("Unreal mode acknowledgement timed out")
        message = {"type": "connect", "case_id": None}
        first = socket(message)
        await connection(self.mission, first)
        identity = self.mission.id
        self.assertTrue(self.mission.active)
        self.assertEqual(self.mission.admission_error, "Unreal mode acknowledgement timed out")
        second = socket(message)
        await connection(self.mission, second)
        for browser in (first, second):
            self.assertEqual(
                json.loads(browser.send.call_args.args[0]),
                {
                    "type": "error",
                    "message": "Mission admission failed: Unreal mode acknowledgement timed out",
                },
            )
        self.assertEqual(self.mission.id, identity)
        self.assertEqual(
            self.mission.summary()["alerts"],
            [
                "Mission admission failed: Unreal mode acknowledgement timed out",
            ],
        )
        self.enter_mission.assert_called_once_with()
        self.mission.inspections.start.assert_not_called()
        self.reset_session.assert_not_called()
        self.fleet.execute.assert_not_awaited()

    async def test_removed_controls_are_rejected_without_flight(self):
        await self.mission.begin(None)
        for message in (
            {"type": "search_point", "north_m": 0, "east_m": 0},
            {"type": "capture_source", "source": "live", "manifest_id": None},
            {"type": "start_replay"},
        ):
            with (
                self.subTest(control=message["type"]),
                self.assertRaisesRegex(AssertionError, "Unknown message type"),
            ):
                await self.mission.handle(message, Mock())
        team = socket({"type": "connect", "case_id": None, "team_id": "alpha"})
        team.request.path = "/team"
        with self.assertRaises(AssertionError):
            await connection(self.mission, team)
        self.fleet.execute.assert_not_awaited()

    async def test_model_catalogue_keeps_map_and_plan_tools_for_both_entries(self):
        def respond(*arguments, trace_path, **kwargs):
            trace_path.with_suffix(".request.json").write_text("{}")
            return {
                "id": "test-response",
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
                "output": [],
            }

        self.mission.model.respond.side_effect = respond
        from world.cosys.landmarks import load

        self.mission.context = Mock(
            return_value=("Test context", {"map": load()[:1], "inspections": []})
        )
        self.mission.decision_state = Mock(return_value=([], []))
        for case_id in (None, self.cases[0]["id"]):
            await self.mission.begin(case_id)
            await self.mission.agent("Hello")
            names = {tool["name"] for tool in self.mission.model.respond.call_args.args[3]}
            self.assertTrue({"find_place", "describe_location", "preview_plan"} <= names)
            self.assertNotIn("resume", names)
            await self.mission.clear()

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

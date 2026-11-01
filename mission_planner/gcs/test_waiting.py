"""Event-driven Wonder continuations, with synthetic telemetry and no inference."""

import asyncio
import copy
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from PIL import Image

from mission_planner.agents.models import Wonder
from mission_planner.environment import ROOT
from mission_planner.gcs.inspection import Inspections
from mission_planner.gcs.service import Mission
from mission_planner.gcs.waiting import ready, validate_conditions

STEP = {"kind": "step", "drone_id": "Drone1", "version": 1, "step_index": 1}
VEHICLE = {
    "id": "Drone1",
    "armed": True,
    "landed_state": 2,
    "mode": "AUTO",
    "link_lost": False,
    "ekf_healthy": True,
}
PLANNER = {
    "active": {
        "plan": {
            "version": 1,
            "drones": [
                {
                    "drone_id": "Drone1",
                    "steps": [
                        {"kind": "takeoff"},
                        {"kind": "fly"},
                        {"kind": "hover", "seconds": None},
                    ],
                }
            ],
        }
    },
    "progress": {"Drone1": {"current_step": 1, "completed_steps": [0]}},
}


class Conditions(unittest.TestCase):
    def setUp(self):
        self.planner = copy.deepcopy(PLANNER)
        self.vehicles = [dict(VEHICLE)]
        self.vehicles[0]["received_at_ms"] = time.time() * 1000

    def test_explicit_wait_can_retrieve_an_already_acknowledged_rescue_candidate(self):
        row = {
            "id": "candidate",
            "status": "candidate",
            "rescue": {"pending": False, "phase": "locked"},
        }
        condition = {"kind": "inspection", "inspection_id": "candidate"}
        validate_conditions([condition], self.planner, self.vehicles, [row])
        result = ready([condition], self.planner, self.vehicles, [row])
        self.assertEqual(result["results"][0]["inspection"], row)
        self.assertFalse(row["rescue"]["pending"])

    def test_arrival_needs_completed_transit_not_upload_or_current_leg(self):
        validate_conditions([STEP], self.planner, self.vehicles, [])
        self.assertIsNone(ready([STEP], self.planner, self.vehicles, []))
        self.planner["progress"]["Drone1"]["completed_steps"].append(1)
        self.assertEqual(ready([STEP], self.planner, self.vehicles, [])["reason"], "condition")

    def test_survey_completion_waits_for_and_includes_final_inspection(self):
        self.planner["active"]["plan"]["drones"][0]["steps"][1] = {
            "kind": "survey",
            "observe": True,
        }
        self.planner["progress"]["Drone1"]["completed_steps"].append(1)
        row = {
            "id": "survey",
            "drone_id": "Drone1",
            "plan_version": 1,
            "status": "running",
            "rescue": {
                "phase": "searching",
                "pending": False,
                "completed_steps": [],
                "step_index": 1,
            },
        }
        self.assertIsNone(ready([STEP], self.planner, self.vehicles, [row]))
        row["rescue"]["completed_steps"].append({"step_index": 1, "frames": 12})
        self.assertIsNone(ready([STEP], self.planner, self.vehicles, [row]))
        row["status"] = "complete"
        row["rescue"].update(phase="complete", pending=True, step_index=None)
        result = ready([STEP], self.planner, self.vehicles, [row])
        self.assertEqual(result["results"][0]["inspection"], row)
        self.assertTrue(row["rescue"]["pending"])

    def test_survey_wait_preserves_failure_instead_of_reporting_observation_completion(self):
        self.planner["active"]["plan"]["drones"][0]["steps"][1] = {
            "kind": "survey",
            "observe": True,
        }
        self.planner["progress"]["Drone1"]["completed_steps"].append(1)
        row = {
            "id": "survey",
            "drone_id": "Drone1",
            "plan_version": 1,
            "status": "failed",
            "rescue": {"phase": "failed", "pending": True, "completed_steps": []},
        }
        result = ready([STEP], self.planner, self.vehicles, [row])
        self.assertEqual(result["results"][0]["inspection"], row)

    def test_invalid_version_and_step_fail_at_registration(self):
        for change in ({"version": 2}, {"step_index": 3}, {"step_index": -1}):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                validate_conditions([{**STEP, **change}], self.planner, self.vehicles, [])

    def test_completed_step_cannot_be_rescheduled(self):
        with self.assertRaisesRegex(AssertionError, "Drone1 step 0 is already complete"):
            validate_conditions([{**STEP, "step_index": 0}], self.planner, self.vehicles, [])

    def test_wait_wakes_on_any_condition_and_returns_only_completed_steps(self):
        other = {**STEP, "step_index": 2}
        validate_conditions([STEP, other], self.planner, self.vehicles, [])
        self.planner["progress"]["Drone1"]["completed_steps"].append(1)
        result = ready([STEP, other], self.planner, self.vehicles, [])
        self.assertEqual([row["condition"] for row in result["results"]], [STEP])
        validate_conditions([other], self.planner, self.vehicles, [])
        self.assertIsNone(ready([other], self.planner, self.vehicles, []))

    def test_rtl_acknowledgement_is_not_landing(self):
        condition = {"kind": "landed", "drone_id": "Drone1"}
        self.vehicles[0]["mode"] = "RTL"
        self.assertIsNone(ready([condition], self.planner, self.vehicles, []))
        self.vehicles[0].update(landed_state=1)
        self.assertIsNone(ready([condition], self.planner, self.vehicles, []))
        self.vehicles[0]["armed"] = False
        self.assertEqual(ready([condition], self.planner, self.vehicles, [])["reason"], "condition")

    def test_telemetry_failure_and_interrupted_flight_are_errors(self):
        for change in ({"link_lost": True}, {"ekf_healthy": False}, {"mode": "RTL"}):
            with self.subTest(change=change):
                result = ready([STEP], self.planner, [{**self.vehicles[0], **change}], [])
                self.assertEqual(result["reason"], "error")

    def test_all_ready_inspections_are_retained_including_failures(self):
        rows = [{"id": "one", "status": "candidate"}, {"id": "two", "status": "failed"}]
        conditions = [{"kind": "inspection", "inspection_id": row["id"]} for row in rows]
        validate_conditions(conditions, self.planner, self.vehicles, rows)
        result = ready(conditions, self.planner, self.vehicles, rows)
        self.assertEqual([r["inspection"] for r in result["results"]], rows)

    def test_search_lock_does_not_misreport_an_interrupted_survey(self):
        self.vehicles[0]["mode"] = "GUIDED"
        row = {"drone_id": "Drone1", "plan_version": 1, "rescue": {"phase": "locked"}}
        for phase in ("reviewing", "locked", "tracking", "held", "resuming"):
            with self.subTest(phase=phase):
                row["rescue"]["phase"] = phase
                self.assertIsNone(ready([STEP], self.planner, self.vehicles, [row]))
        row["rescue"]["phase"] = "failed"
        self.assertEqual(ready([STEP], self.planner, self.vehicles, [row])["reason"], "error")

    def test_acknowledged_candidate_does_not_repeatedly_wake_wonder(self):
        row = {"id": "search", "status": "candidate", "rescue": {"pending": False}}
        inspections = Inspections(Mock(directory=Path(".")))
        inspections.rows[row["id"]] = row
        self.assertEqual(inspections.pending_events(), [])
        row["rescue"]["pending"] = True
        self.assertEqual(inspections.pending_events(), [row])


class WakeLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mission = object.__new__(Mission)
        m = self.mission
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        m.directory = Path(directory.name)
        m.agent_wait = m.wait_task = m.agent_task = None
        m.agent_lock = asyncio.Lock()
        m.agent_busy = False
        m.autopilot_alerts = {}
        m.return_rows = {}
        m.operator_tasks = set()
        m.plan_task = None
        m.tasks = set()
        m.event = Mock()
        m.planner_state = Mock(return_value=copy.deepcopy(PLANNER))
        m.vehicles = Mock(return_value=[{**VEHICLE, "received_at_ms": time.time() * 1000}])
        m.inspections = Mock()
        m.inspections.snapshot.return_value = []
        m.inspections.pending_events.return_value = []
        m.agent = AsyncMock()

    async def asyncTearDown(self):
        self.mission.cancel_wait("Test complete")
        if self.mission.tasks:
            await asyncio.gather(*self.mission.tasks, return_exceptions=True)

    async def test_no_model_request_while_waiting_then_single_arrival_wake(self):
        m = self.mission
        m.schedule_wait([STEP], 60, "Arriving at the lake")
        await asyncio.sleep(0)
        m.agent.assert_not_awaited()
        self.assertFalse(m.agent_busy)
        m.planner_state.return_value["progress"]["Drone1"]["completed_steps"].append(1)
        await asyncio.wait_for(m.wait_task, 1)
        await asyncio.sleep(0)
        m.agent.assert_awaited_once()
        self.assertIsNone(m.agent_wait)

    async def test_search_evidence_wakes_wonder_without_a_voluntary_wait(self):
        m = self.mission
        receipt = {"missions": {"Drone1": {}}}
        m.plan_active = receipt
        row = {"id": "inspection", "rescue": {"pending": True}, "latest": None}
        m.inspections.pending_events.return_value = [row]

        async def review(text, *, wake):
            self.assertIsNone(text)
            self.assertEqual(wake, {"reason": "rescue", "results": [{"inspection": row}]})
            m.plan_active = None

        m.agent.side_effect = review
        await asyncio.wait_for(m.watch_plan(receipt), 1)
        m.inspections.acknowledge.assert_called_once_with("inspection")
        m.agent.assert_awaited_once()

    async def test_step_index_and_inspection_completion_wake_once(self):
        m = self.mission
        planner = m.planner_state.return_value
        planner["active"]["plan"]["drones"][0]["steps"][1] = {"kind": "survey", "observe": True}
        row = {
            "id": "survey",
            "drone_id": "Drone1",
            "plan_version": 1,
            "status": "running",
            "rescue": {
                "phase": "searching",
                "pending": False,
                "completed_steps": [],
                "step_index": 1,
            },
        }
        m.inspections = Inspections(m)
        m.inspections.rows[row["id"]] = row
        m.schedule_wait([STEP], 60, "Finishing the camera pass")
        waiting = m.wait_task
        planner["progress"]["Drone1"]["completed_steps"].append(1)
        await asyncio.sleep(0.15)
        m.agent.assert_not_awaited()
        row["rescue"]["completed_steps"].append({"step_index": 1, "frames": 12})
        await asyncio.sleep(0.15)
        m.agent.assert_not_awaited()
        row["status"] = "complete"
        row["rescue"].update(phase="complete", pending=True, step_index=None)
        await asyncio.wait_for(waiting, 1)
        await asyncio.sleep(0)
        m.agent.assert_awaited_once()
        observed = m.agent.call_args.kwargs["wake"]["results"][0]["inspection"]
        self.assertEqual(observed["id"], row["id"])
        self.assertEqual(observed["rescue"]["completed_steps"], row["rescue"]["completed_steps"])
        self.assertFalse(row["rescue"]["pending"])
        receipt = {"missions": {"Drone1": {}}}
        m.plan_active = receipt
        watching = asyncio.create_task(m.watch_plan(receipt))
        await asyncio.sleep(0.15)
        m.agent.assert_awaited_once()
        m.plan_active = None
        await asyncio.wait_for(watching, 1)

    async def test_search_evidence_waits_while_operator_conversation_is_active(self):
        m = self.mission
        receipt = {"missions": {"Drone1": {}}}
        m.plan_active = receipt
        m.agent_busy = True
        task = asyncio.create_task(m.watch_plan(receipt))
        await asyncio.sleep(0)
        m.inspections.pending_events.assert_not_called()
        m.plan_active = None
        await asyncio.wait_for(task, 1)
        m.agent.assert_not_awaited()

    async def test_autopilot_alert_wakes_general_plan_once_and_stays_visible(self):
        m = self.mission
        receipt = {"missions": {"Drone1": {}}}
        m.plan_active = receipt
        warning = {
            "kind": "statustext",
            "drone_id": "Drone1",
            "text": "Potential Thrust Loss (3)",
            "severity": 0,
            "at_ms": 1000,
        }
        m.fleet_event(warning)

        async def review(text, *, wake):
            self.assertIsNone(text)
            self.assertEqual(wake["reason"], "autopilot")
            self.assertEqual(wake["alerts"], [{**warning, "pending": True}])
            m.fleet_event({**warning, "at_ms": 2000})

        m.agent.side_effect = review
        task = asyncio.create_task(m.watch_plan(receipt))
        await asyncio.sleep(0.25)
        m.agent.assert_awaited_once()
        self.assertEqual(list(m.autopilot_alerts.values()), [{**warning, "pending": False}])
        m.plan_active = None
        await asyncio.wait_for(task, 1)

    async def test_alert_received_during_operator_turn_waits_even_after_landing(self):
        m = self.mission
        receipt = {"missions": {"Drone1": {}}}
        m.plan_active = receipt
        m.agent_busy = True
        warning = {
            "kind": "statustext",
            "drone_id": "Drone1",
            "text": "Critical fault",
            "severity": 2,
            "at_ms": 1000,
        }
        m.fleet_event(warning)
        m.vehicles.return_value[0].update(armed=False, landed_state=1)
        task = asyncio.create_task(m.watch_plan(receipt))
        await asyncio.sleep(0)
        m.agent.assert_not_awaited()
        self.assertFalse(task.done())
        m.agent_busy = False
        await asyncio.wait_for(task, 1)
        await asyncio.sleep(0)
        m.agent.assert_awaited_once()

    async def test_simultaneous_search_result_and_fault_use_one_turn(self):
        m = self.mission
        receipt = {"missions": {"Drone1": {}}}
        m.plan_active = receipt
        row = {"id": "inspection", "rescue": {"pending": True}, "latest": None}
        m.inspections.pending_events.return_value = [row]
        m.fleet_event(
            {
                "kind": "statustext",
                "drone_id": "Drone1",
                "text": "Potential Thrust Loss (3)",
                "severity": 0,
                "at_ms": 1000,
            }
        )

        async def review(text, *, wake):
            self.assertEqual(wake["reason"], "rescue")
            self.assertEqual(wake["results"], [{"inspection": row}])
            self.assertEqual(wake["alerts"][0]["text"], "Potential Thrust Loss (3)")
            m.plan_active = None

        m.agent.side_effect = review
        await asyncio.wait_for(m.watch_plan(receipt), 1)
        m.agent.assert_awaited_once()
        m.inspections.acknowledge.assert_called_once_with("inspection")

    async def test_already_completed_inspection_wakes_after_current_turn_finishes(self):
        m = self.mission
        m.agent_busy = True
        m.inspections.snapshot.return_value = [{"id": "ready", "status": "candidate"}]
        m.schedule_wait([{"kind": "inspection", "inspection_id": "ready"}], 60, "Reviewing view")
        task = m.wait_task
        await asyncio.sleep(0)
        m.agent.assert_not_awaited()
        m.agent_busy = False
        await asyncio.wait_for(task, 1)
        await asyncio.sleep(0)
        m.agent.assert_awaited_once()

    async def test_cancelled_wait_never_resumes(self):
        m = self.mission
        m.schedule_wait([STEP], 60, "Arrival")
        task = m.wait_task
        m.cancel_wait("New operator message")
        m.planner_state.return_value["progress"]["Drone1"]["completed_steps"].append(1)
        await asyncio.gather(task, return_exceptions=True)
        m.agent.assert_not_awaited()
        self.assertIsNone(m.agent_wait)

    async def test_wait_defers_until_all_operator_commands_finish(self):
        m = self.mission
        entered = {name: asyncio.Event() for name in ("Drone1", "Drone2")}
        release = {name: asyncio.Event() for name in entered}

        async def command(name, arguments, source, call_id):
            drone = arguments["drone_id"]
            entered[drone].set()
            await release[drone].wait()
            return {"drone_id": drone}

        m.execute = AsyncMock(side_effect=command)
        commands = {
            name: asyncio.create_task(m.invoke("hold", {"drone_id": name}, "operator", name))
            for name in entered
        }
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 1)
        m.schedule_wait([], 0.001, "Next observation")
        waiting = m.wait_task
        try:
            release["Drone1"].set()
            await commands["Drone1"]
            await asyncio.sleep(0.15)
            m.agent.assert_not_awaited()
        finally:
            release["Drone2"].set()
            await asyncio.gather(*commands.values())
            await asyncio.wait_for(waiting, 1)
        self.assertFalse(m.operator_tasks)
        m.agent.assert_awaited_once()

    async def test_timeout_wakes_once_without_fabricated_negative_observation(self):
        m = self.mission
        waiting = {"conditions": [STEP], "description": "Arrival", "deadline_ms": 0}
        m.agent_wait = waiting
        await m.watch(waiting, time.monotonic() - 1)
        await asyncio.sleep(0)
        m.agent.assert_awaited_once_with(
            None, wake={"reason": "deadline", "description": "Arrival"}
        )


class AgentWake(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(temporary.cleanup)
        self.mission = object.__new__(Mission)
        m = self.mission
        m.directory = Path(temporary.name)
        m.case = None
        m.plan_active = None
        m.fleet = Mock(fences={"version": 0})
        m.fence_change = False
        m.conversation = []
        m.event = Mock()
        from world.cosys.landmarks import load

        m.context = Mock(
            return_value=("Measured mission context", {"map": load()[:1], "inspections": []})
        )
        m.vehicles = Mock(return_value=[])
        m.autopilot_alerts = {}
        m.return_rows = {}
        m.invoke = AsyncMock(return_value={"ok": True, "state": {"description": "Arrival"}})
        m.inspections = Mock(snapshot=Mock(return_value=[]))
        m.model = object.__new__(Wonder)

    async def test_wait_receipt_ends_model_turn_with_paired_tool_history(self):
        import httpx

        calls = []

        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "wait",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "wait_for",
                            "call_id": "wait",
                            "arguments": json.dumps(
                                {"conditions": [STEP], "timeout_s": 60, "description": "Arrival"}
                            ),
                        }
                    ],
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            )

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)
        await self.mission.agent("Fly to the lake and inspect")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.mission.conversation[-1]["type"], "function_call_output")

    async def test_wake_supplies_exact_original_and_mask_images(self):
        import httpx

        from mission_planner.agents.models import data_url

        buffer = io.BytesIO()
        Image.new("RGB", (4, 3), "red").save(buffer, format="JPEG")
        images = [buffer.getvalue(), buffer.getvalue()]
        image_order = [
            {"kind": kind, "frame_id": "frame-one"} for kind in ("original", "SAM overlay")
        ]
        self.mission.inspections.evidence.return_value = {
            "metadata": {"frame_id": "frame-one"},
            "images": images,
            "image_order": image_order,
        }
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "review",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "I see a candidate."}],
                        }
                    ],
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            )

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)
        row = {"id": "inspection-one", "latest": {"frame_id": "frame-one"}}
        await self.mission.agent(
            None, wake={"reason": "condition", "results": [{"inspection": row}]}
        )
        content = requests[0]["input"][0]["content"]
        self.assertEqual(
            [p["image_url"] for p in content if p["type"] == "input_image"],
            list(map(data_url, images)),
        )
        self.assertTrue(
            any('"image_order"' in p["text"] for p in content if p["type"] == "input_text")
        )
        self.mission.invoke.assert_not_awaited()

        await self.mission.agent("Return home")
        previous = requests[1]["input"][0]["content"]
        self.assertFalse(any(part["type"] == "input_image" for part in previous))
        self.assertTrue(any("frame-one" in part["text"] for part in previous))


if __name__ == "__main__":
    unittest.main()

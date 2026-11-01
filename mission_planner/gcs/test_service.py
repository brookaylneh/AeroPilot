"""Agent context boundaries, independent of live flight acceptance."""

import asyncio
import base64
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from PIL import Image

from mission_planner.agents.models import Wonder
from mission_planner.agents.tools import AGENT_INSTRUCTIONS
from mission_planner.environment import ROOT
from mission_planner.gcs.geodesy import ORIGIN, fence_vertices
from mission_planner.gcs.service import (
    Mission,
    compact_vehicle,
    flight_constraints,
    operator_error,
    planning_inspection,
    planning_instructions,
    planning_receipt,
    retire_images,
)
from world.cosys.landmarks import load


class AgentContext(unittest.TestCase):
    def test_candidate_context_describes_observed_points_without_changing_evidence(self):
        from world.cosys.landmarks import load

        landmarks = load()
        point = {"north_m": 207.85755728826473, "east_m": 36.934467208769945}
        row = {
            "latest": {
                "captured_at_ms": 1234,
                "detections": [
                    {"ground": point, "location_error": None},
                    {"ground": None, "location_error": "No ground intersection"},
                    {"confidence": 0.8},
                ],
            },
            "rescue": {
                "phase": "tracking",
                "tracking_error": None,
                "track": {"ground": point, "captured_at_ms": 5678},
                "live_frame": {"captured_at_ms": 5678, "detections": [{"ground": point}]},
            },
        }
        before = copy.deepcopy(row)
        result = planning_inspection(row, landmarks)
        description = result["latest"]["detections"][0]["landmark_location"]
        self.assertIn("Meadow Creek", description)
        self.assertIn("south bank", description)
        self.assertEqual(result["rescue"]["track"]["landmark_location"], description)
        self.assertEqual(result["rescue"]["track"]["captured_at_ms"], 5678)
        self.assertEqual(
            result["rescue"]["live_frame"]["detections"][0]["landmark_location"], description
        )
        self.assertNotIn("landmark_location", result["latest"]["detections"][1])
        self.assertNotIn("landmark_location", result["latest"]["detections"][2])
        self.assertEqual(row, before)

    def test_lost_session_context_names_all_unavailable_candidate_tools(self):
        from world.cosys.landmarks import load

        row = {
            "latest": None,
            "rescue": {
                "track": None,
                "live_frame": None,
                "tracking_error": "SAM target is leaving the camera view",
            },
        }
        result = planning_inspection(row, load())
        self.assertEqual(
            result["rescue"]["unavailable_tools"],
            ["center_candidate", "get_candidate", "follow_candidate"],
        )
        self.assertNotIn("unavailable_tools", row["rescue"])

    def test_model_receipt_keeps_plan_and_metrics_without_dense_flight_encoding(self):
        plan = {"version": 1, "drones": [{"drone_id": "Drone1", "steps": [{"kind": "land"}]}]}
        receipt = {
            "plan": plan,
            "missions": {
                "Drone1": {
                    "items": [{"command": 16}] * 500,
                    "route": [{"north_m": 0, "east_m": 0}] * 501,
                    "distance_m": 1200,
                    "duration_s": 300,
                    "battery_required_percent": 70,
                    "step_ranges": [{"step_index": 0, "duration_s": 300}],
                }
            },
        }
        result = planning_receipt(receipt)
        self.assertIs(result["plan"], plan)
        self.assertEqual(result["missions"]["Drone1"]["route_points"], 501)
        self.assertEqual(result["missions"]["Drone1"]["battery_required_percent"], 70)
        self.assertNotIn("items", result["missions"]["Drone1"])
        self.assertNotIn("route", result["missions"]["Drone1"])
        self.assertEqual(len(receipt["missions"]["Drone1"]["items"]), 500)

    def test_retiring_images_preserves_evidence_identifiers_and_typed_history(self):
        history = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Inspection abc, frame xyz"},
                    {"type": "input_image", "image_url": "data:image/jpeg;base64,abc"},
                ],
            },
            {"type": "reasoning", "summary": [], "encrypted_content": "opaque"},
            {"type": "function_call", "call_id": "one", "name": "get_fleet", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "one", "output": "{}"},
        ]
        paired = history[1:]
        retire_images(history)
        self.assertEqual(
            history[0]["content"], [{"type": "input_text", "text": "Inspection abc, frame xyz"}]
        )
        self.assertEqual(history[1:], paired)

    def test_coupled_startup_events_reach_the_mission_log(self):
        mission = object.__new__(Mission)
        mission.fence_change = False
        mission.fleet = Mock(fences={"version": 0}, fence_error=None)
        mission.event = Mock()
        port_event = {"kind": "sitl_ports_verified", "ports": [14620, 5701, 9203]}
        home_event = {"kind": "home_comparison", "drone_id": "Drone1", "home_error_m": 2.372}
        mission.fleet_event(port_event)
        mission.event.assert_called_with("mode", "SITL listener ports verified", result=port_event)
        mission.fleet_event(home_event)
        mission.event.assert_called_with(
            "mode", "Drone1 home position error 2.372 m", result=home_event
        )
        with self.assertRaisesRegex(AssertionError, "Unknown fleet event"):
            mission.fleet_event({"kind": "unexpected"})

    def test_command_result_omits_unbounded_telemetry(self):
        vehicle = {
            "id": "Drone1",
            "mode": "GUIDED",
            "armed": True,
            "landed_state": 2,
            "north_m": -814.17,
            "east_m": -2.5,
            "altitude_above_home_m": 30,
            "mission_current": 1,
            "mission_total": 51,
            "ground_speed_mps": 5,
            "heading_deg": 119.75,
            "climb_rate_mps": 1.6,
            "battery_percent": 90,
            "home": {"altitude_msl_m": 2632.73},
            "fence": ["vertex"] * 100,
            "mission": ["item"] * 51,
            "status_text": ["old status"] * 20,
        }
        result = compact_vehicle(vehicle)
        self.assertEqual(result["drone_id"], "Drone1")
        self.assertEqual(result["mission_total"], 51)
        self.assertEqual(result["altitude_above_home_m"], 30)
        self.assertLess(len(json.dumps(result)), 400)
        self.assertTrue({"fence", "mission", "status_text"}.isdisjoint(result))

    def test_planning_constraints_preserve_exact_readback_polygon_and_limits(self):
        points = [{"north_m": -844.676, "east_m": -25.004}, {"north_m": 804.674, "east_m": 804.671}]
        fence = {"inclusion": points, "exclusions": [points]}
        result = flight_constraints([{"id": "Drone1", "fence": fence}])
        self.assertEqual(result["fences"]["Drone1"], fence)
        self.assertEqual(result["boundary_clearance_m"], 10)
        self.assertEqual(result["altitude_above_home_m"], [10, 100])
        self.assertEqual(result["minimum_separation_m"], 0.1)
        self.assertEqual(result["origin_altitude_msl_m"], ORIGIN["altitude_msl_m"])

    def test_every_request_contains_map_and_briefing(self):
        mission = object.__new__(Mission)
        mission.fence_change = False
        mission.fleet = Mock(fences={"version": 0}, fence_error=None)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(temporary.cleanup)
        mission.directory = Path(temporary.name)
        mission.landmarks = load()
        mission.agent_wait = mission.wait_task = None
        mission.inspections = Mock(
            snapshot=Mock(return_value=[]), close=AsyncMock(), flight_stopped=AsyncMock()
        )
        mission.plan_preview = mission.plan_active = mission.plan_previous = None
        mission.case = {"report": {key: "Briefing text" for key in ("brief",)}}
        mission.events = []
        mission.autopilot_alerts = {}
        mission.return_rows = {}
        mission.vehicles = lambda: []
        mission.event_sequence = 0
        for _ in range(2):
            context = json.loads(
                planning_instructions(*mission.context()).split("Current mission context: ")[1]
            )
            self.assertEqual(context["briefing"], {"brief": "Briefing text"})
            self.assertTrue(context["map"])
        instruction = {
            "id": "operator-message",
            "kind": "operator",
            "sequence": 1,
            "timestamp_ms": 1234,
            "text": "Let Drone4 continue",
            "source": "operator",
            "name": None,
            "arguments": None,
        }
        mission.events = [instruction, {"kind": "perception", "source": "inspection"}]
        context = json.loads(
            planning_instructions(*mission.context()).split("Current mission context: ")[1]
        )
        self.assertEqual(
            context["latest_operator_instruction"],
            {key: value for key, value in instruction.items() if key != "source"},
        )
        override = {
            **instruction,
            "id": "operator-override",
            "sequence": 3,
            "timestamp_ms": 2345,
            "kind": "tool_call",
            "text": "return_home",
            "name": "return_home",
            "arguments": {"drone_id": "Drone4"},
        }
        mission.events.extend([override, {"kind": "perception", "source": "inspection"}])
        context = json.loads(
            planning_instructions(*mission.context()).split("Current mission context: ")[1]
        )
        self.assertEqual(
            context["latest_operator_instruction"],
            {key: value for key, value in override.items() if key != "source"},
        )

    def test_current_telemetry_is_distinct_from_historical_acknowledgements(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        mission = object.__new__(Mission)
        mission.fence_change = False
        mission.fleet = Mock(fences={"version": 0}, fence_error=None)
        mission.case = None
        mission.directory = Path(directory.name)
        mission.landmarks = load()
        mission.agent_wait = mission.wait_task = None
        mission.inspections = Mock(
            snapshot=Mock(return_value=[]), close=AsyncMock(), flight_stopped=AsyncMock()
        )
        mission.plan_preview = mission.plan_active = mission.plan_previous = None
        mission.event_sequence = 2
        mission.autopilot_alerts = {}
        mission.return_rows = {}
        vehicle = {
            "id": "Drone1",
            "mode": "GUIDED",
            "armed": True,
            "landed_state": 2,
            "north_m": -814,
            "east_m": -2.5,
            "altitude_above_home_m": 30,
            "mission_current": 0,
            "mission_total": 4,
            "ground_speed_mps": 5,
            "heading_deg": 119.75,
            "climb_rate_mps": 1.6,
            "battery_percent": 90,
            "home": {"altitude_msl_m": 2632.73},
            "fence": {"inclusion": fence_vertices(), "exclusions": []},
        }
        mission.vehicles = lambda: [vehicle]
        mission.events = [
            {
                "sequence": 1,
                "timestamp_ms": 1000,
                "source": "operator",
                "kind": "tool_result",
                "text": "fly_route",
                "name": "fly_route",
                "arguments": {"drone_id": "Drone1"},
                "result": {
                    "ok": True,
                    "state": {"mode": "AUTO", "heading_deg": 350.2, "climb_rate_mps": -0.3},
                },
            }
        ]
        context = json.loads(
            planning_instructions(*mission.context()).split("Current mission context: ")[1]
        )
        self.assertEqual(context["current_vehicles"][0]["mode"], "GUIDED")
        self.assertEqual(context["current_vehicles"][0]["heading_deg"], 119.75)
        self.assertEqual(context["current_vehicles"][0]["climb_rate_mps"], 1.6)
        historical = context["historical_operator_events"][0]
        self.assertEqual(historical["result"]["state"]["mode"], "AUTO")
        self.assertEqual(historical["result"]["state"]["heading_deg"], 350.2)
        self.assertEqual(historical["result"]["state"]["climb_rate_mps"], -0.3)
        self.assertEqual(historical["timestamp_ms"], 1000)
        self.assertEqual(historical["sequence"], 1)
        self.assertGreater(context["telemetry_at_utc_ms"], historical["timestamp_ms"])
        exclusion = [
            {"north_m": 0, "east_m": 0},
            {"north_m": 20, "east_m": 0},
            {"north_m": 20, "east_m": 20},
            {"north_m": 0, "east_m": 20},
        ]
        mission.fleet.fences["version"] = 1
        vehicle["fence"]["exclusions"].append(exclusion)
        updated = json.loads(
            planning_instructions(*mission.context()).split("Current mission context: ")[1]
        )
        self.assertEqual(updated["fence_version"], 1)
        self.assertEqual(updated["flight_constraints"]["fences"]["Drone1"], vehicle["fence"])
        self.assertEqual(updated["flight_constraints"]["boundary_clearance_m"], 10)
        self.assertEqual(context["flight_constraints"]["fences"]["Drone1"]["exclusions"], [])

    def test_general_context_has_flexible_observation_guidance_without_fabricated_brief(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as directory:
            mission = object.__new__(Mission)
            mission.fence_change = False
            mission.fleet = Mock(fences={"version": 0}, fence_error=None)
            mission.directory = Path(directory)
            mission.case = None
            mission.landmarks = load()
            mission.agent_wait = mission.wait_task = None
            mission.inspections = Mock(
                snapshot=Mock(return_value=[]), close=AsyncMock(), flight_stopped=AsyncMock()
            )
            mission.plan_preview = mission.plan_active = mission.plan_previous = None
            mission.events = []
            mission.autopilot_alerts = {}
            mission.return_rows = {}
            mission.vehicles = lambda: []
            mission.event_sequence = 0
            instructions = planning_instructions(*mission.context())
            context = json.loads(instructions.split("Current mission context: ")[1])
            self.assertTrue(context["map"])
            self.assertIsNone(context["briefing"])
            self.assertNotIn("search", context)
            self.assertTrue(instructions.startswith(AGENT_INSTRUCTIONS))
            self.assertIn("observe:true", instructions)
            self.assertNotIn("{NORTH_MIN_M", instructions)

    def test_http_error_does_not_expose_exception_class(self):
        request = httpx.Request("POST", "https://api.meta.ai/v1/responses")
        response = httpx.Response(500, request=request)
        error = httpx.HTTPStatusError("Internal server error", request=request, response=response)
        self.assertEqual(
            operator_error(error),
            "Wonder is unavailable (HTTP 500). No command was issued from this Wonder request.",
        )

    def test_errors_are_operator_sentences(self):
        self.assertEqual(operator_error(AssertionError("Operator declined")), "Operator declined")
        self.assertEqual(
            operator_error(AssertionError()), "AssertionError: no error message was provided."
        )


class EvidenceIdentification(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mission = object.__new__(Mission)
        self.mission.fence_change = False
        self.mission.fleet = Mock(fences={"version": 0}, fence_error=None)
        m = self.mission
        m.case = None
        m.events, m.event_sequence = [], 0
        m.log = io.StringIO()
        m.fleet = Mock(fences={"version": 0}, fence_error=None)
        m.inspections = Mock()
        self.evidence = m.event(
            "evidence",
            "Person wearing the reported clothing",
            arguments={
                "inspection_id": "inspection",
                "frame_id": "frame",
                "candidate_index": 0,
                "assessment": "matching",
                "headline": "Person matching the reported clothing",
                "summary": "Pink-over-white top and light-blue jeans; identity unknown",
            },
            result={"drone_id": "Drone3", "latest": {"captured_at_ms": 900}},
            source="agent",
        )
        self.evidence["timestamp_ms"] = 1000
        self.operator = m.event("operator", "That is Maya in Drone3's image.", source="operator")
        self.operator["timestamp_ms"] = 2000
        self.arguments = {
            "evidence_id": self.evidence["id"],
            "operator_event_id": self.operator["id"],
        }

    async def test_identification_preserves_exact_operator_provenance_without_flight(self):
        result = await self.mission.execute("confirm_evidence", self.arguments, "agent", "call")
        self.assertEqual(result["kind"], "evidence_confirmation")
        self.assertEqual(result["source"], "agent")
        self.assertEqual(result["text"], self.operator["text"])
        self.assertEqual(result["result"], {**self.arguments, "statement": self.operator["text"]})
        self.mission.fleet.assert_not_called()
        self.assertEqual(self.mission.fleet.method_calls, [])
        self.assertEqual(self.mission.inspections.method_calls, [])
        with self.assertRaisesRegex(AssertionError, "already has operator identification"):
            await self.mission.execute("confirm_evidence", self.arguments, "agent", "duplicate")

    async def test_unknown_old_or_nonoperator_confirmation_is_rejected(self):
        for change, message in (
            ({"evidence_id": "unknown"}, "Unknown accepted evidence"),
            ({"operator_event_id": "unknown"}, "latest operator message"),
        ):
            with self.subTest(change=change), self.assertRaisesRegex(AssertionError, message):
                await self.mission.execute(
                    "confirm_evidence", {**self.arguments, **change}, "agent", "call"
                )
        approval = self.mission.event("approval", "Launch approved", source="operator")
        with self.assertRaisesRegex(AssertionError, "latest operator message"):
            await self.mission.execute(
                "confirm_evidence",
                {**self.arguments, "operator_event_id": approval["id"]},
                "agent",
                "call",
            )
        self.mission.event("operator", "The other image is a tree", source="operator")
        with self.assertRaisesRegex(AssertionError, "latest operator message"):
            await self.mission.execute("confirm_evidence", self.arguments, "agent", "call")
        self.assertFalse(
            any(event["kind"] == "evidence_confirmation" for event in self.mission.events)
        )

    async def test_identification_must_follow_publication_and_be_recorded_by_wonder(self):
        self.operator["timestamp_ms"] = 999
        with self.assertRaisesRegex(AssertionError, "must follow evidence publication"):
            await self.mission.execute("confirm_evidence", self.arguments, "agent", "call")
        with self.assertRaisesRegex(AssertionError, "Only Wonder"):
            await self.mission.execute("confirm_evidence", self.arguments, "operator", "call")

    async def test_context_exposes_finding_and_identification_source_ids(self):
        from world.cosys.landmarks import load

        m = self.mission
        m.landmarks = load()
        m.planner_state = Mock(
            return_value={"active": None, "preview": None, "previous": None, "progress": {}}
        )
        m.vehicles = Mock(return_value=[])
        m.inspections.snapshot.return_value = []
        m.autopilot_alerts, m.return_rows = {}, {}
        confirmed = await m.execute("confirm_evidence", self.arguments, "agent", "call")
        _, context = m.context()
        self.assertEqual(context["latest_operator_instruction"]["id"], self.operator["id"])
        self.assertEqual(
            context["evidence"],
            [
                {
                    "id": self.evidence["id"],
                    "timestamp_ms": 1000,
                    "drone_id": "Drone3",
                    "captured_at_ms": 900,
                    **self.evidence["arguments"],
                    "confirmation": confirmed["result"],
                }
            ],
        )


class AutopilotAlerts(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(directory.cleanup)
        root = patch("mission_planner.gcs.service.ROOT", Path(directory.name))
        root.start()
        self.addCleanup(root.stop)
        self.mission = object.__new__(Mission)
        self.mission.fence_change = False
        self.mission.fleet = Mock(fences={"version": 0}, fence_error=None)
        self.mission.initialize_run(None, active=True)
        self.addCleanup(lambda: self.mission.log.close())
        self.mission.fleet_ready = True
        self.mission.fleet_error = None
        self.mission.vehicles = lambda: []
        self.warning = {
            "kind": "statustext",
            "drone_id": "Drone1",
            "text": "Potential Thrust Loss (3)",
            "severity": 0,
            "at_ms": 1000,
        }

    def test_session_history_and_severe_alert_survive_long_flights(self):
        m = self.mission
        m.fleet_event(self.warning)
        for index in range(1001):
            m.fleet_event({**self.warning, "severity": 6, "text": "Normal status", "at_ms": index})
        self.assertTrue(any(e["result"] == self.warning for e in m.events))
        self.assertEqual(len(m.events), 1002)
        self.assertEqual(m.summary()["alerts"], ["Drone1 autopilot: Potential Thrust Loss (3)"])
        context = json.loads(
            planning_instructions(*m.context()).split("Current mission context: ")[1]
        )
        self.assertEqual(context["autopilot_alerts"], [{**self.warning, "pending": True}])
        logged = [
            json.loads(line) for line in (m.directory / "events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(logged[0]["result"], self.warning)

    def test_deduplicates_severity_identity_without_hiding_distinct_faults(self):
        m = self.mission
        m.fleet_event(self.warning)
        m.fleet_event({**self.warning, "at_ms": 2000})
        self.assertEqual(list(m.autopilot_alerts.values()), [{**self.warning, "pending": True}])
        for severity in range(1, 8):
            m.fleet_event({**self.warning, "severity": severity})
        self.assertEqual([row["severity"] for row in m.autopilot_alerts.values()], [0, 1, 2, 3])

    def test_only_a_new_launch_resets_that_drones_previous_flight_alerts(self):
        m = self.mission
        m.fleet_event(self.warning)
        m.fleet_event({**self.warning, "drone_id": "Drone2"})
        receipt = {
            "plan": {"version": 2, "drones": [{"drone_id": "Drone1", "steps": [{"kind": "fly"}]}]}
        }
        m.fleet_event({"kind": "plan_swap", "version": 2, "stage": "uploaded", "receipt": receipt})
        self.assertEqual(len(m.autopilot_alerts), 2)
        receipt = {
            "plan": {
                "version": 3,
                "drones": [{"drone_id": "Drone1", "steps": [{"kind": "takeoff"}]}],
            }
        }
        m.fleet_event({"kind": "plan_swap", "version": 3, "stage": "uploaded", "receipt": receipt})
        self.assertEqual([row["drone_id"] for row in m.autopilot_alerts.values()], ["Drone2"])
        m.fleet_event({**self.warning, "at_ms": 3000})
        self.assertEqual(len(m.autopilot_alerts), 2)
        self.assertEqual(list(m.autopilot_alerts.values())[-1]["at_ms"], 3000)

    def test_explicit_session_reset_clears_alerts(self):
        m = self.mission
        m.fleet_event(self.warning)
        m.log.close()
        m.initialize_run(None, active=False)
        self.assertEqual(m.autopilot_alerts, {})

    def test_context_snapshot_is_detached_from_mutable_flight_state(self):
        self.mission.fleet_event(self.warning)
        _, context = self.mission.context()
        alert = next(iter(self.mission.autopilot_alerts.values()))
        alert.update(text="A newer alert", pending=False)
        self.mission.autopilot_alerts.clear()
        self.assertEqual(context["autopilot_alerts"], [{**self.warning, "pending": True}])

    def test_validated_swap_revokes_inspections_before_aircraft_start_braking(self):
        m = self.mission
        m.inspections = Mock()
        m.cancel_wait = Mock()
        m.fleet_event(
            {"kind": "plan_swap", "stage": "holding", "version": 2, "drone_ids": ["Drone1"]}
        )
        m.inspections.invalidate.assert_called_once_with("Flight plan changed")
        m.cancel_wait.assert_called_once_with("Flight plan changed")
        self.assertEqual(m.events[-1]["result"]["stage"], "holding")


class AgentConversation(unittest.IsolatedAsyncioTestCase):
    async def test_context_failure_reports_operation_type_and_traceback_without_model_call(self):
        self.replies([])
        with patch("mission_planner.gcs.service.planning_instructions", side_effect=AssertionError):
            task = self.mission.launch(self.mission.agent("Review the candidate"))
            await asyncio.wait([task])
        self.assertIsInstance(task.exception(), RuntimeError)
        self.assertEqual(self.requests, [])
        self.assertEqual(
            self.mission.event.call_args.args,
            (
                "error",
                "Wonder context preparation failed: AssertionError: no error message was provided.",
            ),
        )
        result = self.mission.event.call_args.kwargs["result"]
        self.assertEqual(result["exception_type"], "RuntimeError")
        self.assertIn("AssertionError", result["traceback"])
        self.assertIn("planning_instructions", result["traceback"])

    async def test_slow_context_formatting_leaves_event_loop_responsive_and_can_be_cancelled(self):
        self.replies([])
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def blocked_map(landmarks):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2), "Context formatting blocked the event loop"
            return {"places": landmarks}

        with patch("mission_planner.gcs.service.compact_map", side_effect=blocked_map):
            task = asyncio.create_task(self.mission.agent("Review the candidate"))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                self.assertFalse(task.done())
                task.cancel()
                await asyncio.wait([task])
                self.assertTrue(task.cancelled())
            finally:
                release.set()
                await asyncio.wait([task])
        self.assertEqual(self.requests, [])

    async def test_current_candidate_tool_supplies_its_exact_images_to_the_next_turn(self):
        for name in ("get_candidate", "center_candidate"):
            with self.subTest(tool=name):
                self.requests.clear()
                self.mission.conversation.clear()
                self.mission.inspections.evidence.reset_mock()
                identity, frame = "inspection", "current-frame"
                call = {
                    "type": "function_call",
                    "name": name,
                    "call_id": "current-image",
                    "arguments": json.dumps({"inspection_id": identity}),
                }
                self.mission.invoke.side_effect = None
                self.mission.invoke.return_value = {
                    "ok": True,
                    "state": {"inspection_id": identity, "frame": {"frame_id": frame}},
                }
                image = io.BytesIO()
                Image.new("RGB", (4, 4), "pink").save(image, format="JPEG")
                payload = image.getvalue()
                self.mission.inspections.evidence.return_value = {
                    "metadata": {"frame_id": frame},
                    "image_order": [{"kind": "original", "frame_id": frame}],
                    "images": [payload],
                }
                self.replies([self.reply([call]), self.reply([])])
                await self.mission.agent("Show me the closer candidate view")
                self.mission.inspections.evidence.assert_called_once_with(identity, frame)
                content = self.requests[1]["input"][-1]["content"]
                self.assertIn(frame, content[0]["text"])
                self.assertEqual(
                    base64.b64decode(content[1]["image_url"].split(",", 1)[1]), payload
                )
                paired = self.requests[1]["input"][-2]
                self.assertEqual(paired["type"], "function_call_output")
                self.assertEqual(paired["call_id"], "current-image")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(directory.cleanup)
        self.mission = object.__new__(Mission)
        self.mission.fence_change = False
        self.mission.fleet = Mock(fences={"version": 0}, fence_error=None)
        self.mission.directory = Path(directory.name)
        self.mission.fleet_ready = False
        self.mission.case = None
        self.mission.plan_active = None
        self.mission.agent_lock = asyncio.Lock()
        self.mission.agent_task = None
        self.mission.agent_busy = False
        self.mission.tasks = set()
        self.mission.clearing = False
        self.mission.agent_wait = self.mission.wait_task = None
        self.mission.inspections = Mock(
            snapshot=Mock(return_value=[]), close=AsyncMock(), flight_stopped=AsyncMock()
        )
        self.mission.conversation = []
        self.mission.event = Mock()
        from world.cosys.landmarks import load

        self.mission.context = Mock(
            return_value=("Current aircraft state", {"map": load()[:1], "inspections": []})
        )
        self.mission.vehicles = Mock(return_value=[])
        self.mission.autopilot_alerts = {}
        self.mission.return_rows = {}
        self.fleet_result = {
            "ok": True,
            "state": {
                "vehicles": [],
                "planner": {"preview": None, "active": None, "previous": None, "progress": {}},
            },
        }
        self.mission.invoke = AsyncMock(
            side_effect=lambda name, *args: (
                self.fleet_result if name == "get_fleet" else {"ok": True, "state": {"mode": "RTL"}}
            )
        )
        self.mission.model = object.__new__(Wonder)
        self.requests = []

    async def asyncTearDown(self):
        for task in self.mission.tasks:
            task.cancel()
        if self.mission.tasks:
            await asyncio.gather(*self.mission.tasks, return_exceptions=True)

    def replies(self, responses):
        def respond(request):
            self.requests.append(json.loads(request.content))
            return responses[len(self.requests) - 1]

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)

    def reply(self, output):
        return httpx.Response(
            200,
            json={
                "id": "test-response",
                "status": "completed",
                "output": output,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "output_tokens_details": {"reasoning_tokens": 5},
                },
            },
        )

    async def test_followup_replays_items_in_order_with_fresh_context(self):
        reasoning = {"type": "reasoning", "summary": [], "encrypted_content": "opaque"}
        commentary = {
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "Checking the fleet."}],
        }
        calls = [
            {"type": "function_call", "name": "get_fleet", "call_id": name, "arguments": "{}"}
            for name in ("one", "two")
        ]
        answer = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "The fleet is ready."}],
        }
        self.replies(
            [
                self.reply([reasoning, commentary, *calls]),
                self.reply([answer]),
                self.reply([answer]),
            ]
        )
        await self.mission.agent("Which drones are ready?")
        first_user = self.requests[0]["input"][0]
        results = [
            {
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": '{"ok":true,"state":{"vehicles":[],"planner":{"active":null,"preview":null,"previous_version":null,"progress":{}}}}',
            }
            for call in calls
        ]
        previous = [first_user, reasoning, commentary, *calls, *results, answer]
        self.assertEqual(self.mission.conversation, previous)
        self.assertEqual(self.requests[1]["input"], previous[:-1])
        self.mission.context.return_value = (
            "Drone1 is now airborne at the hut",
            self.mission.context.return_value[1],
        )
        await self.mission.agent("Changed my mind, return back home")
        self.assertEqual(self.requests[2]["input"][:-1], previous)
        self.assertEqual(
            self.requests[2]["input"][-1]["content"][0]["text"], "Changed my mind, return back home"
        )
        self.assertEqual(
            self.requests[2]["instructions"],
            planning_instructions(*self.mission.context.return_value),
        )
        for request in self.requests:
            self.assertNotIn("previous_response_id", request)
            self.assertFalse(request["store"])
            self.assertEqual(request["include"], ["reasoning.encrypted_content"])
        self.assertEqual(self.mission.invoke.await_count, 2)

    async def test_http_failure_preserves_history_and_does_not_retry(self):
        call = {
            "type": "function_call",
            "name": "return_home",
            "call_id": "return",
            "arguments": '{"drone_id":"Drone1"}',
        }
        reason = "referenced response not found or expired"
        self.replies([self.reply([call]), httpx.Response(400, json={"error": {"message": reason}})])
        await self.mission.agent("Return home")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.mission.conversation, self.requests[1]["input"])
        self.mission.invoke.assert_awaited_once_with(
            "return_home", {"drone_id": "Drone1"}, "agent", "return"
        )
        error = self.mission.event.call_args
        self.assertEqual(error.args[0], "error")
        self.assertIn(reason, error.args[1])
        self.assertIn("No command was issued from this Wonder request", error.args[1])
        self.assertEqual(error.kwargs["result"]["status_code"], 400)

    async def test_evidence_pixels_stay_in_browser_state_and_images_not_model_text(self):
        from mission_planner.gcs.inspection import Inspections

        images = []
        for value in (10, 20, 30, 40):
            output = io.BytesIO()
            Image.new("RGB", (8, 8), (value, value, value)).save(output, format="JPEG")
            images.append(output.getvalue())
        order = [
            {"kind": kind}
            for kind in ("original", "SAM overlay", "Glimmer crop", "Glimmer context")
        ]
        metadata = {"frame_id": "source-frame", "captured_at_ms": 1234, "drone_id": "Drone3"}
        png = io.BytesIO()
        Image.new("L", (2, 2), 255).save(png, format="PNG")
        pixels = base64.b64encode(png.getvalue()).decode()
        row = {
            "id": "review",
            "status": "candidate",
            "latest": {
                "frame_ref": "/api/evidence/mission/source-frame.jpg",
                "detections": [
                    {"mask_area_px": 4, "mask": {"origin_xy": [1, 2], "png_base64": pixels}}
                ],
            },
            "rescue": {
                "phase": "locked",
                "tracking_error": None,
                "track": None,
                "reports": [
                    {
                        "images": {
                            "crop_ref": "candidate-crop.jpg",
                            "context_ref": "candidate-context.jpg",
                        }
                    }
                ],
            },
        }
        original = copy.deepcopy(row)
        mission = self.mission
        mission.landmarks = load()
        mission.inspections = Inspections(mission)
        mission.inspections.rows[row["id"]] = row
        mission.inspections.evidence = Mock(
            return_value={"metadata": metadata, "image_order": order, "images": images}
        )
        mission.plan_preview = mission.plan_previous = None
        mission.events, mission.event_sequence = [], 0
        mission.log = io.StringIO()
        mission.event = Mission.event.__get__(mission)
        mission.context = Mission.context.__get__(mission)
        mission.summary = Mock(return_value={"id": "mission"})
        mission.approvals, mission.map_focus = {}, None
        raw_result = {"ok": True, "state": row}
        mission.event(
            "tool_result",
            "Inspection evidence",
            name="stop_inspection",
            result=raw_result,
            source="operator",
        )
        mission.invoke.side_effect = lambda *args: raw_result
        self.replies(
            [
                self.reply(
                    [
                        {
                            "type": "function_call",
                            "name": "stop_inspection",
                            "call_id": "review-result",
                            "arguments": '{"drone_id":"Drone3"}',
                        }
                    ]
                ),
                self.reply([]),
                self.reply([]),
            ]
        )
        await self.mission.agent(
            None,
            wake={"reason": "rescue", "results": [{"inspection": row}]},
        )
        content = self.requests[0]["input"][0]["content"]
        self.assertIn("exact review inputs", content[1]["text"])
        self.assertEqual(
            json.loads(content[1]["text"].split("Enlargement adds no detail. ")[1]),
            {"metadata": metadata, "image_order": order},
        )
        self.assertEqual(
            [base64.b64decode(item["image_url"].split(",")[1]) for item in content[2:]], images
        )
        self.mission.inspections.evidence.assert_called_once_with("review")
        await mission.agent("Explain the evidence")
        for request in self.requests:
            self.assertNotIn(pixels, json.dumps(request))
            self.assertNotIn("png_base64", json.dumps(request))
            context = json.loads(request["instructions"].split("Current mission context: ")[1])
            detection = context["inspections"][0]["latest"]["detections"][0]
            self.assertEqual(detection, {"mask_area_px": 4, "mask": {"origin_xy": [1, 2]}})
            self.assertEqual(
                context["inspections"][0]["rescue"]["reports"], original["rescue"]["reports"]
            )
        output = self.requests[1]["input"][-1]
        self.assertEqual(output["type"], "function_call_output")
        self.assertNotIn("png_base64", output["output"])
        self.assertEqual(mission.state()["inspections"], [original])
        self.assertEqual(row, original)
        self.assertIn(pixels, mission.log.getvalue())
        self.assertIn(pixels, json.dumps(mission.events))

    async def test_plan_replacement_discards_a_delayed_automatic_review(self):
        entered, release = threading.Event(), threading.Event()
        call = {
            "type": "function_call",
            "name": "start_inspection",
            "call_id": "stale-inspection",
            "arguments": '{"drone_id":"Drone1"}',
        }

        def respond(request):
            self.requests.append(json.loads(request.content))
            entered.set()
            assert release.wait(5), "Blocked review was not released"
            return self.reply([call])

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)
        self.mission.plan_active = {"plan": {"version": 1}}
        task = asyncio.create_task(
            self.mission.agent(None, wake={"reason": "deadline", "description": "Inspect lake"})
        )
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.mission.plan_active = {"plan": {"version": 2}}
        finally:
            release.set()
            await asyncio.wait_for(task, 2)
        self.mission.invoke.assert_not_awaited()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.mission.conversation, self.requests[0]["input"])
        self.assertIn("response discarded", self.mission.event.call_args.args[1])
        self.assertEqual(self.mission.event.call_args.args[0], "mode")

    async def test_external_plan_change_pairs_but_does_not_dispatch_remaining_calls(self):
        calls = [
            {"type": "function_call", "name": name, "call_id": name, "arguments": "{}"}
            for name in ("get_fleet", "start_inspection")
        ]
        self.replies([self.reply(calls)])

        async def changed(*args):
            self.mission.plan_active = {"plan": {"version": 2}}
            return self.fleet_result

        self.mission.invoke.side_effect = changed
        await self.mission.agent("Continue inspecting")
        self.mission.invoke.assert_awaited_once_with("get_fleet", {}, "agent", "get_fleet")
        results = self.mission.conversation[-2:]
        self.assertEqual([r["call_id"] for r in results], [c["call_id"] for c in calls])
        self.assertFalse(json.loads(results[-1]["output"])["ok"])
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(any(call.args[0] == "error" for call in self.mission.event.call_args_list))

    async def test_inspection_failure_during_inference_discards_stale_plan_and_claims(self):
        row = {"id": "inspection", "status": "running"}
        self.mission.inspections.snapshot.return_value = [row]

        def respond(request):
            self.requests.append(json.loads(request.content))
            row["status"] = "failed"
            return self.reply(
                [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "The search is still running."}
                        ],
                    },
                    {
                        "type": "function_call",
                        "name": "preview_plan",
                        "call_id": "old-plan",
                        "arguments": "{}",
                    },
                ]
            )

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)
        await self.mission.agent("Give Drone4 the remaining search")
        self.mission.invoke.assert_not_awaited()
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(
            any(
                "The search is still running." == c.args[1]
                for c in self.mission.event.call_args_list
            )
        )
        self.assertFalse(any(c.args[0] == "error" for c in self.mission.event.call_args_list))
        self.assertIn("no command was sent", self.mission.event.call_args.args[1])

    async def test_fresh_reads_and_checked_waits_survive_changed_inspection_state(self):
        row = {"id": "inspection", "status": "running"}
        self.mission.inspections.snapshot.return_value = [row]
        calls = [
            {"type": "function_call", "name": "get_fleet", "call_id": "fleet", "arguments": "{}"},
            {"type": "function_call", "name": "wait_for", "call_id": "wait", "arguments": "{}"},
        ]

        def respond(request):
            self.requests.append(json.loads(request.content))
            row["status"] = "candidate"
            return self.reply(calls)

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)
        await self.mission.agent("Check the fleet and wait for evidence")
        self.assertEqual(
            [c.args[0] for c in self.mission.invoke.await_args_list], ["get_fleet", "wait_for"]
        )
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(any(c.args[0] == "error" for c in self.mission.event.call_args_list))

    def test_tracking_diagnostic_text_does_not_change_flight_authority(self):
        row = {
            "id": "inspection",
            "status": "candidate",
            "rescue": {"phase": "locked", "tracking_error": "Too few features"},
        }
        self.mission.inspections.snapshot.return_value = [row]
        before = self.mission.decision_state()
        row["rescue"]["tracking_error"] = "Tracking was lost"
        self.assertEqual(before, self.mission.decision_state())
        row["status"] = "failed"
        self.assertNotEqual(before, self.mission.decision_state())

    async def test_landing_during_inference_does_not_delay_an_explicit_return_override(self):
        state = {"id": "Drone2", "mode": "RTL", "armed": True, "landed_state": 4}
        self.mission.vehicles.return_value = [state]

        def respond(request):
            self.requests.append(json.loads(request.content))
            state.update(armed=False, landed_state=1)
            if len(self.requests) == 1:
                return self.reply(
                    [
                        {
                            "type": "function_call",
                            "name": "return_home",
                            "call_id": "return-four",
                            "arguments": '{"drone_id":"Drone4"}',
                        }
                    ]
                )
            return self.reply(
                [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Return acknowledged."}],
                    }
                ]
            )

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)
        await self.mission.agent("Return Drone4 home")
        self.mission.invoke.assert_awaited_once_with(
            "return_home", {"drone_id": "Drone4"}, "agent", "return-four"
        )

    async def test_mixed_stale_batch_executes_override_and_pairs_cancelled_plan_result(self):
        row = {"id": "inspection", "status": "running"}
        self.mission.inspections.snapshot.return_value = [row]
        calls = [
            {"type": "function_call", "name": "preview_plan", "call_id": "plan", "arguments": "{}"},
            {
                "type": "function_call",
                "name": "return_home",
                "call_id": "return",
                "arguments": '{"drone_id":"Drone4"}',
            },
        ]

        def respond(request):
            self.requests.append(json.loads(request.content))
            row["status"] = "failed"
            return self.reply(
                calls
                if len(self.requests) == 1
                else [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Return acknowledged."}],
                    }
                ]
            )

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)
        await self.mission.agent("Return Drone4 and reassign its sector")
        self.mission.invoke.assert_awaited_once_with(
            "return_home", {"drone_id": "Drone4"}, "agent", "return"
        )
        outputs = [
            item
            for item in self.requests[1]["input"]
            if "type" in item and item["type"] == "function_call_output"
        ]
        self.assertEqual([item["call_id"] for item in outputs], ["plan", "return"])
        self.assertFalse(json.loads(outputs[0]["output"])["ok"])
        self.assertTrue(json.loads(outputs[1]["output"])["ok"])

    async def test_failure_during_read_call_invalidates_later_action_in_same_batch(self):
        row = {"id": "inspection", "status": "running"}
        self.mission.inspections.snapshot.return_value = [row]
        calls = [
            {"type": "function_call", "name": "get_fleet", "call_id": "read", "arguments": "{}"},
            {
                "type": "function_call",
                "name": "start_inspection",
                "call_id": "start",
                "arguments": "{}",
            },
        ]
        self.replies(
            [
                self.reply(calls),
                self.reply(
                    [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": "The search requires attention."}
                            ],
                        }
                    ]
                ),
            ]
        )

        async def read(*args):
            row["status"] = "failed"
            return self.fleet_result

        self.mission.invoke.side_effect = read
        await self.mission.agent("Check and inspect")
        self.mission.invoke.assert_awaited_once_with("get_fleet", {}, "agent", "read")
        output = next(
            item
            for item in self.requests[1]["input"]
            if "type" in item
            and item["type"] == "function_call_output"
            and item["call_id"] == "start"
        )
        self.assertIn("state changed during this tool batch", json.loads(output["output"])["error"])

    async def test_normal_flight_motion_does_not_invalidate_model_decisions(self):
        state = {"id": "Drone1", "mode": "AUTO", "armed": True, "landed_state": 2, "north_m": 1}
        self.mission.vehicles.return_value = [state]

        def respond(request):
            state["north_m"] = 20
            return self.reply(
                [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "I am monitoring the search."}],
                    }
                ]
            )

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)
        await self.mission.agent("Keep monitoring")
        self.mission.event.assert_called_with(
            "agent", "I am monitoring the search.", source="agent"
        )

    async def test_own_successful_plan_change_allows_following_wait(self):
        calls = [
            {"type": "function_call", "name": name, "call_id": name, "arguments": "{}"}
            for name in ("apply_plan_change", "wait_for")
        ]
        self.replies([self.reply(calls)])

        async def applied(name, *args):
            if name == "apply_plan_change":
                self.mission.plan_active = {"plan": {"version": 2}}
                return {
                    "ok": True,
                    "state": {"receipt": {"plan": {"version": 2}, "missions": {}}, "states": []},
                }
            return {"ok": True, "state": {}}

        self.mission.invoke.side_effect = applied
        await self.mission.agent("Change destination and wait for arrival")
        self.assertEqual(self.mission.invoke.await_count, 2)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(json.loads(self.mission.conversation[-1]["output"])["ok"])

    async def test_new_message_interrupts_model_without_waiting_for_its_http_response(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        answer = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "I will keep the original objective."}],
        }

        def respond(request):
            self.requests.append(json.loads(request.content))
            if len(self.requests) == 1:
                entered.set()
                assert release.wait(5), "Interrupted HTTP response was not released"
                return self.reply(
                    [
                        {
                            "type": "function_call",
                            "name": "start_inspection",
                            "call_id": "stale",
                            "arguments": "{}",
                        }
                    ]
                )
            return self.reply([answer])

        self.mission.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.mission.model.http.close)
        original = self.mission.model.respond

        def observed(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            finally:
                if len(kwargs["history"]) == 1:
                    finished.set()

        self.mission.model.respond = observed
        m = self.mission
        await m.handle({"type": "chat", "text": "Find people near the lake"}, None)
        previous = m.agent_task
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            await asyncio.wait_for(
                m.handle({"type": "chat", "text": "Use Drone2 instead"}, None), 2
            )
            self.assertFalse(release.is_set())
            self.assertTrue(previous.cancelled())
            await asyncio.wait_for(m.agent_task, 2)
        finally:
            release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait, 2))
        m.invoke.assert_not_awaited()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(
            [item["content"][0]["text"] for item in self.requests[-1]["input"]],
            ["Find people near the lake", "Use Drone2 instead"],
        )

    async def test_new_message_cancels_pending_tool_and_preserves_paired_history(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        calls = [
            {"type": "function_call", "name": name, "call_id": name, "arguments": "{}"}
            for name in ("start_inspection", "wait_for")
        ]
        answer = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Checking the current situation."}],
        }
        self.replies([self.reply(calls), self.reply([answer])])

        async def pending(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        m = self.mission
        m.invoke.side_effect = pending
        await m.handle({"type": "chat", "text": "Inspect for people near the lake"}, None)
        previous = m.agent_task
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(
            m.handle({"type": "chat", "text": "Go to the cabin instead"}, None), 2
        )
        await asyncio.wait_for(m.agent_task, 2)
        self.assertTrue(previous.cancelled())
        self.assertTrue(cancelled.is_set())
        m.invoke.assert_awaited_once_with("start_inspection", {}, "agent", "start_inspection")
        history = self.requests[-1]["input"]
        outputs = [
            item for item in history if "type" in item and item["type"] == "function_call_output"
        ]
        self.assertEqual([item["call_id"] for item in outputs], [c["call_id"] for c in calls])
        self.assertTrue(all(json.loads(item["output"])["ok"] is False for item in outputs))
        self.assertEqual(history[0]["content"][0]["text"], "Inspect for people near the lake")
        self.assertEqual(history[-1]["content"][0]["text"], "Go to the cabin instead")

    async def test_simultaneous_messages_keep_one_turn_and_both_operator_inputs(self):
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        call = {"type": "function_call", "name": "get_fleet", "call_id": "fleet", "arguments": "{}"}
        answer = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Both changes are recorded."}],
        }
        self.replies([self.reply([call]), self.reply([answer])])

        async def pending(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
                await release.wait()

        m = self.mission
        m.invoke.side_effect = pending
        await m.handle({"type": "chat", "text": "Inspect the lake"}, None)
        await asyncio.wait_for(entered.wait(), 2)
        first = asyncio.create_task(
            m.handle({"type": "chat", "text": "Also inspect the cabin"}, None)
        )
        await asyncio.wait_for(cancelled.wait(), 2)
        second = asyncio.create_task(m.handle({"type": "chat", "text": "Then return home"}, None))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        if m.agent_task is not None:
            await asyncio.wait_for(m.agent_task, 2)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(
            [item["content"][0]["text"] for item in self.requests[-1]["input"] if "role" in item],
            ["Inspect the lake", "Also inspect the cabin", "Then return home"],
        )
        self.assertIsNone(m.agent_task)
        self.assertFalse(m.agent_busy)

    async def test_reset_during_interruption_cannot_start_an_old_chat(self):
        m = self.mission
        for change in ("clearing", "conversation"):
            with self.subTest(change=change):
                started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

                async def pending():
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                        await release.wait()

                m.clearing = False
                m.conversation = [{"role": "user", "content": "Original objective"}]
                m.agent = AsyncMock()
                m.agent_busy = True
                m.agent_task = m.launch(pending())
                await asyncio.wait_for(started.wait(), 2)
                request = asyncio.create_task(m.handle({"type": "chat", "text": "Continue"}, None))
                await asyncio.wait_for(cancelled.wait(), 2)
                if change == "clearing":
                    m.clearing = True
                else:
                    m.conversation = []
                release.set()
                with self.assertRaisesRegex(AssertionError, "chat was not applied"):
                    await asyncio.wait_for(request, 2)
                m.agent.assert_not_called()
                self.assertIsNone(m.agent_task)
                self.assertFalse(m.agent_busy)

    async def test_new_chat_requires_every_drone_at_base_and_clears_wonder_history(self):
        m = self.mission
        m.events, m.event_sequence = [], 0
        m.log = io.StringIO()
        m.event = Mission.event.__get__(m)
        m.active_flight, m.plan_task = set(), None
        home = {"north_m": -735.5, "east_m": -29.5}
        parked = {"armed": False, "landed_state": 1, "link_lost": False, **home, "home": home}
        fleet = [{"id": f"Drone{i}", **parked} for i in range(1, 5)]
        m.vehicles = Mock(return_value=fleet)
        m.event("operator", "Search the lake", source="operator")
        m.conversation = [{"role": "user", "content": "Search the lake"}]
        active = m.plan_active = {"plan": {"version": 7}, "missions": {}}
        for change in ({"north_m": -732.5}, {"armed": True, "landed_state": 2}):
            with self.subTest(change=change):
                fleet[1] = {**fleet[1], **change}
                self.assertFalse(m.chat_ready())
                with self.assertRaisesRegex(AssertionError, "at its home pad"):
                    await m.handle({"type": "new_chat"}, None)
                fleet[1] = {"id": "Drone2", **parked}
        self.assertTrue(m.chat_ready())
        await m.handle({"type": "new_chat"}, None)
        self.assertEqual(m.conversation, [])
        self.assertIs(m.plan_active, active)
        self.assertEqual(
            [(e["kind"], e["source"]) for e in m.events],
            [("operator", "operator"), ("chat_reset", "operator")],
        )
        with self.assertRaisesRegex(AssertionError, "no operator message"):
            await m.handle({"type": "new_chat"}, None)
        m.fleet_ready, m.approvals = True, {}
        m.fleet = Mock(preview_plan=AsyncMock())
        request = {
            "version": 1,
            "drones": [
                {
                    "drone_id": "Drone2",
                    "steps": [
                        {"kind": "takeoff", "height_m": 30},
                        {"kind": "hover", "seconds": None},
                    ],
                }
            ],
        }
        with self.assertRaisesRegex(AssertionError, "requested 1, active 7"):
            await m.execute_plan("preview_plan", {"plan": request}, "agent", "new-plan")
        self.assertIsNone(m.plan_preview)
        self.assertIs(m.plan_active, active)
        m.fleet.preview_plan.assert_not_awaited()

    async def test_conversation_continues_past_twelve_tool_turns_until_answered(self):
        calls = [
            {
                "type": "function_call",
                "name": "get_fleet",
                "call_id": f"fleet-{turn}",
                "arguments": "{}",
            }
            for turn in range(13)
        ]
        answer = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "The plan is ready."}],
        }
        self.replies([*(self.reply([call]) for call in calls), self.reply([answer])])
        await self.mission.agent("Prepare the flight plan")
        self.assertEqual(len(self.requests), 14)
        self.assertEqual(self.mission.invoke.await_count, 13)
        self.assertEqual(
            [item["call_id"] for item in self.requests[-1]["input"] if "call_id" in item],
            [call["call_id"] for call in calls for _ in range(2)],
        )
        self.assertEqual(self.mission.conversation[-1], answer)
        self.assertEqual(
            [
                call.kwargs["result"]["turn"]
                for call in self.mission.event.call_args_list
                if call.args[0] == "model_usage"
            ],
            list(range(14)),
        )
        self.mission.event.assert_called_with("agent", "The plan is ready.", source="agent")

    async def test_invalid_call_batch_does_not_execute_or_leave_unpaired_history(self):
        calls = [
            {"type": "function_call", "name": "get_fleet", "call_id": "one", "arguments": "{}"},
            {"type": "function_call", "name": "get_fleet", "call_id": "two", "arguments": "{"},
        ]
        self.replies([self.reply(calls)])
        with self.assertRaises(json.JSONDecodeError):
            await self.mission.agent("Check the fleet")
        self.mission.invoke.assert_not_awaited()
        self.assertEqual(self.mission.conversation, self.requests[0]["input"])


if __name__ == "__main__":
    unittest.main()

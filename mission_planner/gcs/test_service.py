"""Agent context boundaries, independent of live flight acceptance."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx

from mission_planner.environment import ROOT
from mission_planner.gcs.service import Mission, compact_vehicle, fence_summary, operator_error


class AgentContext(unittest.TestCase):
    def test_coupled_startup_events_reach_the_mission_log(self):
        mission = object.__new__(Mission)
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
            "fence": ["vertex"] * 100,
            "mission": ["item"] * 51,
            "status_text": ["old status"] * 20,
        }
        result = compact_vehicle(vehicle)
        self.assertEqual(result["drone_id"], "Drone1")
        self.assertEqual(result["mission_total"], 51)
        self.assertEqual(result["altitude_above_home_m"], 30)
        self.assertLess(len(json.dumps(result)), 300)
        self.assertTrue({"fence", "mission", "status_text"}.isdisjoint(result))

    def test_fence_summary_uses_readback(self):
        result = fence_summary(
            [{"north_m": -844.676, "east_m": -25.004}, {"north_m": 804.674, "east_m": 804.671}]
        )
        self.assertEqual(result["vertex_count"], 2)
        self.assertEqual(result["bounds"]["north_m"], [-844.676, 804.674])

    def test_tool_continuation_does_not_repeat_briefing(self):
        mission = object.__new__(Mission)
        from mission_planner.search.state import Search

        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(temporary.cleanup)
        mission.directory = Path(temporary.name)
        mission.search = Search(mission)
        mission.autonomy = "Supervised"
        mission.report = {"brief": "Briefing text"}
        mission.events = []
        mission.vehicles = lambda: []
        mission.context_index = 0
        mission.event_sequence = 0
        self.assertIn("Briefing text", mission.instructions(True))
        self.assertNotIn("Briefing text", mission.instructions(False))
        self.assertNotIn("Phase 3A", mission.instructions(False))

    def test_current_telemetry_is_distinct_from_historical_acknowledgements(self):
        from mission_planner.search.state import Search

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        mission = object.__new__(Mission)
        mission.directory = Path(directory.name)
        mission.search = Search(mission)
        mission.autonomy = "Supervised"
        mission.context_index = 0
        mission.event_sequence = 2
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
                "result": {"ok": True, "state": {"mode": "AUTO"}},
            }
        ]
        context = json.loads(mission.instructions(False).split("Current mission context: ")[1])
        self.assertEqual(context["current_vehicles"][0]["mode"], "GUIDED")
        historical = context["historical_operator_events"][0]
        self.assertEqual(historical["result"]["state"]["mode"], "AUTO")
        self.assertEqual(historical["timestamp_ms"], 1000)
        self.assertEqual(historical["sequence"], 1)
        self.assertGreater(context["telemetry_at_utc_ms"], historical["timestamp_ms"])

    def test_http_error_does_not_expose_exception_class(self):
        request = httpx.Request("POST", "https://api.meta.ai/v1/responses")
        response = httpx.Response(500, request=request)
        error = httpx.HTTPStatusError("Internal server error", request=request, response=response)
        self.assertEqual(
            operator_error(error), "Wonder is unavailable (HTTP 500). No command was sent."
        )

    def test_errors_are_operator_sentences(self):
        self.assertEqual(operator_error(AssertionError("Operator declined")), "Operator declined")


if __name__ == "__main__":
    unittest.main()

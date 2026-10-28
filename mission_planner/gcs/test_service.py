"""Agent context boundaries, independent of live flight acceptance."""

import json
import unittest

import httpx

from mission_planner.gcs.service import Mission, compact_vehicle, fence_summary, operator_error


class AgentContext(unittest.TestCase):
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
        mission.autonomy = "Supervised"
        mission.case = {
            "report": {
                key: "Briefing text"
                for key in ("description", "reportedCircumstances", "lastKnownLocation")
            }
        }
        mission.events = []
        mission.context_index = 0
        mission.event_sequence = 0
        self.assertIn("Briefing text", mission.instructions(True))
        self.assertNotIn("Briefing text", mission.instructions(False))
        self.assertNotIn("Phase 3A", mission.instructions(False))

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

"""Benchmark failures propagate with earlier scores and raw traces retained."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from mission_planner.agents.bench_tools import call, run


class BenchmarkFailures(unittest.TestCase):
    def test_timeout_stops_without_retry_and_preserves_completed_scores(self):
        attempted = []

        def respond(request, images, effort, tools, *, instructions, trace_path):
            attempted.append(request)
            trace_path.with_suffix(".http.json").write_text(
                json.dumps(
                    {
                        "complete": request != "Second request",
                        "status_code": 200 if request != "Second request" else None,
                        "request_body_bytes": 100,
                    }
                )
            )
            if request == "Second request":
                raise httpx.ReadTimeout("Test timeout")
            return {"output": [], "usage": {"input_tokens": 1, "output_tokens": 1}}

        model = SimpleNamespace(respond=respond, http=SimpleNamespace(close=Mock()))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "new"
            with (
                patch("mission_planner.agents.bench_tools.Wonder", return_value=model),
                patch(
                    "mission_planner.agents.bench_tools.CASES",
                    [("First request", []), ("Second request", []), ("Third request", [])],
                ),
            ):
                with self.assertRaisesRegex(httpx.ReadTimeout, "Test timeout"):
                    run(output, 1)
            scores = json.loads((output / "scores.json").read_text())
            self.assertEqual(len(scores), 1)
            self.assertTrue(scores[0]["passed"])
            self.assertFalse((output / "measurement.json").exists())
            self.assertFalse(json.loads((output / "02.http.json").read_text())["complete"])
        self.assertEqual(attempted, ["First request", "Second request"])
        model.http.close.assert_called_once()

    def test_expected_calls_require_the_current_wonder_catalogue_and_valid_arguments(self):
        for name in (
            "plan_search",
            "notify_ground_team",
            "take_off",
            "fly_to",
            "fly_route",
            "resume",
        ):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(AssertionError, "Unavailable Wonder tool"),
            ):
                call(name)
        with self.assertRaisesRegex(AssertionError, "Unknown drone"):
            call("open_feed", drone_id="Drone9")
        self.assertEqual(
            call("return_fleet", drone_ids=["Drone1", "Drone2"]),
            {"name": "return_fleet", "arguments": {"drone_ids": ["Drone1", "Drone2"]}},
        )


if __name__ == "__main__":
    unittest.main()

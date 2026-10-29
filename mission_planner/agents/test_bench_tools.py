"""A failed Wonder request is retained once while distinct benchmark cases continue."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from mission_planner.agents.bench_tools import run


class BenchmarkFailures(unittest.TestCase):
    def test_timeout_is_visible_and_next_case_runs_once(self):
        attempted = []

        def respond(request, images, effort, tools, *, instructions, trace_path):
            attempted.append(request)
            if request == "First request":
                raise httpx.ReadTimeout("Test timeout")
            trace_path.with_suffix(".http.json").write_text(
                json.dumps(
                    {
                        "status_code": 200,
                        "request_body_bytes": 100,
                    }
                )
            )
            return {"output": [], "usage": {"input_tokens": 1, "output_tokens": 1}}

        model = SimpleNamespace(respond=respond, http=SimpleNamespace(close=Mock()))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "new"
            with (
                patch("mission_planner.agents.bench_tools.Wonder", return_value=model),
                patch(
                    "mission_planner.agents.bench_tools.CASES",
                    [("First request", []), ("Second request", [])],
                ),
            ):
                run(output, 1)
            report = json.loads((output / "measurement.json").read_text())
        self.assertEqual(attempted, ["First request", "Second request"])
        self.assertEqual((report["correct"], report["total"]), (1, 2))
        self.assertEqual(report["cases"][0]["error"], "ReadTimeout")
        self.assertIsNone(report["cases"][0]["actual"])
        self.assertIsNone(report["cases"][0]["http_status_code"])
        model.http.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

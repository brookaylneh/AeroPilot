"""An entirely failed run still reports failed frames and every observed model call."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mission_planner.agents.models import Detection
from mission_planner.agents.perception.bench_commander import replay
from mission_planner.agents.perception.configuration import load_configuration
from mission_planner.agents.perception.ingestion import capture_id
from mission_planner.agents.perception.pipeline import Processor
from mission_planner.agents.perception.test_pipeline import recorded


class FailedRun(unittest.IsolatedAsyncioTestCase):
    async def test_sam_and_later_glimmer_failures_keep_counts_and_null_latency(self):
        record, payload = recorded()
        captures = {str(i): {**record, "timestampNs": record["timestampNs"] + i} for i in range(2)}
        manifest = {
            "id": "failed-run",
            "records": [
                {"record": key, "frame_id": capture_id(value), "replay_lane": "Drone1"}
                for key, value in captures.items()
            ],
        }
        lock, calls = threading.Lock(), []

        def detect(image, prompts, threshold, timing):
            with lock:
                calls.append(prompts)
                first = len(calls) == 1
            timing.update(status_code=503 if first else 200, roundtrip_s=0.01)
            if first:
                raise RuntimeError("SAM failed")
            return {
                prompt: [Detection((950, 500, 970, 550), 0.8, 50)] if prompt == "person" else []
                for prompt in prompts
            }

        def report(images, system, text, schema, max_tokens, timing):
            timing.update(status_code=503, roundtrip_s=0.02)
            raise RuntimeError("Glimmer failed")

        search = SimpleNamespace(
            commander=SimpleNamespace(), complete_capture=Mock(), changed=lambda: None
        )
        config = load_configuration()
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch(
                "mission_planner.agents.perception.ingestion.read_capture",
                side_effect=lambda path: (captures[str(path)], payload),
            ),
        ):
            directory = Path(temporary)
            processor = Processor(
                directory / "evidence",
                ["bag"],
                {
                    "objective": "Find the person.",
                    "target": "a person",
                    "attributes": [],
                    "ignore": [],
                },
                config,
                SimpleNamespace(detect=detect),
                SimpleNamespace(report=report),
            )
            result = await replay(manifest, processor, search, directory, 0.01)
            self.assertEqual(json.loads((directory / "throughput.json").read_text()), result)
            self.assertEqual(result["completed"], 0)
            self.assertEqual(len(result["failed"]), 2)
            self.assertEqual((result["sam_requests"], result["glimmer_requests"]), (3, 1))
            self.assertEqual(result["frames_without_request_timings"], [])
            self.assertEqual(
                result["record_arrival_to_belief_s"], dict.fromkeys(("p50", "p95", "p100"))
            )
            self.assertEqual(result["release_to_belief_s"], dict.fromkeys(("p50", "p95", "p100")))
            self.assertFalse(result["finite_run_gate"]["latency_passed"])
            self.assertFalse(result["sustains_offered_rate"])
            search.complete_capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()

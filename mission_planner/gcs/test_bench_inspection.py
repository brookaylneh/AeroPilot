"""Recorded rate measurements retain failures without repeating a paid request."""

import contextlib
import hashlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np
from PIL import Image

from mission_planner.agents.segmentation import encode_mask
from mission_planner.gcs import bench_inspection as bench


class InspectionRate(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, behavior):
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "black").save(image, format="JPEG")
        jpeg = image.getvalue()
        sources = [
            {"path": f"recorded/frame-{i}.jpg", "sha256": hashlib.sha256(jpeg).hexdigest()}
            for i in range(40)
        ]
        protocol = {"frames": sources, "prompts": ["person"], "sam_threshold": 0.3}
        calls = [0] * 4
        requests, clients = [], []

        def endpoint(name):
            self.assertEqual(name, "SAM")
            index = len(clients)

            def handle(request):
                calls[index] += 1
                body = json.loads(request.content)
                self.assertEqual(set(body), {"jpeg", "prompts", "threshold"})
                requests.append((index, body))
                if index == 0 and behavior == "http_error":
                    return httpx.Response(503, json={"error": "SAM unavailable"})
                if index == 0 and behavior == "overrun":
                    time.sleep(0.25)
                detection = {
                    "box_xyxy": [1, 1, 3, 3],
                    "confidence": 0.8,
                    "mask_area_px": 4,
                    "mask": encode_mask(np.ones((2, 2), dtype=bool), (1, 1)),
                }
                if index == 0 and behavior == "invalid_mask":
                    detection["mask_area_px"] = 3
                return httpx.Response(
                    200,
                    json={
                        "width": 8,
                        "height": 8,
                        "detections": {"person": [] if behavior == "empty" else [detection]},
                        "server": {"instance_id": "mock"},
                        "timings": {"forward_s": 0.001},
                    },
                )

            client = httpx.Client(
                base_url="https://sam.invalid/", transport=httpx.MockTransport(handle)
            )
            clients.append(client)
            return client

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "new-run"
            with (
                patch.object(
                    bench,
                    "recorded_frames",
                    return_value=(protocol, list(zip(sources, [jpeg] * 40))),
                ),
                patch.object(bench, "endpoint", side_effect=endpoint),
                patch.object(bench, "INTERVAL_S", 0.1),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                if behavior == "ok":
                    await bench.measure(directory)
                else:
                    with self.assertRaises(AssertionError if behavior == "empty" else RuntimeError):
                        await bench.measure(directory)
            self.assertTrue(all(client.is_closed for client in clients))
            result = json.loads((directory / "result.json").read_text())
            rows = [
                json.loads(path.read_text())
                for path in sorted(directory.glob("Drone*.json"))
                if not path.name.endswith(".response.json")
            ]
            responses = [path.read_text() for path in sorted(directory.glob("*.response.json"))]
        return result, rows, responses, calls, requests

    async def test_four_lanes_use_each_recorded_input_once_and_validate_masks(self):
        result, rows, responses, calls, requests = await self.run_case("ok")
        self.assertTrue(result["accepted"])
        self.assertEqual(
            (result["attempted"], result["successful"], result["masks_validated"]), (40, 40, 40)
        )
        self.assertEqual(calls, [10] * 4)
        self.assertEqual(len(responses), 40)
        self.assertEqual(len(requests), 40)
        self.assertEqual(len({row["source"]["path"] for row in rows}), 40)
        offsets = [
            (
                next(
                    row["scheduled_at_unix_s"]
                    for row in rows
                    if row["drone_id"] == name and row["frame"] == 0
                )
            )
            % 0.1
            for name in bench.NAMES
        ]
        for before, after in zip(offsets, offsets[1:]):
            self.assertAlmostEqual((after - before) % 0.1, 0.025, places=5)
        for index, name in enumerate(bench.NAMES):
            stream = [row for row in rows if row["drone_id"] == name]
            self.assertEqual(
                [row["source"]["path"] for row in stream],
                [f"recorded/frame-{i}.jpg" for i in range(index * 10, index * 10 + 10)],
            )
            self.assertEqual(len(result["streams"][name]["start_intervals_s"]), 9)
            self.assertTrue(all(row["mask_valid"] and row["http_status"] == 200 for row in stream))

    async def test_http_failure_stops_only_its_lane_and_retains_raw_response(self):
        result, rows, responses, calls, _ = await self.run_case("http_error")
        self.assertFalse(result["accepted"])
        self.assertEqual(calls, [1, 10, 10, 10])
        self.assertEqual(result["successful"], 30)
        failed = next(row for row in rows if row["drone_id"] == "Drone1")
        self.assertEqual(failed["http_status"], 503)
        self.assertIn("HTTPStatusError", failed["error"])
        self.assertIn('"SAM unavailable"', responses[0])

    async def test_slot_miss_is_logged_without_sending_a_late_followup(self):
        result, rows, _, calls, _ = await self.run_case("overrun")
        self.assertFalse(result["accepted"])
        self.assertEqual(calls, [1, 10, 10, 10])
        failed = next(row for row in rows if row["drone_id"] == "Drone1")
        self.assertTrue(failed["mask_valid"])
        self.assertGreater(failed["total_s"], 0.1)
        self.assertIn("missed the next", failed["error"])

    async def test_http_success_with_invalid_mask_fails_and_keeps_evidence(self):
        result, rows, responses, calls, _ = await self.run_case("invalid_mask")
        self.assertFalse(result["accepted"])
        self.assertEqual(calls, [1, 10, 10, 10])
        failed = next(row for row in rows if row["drone_id"] == "Drone1")
        self.assertEqual(failed["http_status"], 200)
        self.assertFalse(failed["mask_valid"])
        self.assertIn("SAM mask area disagrees", failed["error"])
        self.assertEqual(json.loads(responses[0])["detections"]["person"][0]["mask_area_px"], 3)

    async def test_all_negative_frames_do_not_claim_the_mask_contract_was_tested(self):
        result, _, _, calls, _ = await self.run_case("empty")
        self.assertEqual(calls, [10] * 4)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["successful"], 40)
        self.assertEqual(result["masks_validated"], 0)
        self.assertIn("No masks returned", result["failures"][0])

    async def test_existing_output_is_rejected_before_loading_inputs_or_opening_clients(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(bench, "recorded_frames") as frames,
                patch.object(bench, "endpoint") as endpoint,
            ):
                with self.assertRaises(FileExistsError):
                    await bench.measure(Path(temporary))
                frames.assert_not_called()
                endpoint.assert_not_called()


if __name__ == "__main__":
    unittest.main()

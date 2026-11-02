"""Recorded local runs preserve frame identities and terminal lane failures."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

from mission_planner.agents.test_vision import mask_box
from mission_planner.gcs import bench_inspection as bench


class InspectionRate(unittest.TestCase):
    def run_case(self, behavior):
        output = io.BytesIO()
        Image.new("RGB", (3840, 2160), "green").save(output, format="JPEG")
        jpeg = output.getvalue()
        digest = hashlib.sha256(jpeg).hexdigest()
        calls, clients = [], []

        def connect():
            name = None

            def handle(request):
                nonlocal name
                action = request.url.path.rsplit("/", 1)[1]
                body = (
                    dict(request.url.params) if action == "frame" else json.loads(request.content)
                )
                if action == "start":
                    name = body["drone_id"]
                    result = {
                        **body,
                        "instance_id": "one-sam-model",
                        "model": "sam3.1",
                        "tracker": "Object Multiplex",
                    }
                elif action == "close":
                    result = {**body, "status": "closed"}
                else:
                    self.assertEqual(request.content, jpeg)
                    self.assertEqual(request.headers["content-type"], "image/jpeg")
                    calls.append((name, int(body["frame_index"])))
                    if name == "Drone1" and behavior == "http_error":
                        return httpx.Response(503, json={"error": "Detector unavailable"})
                    result = {
                        **body,
                        "frame_index": int(body["frame_index"]),
                        "timestamp_ns": int(body["timestamp_ns"]),
                        "width": 3840,
                        "height": 2160,
                        "inference_height": 1080,
                        "sha256": "0" * 64
                        if name == "Drone1" and behavior == "wrong_frame"
                        else digest,
                        "detections": []
                        if behavior == "empty"
                        else [
                            {
                                "track_id": 1,
                                "class_name": "person",
                                "observations": 2,
                                "confidence": 0.8,
                                "box_xyxy": [100, 200, 300, 400],
                                "mask": mask_box([100, 200, 300, 400]),
                            }
                        ],
                        "timings": {
                            "decode_s": 0.01,
                            "preprocess_s": 0.01,
                            "inference_s": 0.02,
                            "postprocess_s": 0.01,
                            "queue_s": 0,
                        },
                    }
                return httpx.Response(200, json=result)

            client = httpx.Client(base_url="http://vision/", transport=httpx.MockTransport(handle))
            clients.append(client)
            return client

        with tempfile.TemporaryDirectory() as temporary:
            frames, output = Path(temporary) / "frames", Path(temporary) / "run"
            frames.mkdir()
            (frames / "source.jpg").write_bytes(jpeg)
            protocol = {
                "scope": "Mock transport contract, not measured inference",
                "target": "person",
                "threshold": 0.3,
                "lanes": {
                    name: {"frames": [{"path": "source.jpg", "sha256": digest}] * 2}
                    for name in bench.NAMES
                },
            }
            (frames / "protocol.json").write_text(json.dumps(protocol))
            with patch.object(bench, "vision_http", side_effect=connect):
                if behavior in ("http_error", "wrong_frame"):
                    with self.assertRaises(AssertionError):
                        bench.run(frames / "protocol.json", output, 1080, 4)
                else:
                    bench.run(frames / "protocol.json", output, 1080, 4)
            result = json.loads((output / "result.json").read_text())
            logs = {
                name: [
                    json.loads(line)
                    for line in (output / (name + ".jsonl")).read_text().splitlines()
                ]
                for name in bench.NAMES
            }
        self.assertTrue(all(client.is_closed for client in clients))
        return result, logs, calls

    def test_four_independent_sessions_preserve_each_frame_and_track(self):
        result, logs, calls = self.run_case("ok")
        self.assertFalse(result["failures"])
        self.assertEqual(len(calls), 8)
        for name in bench.NAMES:
            self.assertEqual([index for drone, index in calls if drone == name], [0, 1])
            self.assertEqual(result["results"][name]["frames_with_tracks"], 2)
            self.assertEqual(result["results"][name]["instance_id"], "one-sam-model")
            self.assertEqual(
                [row["action"] for row in logs[name]], ["start", "frame", "frame", "close", "end"]
            )

    def test_failed_frame_stops_only_its_lane_and_retains_exact_failure(self):
        for behavior in ("http_error", "wrong_frame"):
            with self.subTest(behavior=behavior):
                result, logs, calls = self.run_case(behavior)
                self.assertEqual(list(result["failures"]), ["Drone1"])
                self.assertEqual([index for drone, index in calls if drone == "Drone1"], [0])
                self.assertEqual(len(calls), 7)
                failure = next(row for row in logs["Drone1"] if row["action"] == "failure")
                self.assertIn(
                    "HTTP 503" if behavior == "http_error" else "AssertionError", failure["error"]
                )
                self.assertEqual([row["action"] for row in logs["Drone1"]][-2:], ["close", "end"])

    def test_empty_results_report_no_observed_tracks(self):
        result, _, calls = self.run_case("empty")
        self.assertEqual(len(calls), 8)
        self.assertTrue(all(row["frames_with_tracks"] == 0 for row in result["results"].values()))

    def test_existing_output_is_rejected_before_opening_a_client(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(bench, "vision_http") as connect,
        ):
            with self.assertRaises(FileExistsError):
                bench.run(Path(temporary), Path(temporary), 1080, 4)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()

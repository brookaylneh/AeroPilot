"""Multimodal request contracts across a process boundary, without external model calls."""

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from mission_planner.agents.model_process import WonderRequestError, model_pool
from mission_planner.agents.models import Wonder, image_inputs
from mission_planner.environment import ROOT
from mission_planner.gcs.service import evidence_message, operator_error
from mission_planner.gcs.test_inspection import model_evidence, photograph_bytes


def local_request(history):
    result = {}

    def respond(request):
        body = json.loads(request.content)
        result["request_bytes"] = len(request.content)
        result["hashes"] = [
            hashlib.sha256(base64.b64decode(part["image_url"].split(",", 1)[1])).hexdigest()
            for item in body["input"]
            if "content" in item and isinstance(item["content"], list)
            for part in item["content"]
            if part["type"] == "input_image"
        ]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Local fixture"}],
                    }
                ],
            },
        )

    model = Wonder()
    model.http.close()
    model.http = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        model.respond(None, [], "low", [], history=history)
        return result
    finally:
        model.close()


def unavailable():
    raise WonderRequestError("http", 402, "Fixture only")


class ModelProcessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_original_photo_crosses_process_by_path_and_is_attached_only_once(self):
        evidence = model_evidence(
            self.directory, {"frame_id": "source"}, [{"frame_id": "photo"}], [photograph_bytes()]
        )
        first = evidence_message(evidence, [])
        second = evidence_message(evidence, [first])
        self.assertEqual([p["type"] for p in second["content"]], ["input_text"])
        self.assertIn("previously_supplied", second["content"][0]["text"])
        self.assertNotIn("image_url", first["content"][1])
        with model_pool(1) as pool:
            result = pool.submit(local_request, [first, second]).result(timeout=15)
        self.assertEqual(result["hashes"], [hashlib.sha256(photograph_bytes()).hexdigest()])

    def test_changed_file_fails_before_any_request_and_text_history_is_preserved(self):
        evidence = model_evidence(self.directory, {}, [{"frame_id": "photo"}], [photograph_bytes()])
        message = evidence_message(evidence, [])
        Path(evidence["image_paths"][0]).write_bytes(b"changed")
        with self.assertRaisesRegex(AssertionError, "changed on disk"):
            image_inputs([message])
        text = [
            {"role": "user", "content": "Plain text"},
            {"type": "function_call_output", "output": "{}"},
        ]
        self.assertEqual(image_inputs(text), text)

    def test_provider_failure_keeps_http_status_across_process_boundary(self):
        with model_pool(1) as pool:
            with self.assertRaises(WonderRequestError) as raised:
                pool.submit(unavailable).result(timeout=15)
        self.assertIn("HTTP 402", operator_error(raised.exception))

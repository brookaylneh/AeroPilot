"""Analyst request contract and report checks against a recorded llama-server response shape."""

import copy
import io
import json
import unittest

import httpx
from jsonschema import ValidationError
from PIL import Image

from mission_planner.agents.models import Glimmer
from mission_planner.agents.perception.analyst import analyze, check_look_for, schema

LOOK_FOR = {
    "objective": "Check whether people are near the cabin.",
    "target": "a person",
    "attributes": [
        {"id": "jacket", "text": "red jacket"},
        {"id": "pack", "text": "yellow daypack"},
    ],
    "ignore": ["vehicles"],
}
SCENE = {"agl_m": 30.0, "crop_m": 5.0, "context_m": 15.0}
REPORT = {
    "observations": [
        {"attribute_id": "jacket", "visible": "yes", "note": "red upper body"},
        {"attribute_id": "pack", "visible": "cannot_tell", "note": "back not visible"},
    ],
    "reasoning": "One upright figure beside the path.",
    "decision": "escalate",
    "action": "none",
    "reason": "A person is visible.",
}


def jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (512, 512)).save(buffer, format="JPEG")
    return buffer.getvalue()


def glimmer(report, finish_reason="stop", content=None):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path.endswith("/tokenize"):
            return httpx.Response(200, json={"tokens": list(range(len(body["content"].split())))})
        message = {
            "content": json.dumps(report) if content is None else content,
            "reasoning_content": "Figure is upright and wears red.",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": finish_reason, "message": message}],
                "usage": {"completion_tokens": 40},
                "timings": {"predicted_n": 40},
            },
        )

    client = Glimmer.__new__(Glimmer)
    client.http = httpx.Client(
        base_url="https://example.test/glimmer/", transport=httpx.MockTransport(handler)
    )
    return client, requests


class Analyst(unittest.TestCase):
    def test_request_carries_brief_both_images_and_fixed_sampling(self):
        client, requests = glimmer(REPORT)
        result = analyze(client, LOOK_FOR, "person", jpeg(), jpeg(), SCENE)
        (path, body), (count_path, _) = requests
        self.assertEqual(path, "/glimmer/v1/chat/completions")
        self.assertEqual(count_path, "/glimmer/tokenize")
        content = body["messages"][1]["content"]
        self.assertEqual([c["type"] for c in content], ["image_url", "image_url", "text"])
        self.assertIn("- jacket: red jacket", content[2]["text"])
        self.assertIn("Ignore: vehicles", content[2]["text"])
        self.assertEqual(body["chat_template_kwargs"], {"reasoning_strength": "low"})
        self.assertEqual(
            body["response_format"],
            {"type": "json_schema", "json_schema": {"schema": schema(LOOK_FOR)}},
        )
        self.assertEqual((body["temperature"], body["top_p"], body["top_k"]), (1.0, 0.95, 64))
        self.assertEqual(result["template"], "analyst-v1")
        self.assertEqual(result["report"], REPORT)
        self.assertEqual(result["reasoning_tokens"], 6)

    def test_incomplete_or_invalid_reports_fail(self):
        with self.assertRaisesRegex(AssertionError, "before a complete report"):
            analyze(glimmer(REPORT, "length")[0], LOOK_FOR, "person", jpeg(), jpeg(), SCENE)
        with self.assertRaisesRegex(AssertionError, "no report"):
            analyze(glimmer(REPORT, content="")[0], LOOK_FOR, "person", jpeg(), jpeg(), SCENE)
        duplicated = copy.deepcopy(REPORT)
        duplicated["observations"][1]["attribute_id"] = "jacket"
        with self.assertRaisesRegex(AssertionError, "exactly once"):
            analyze(glimmer(duplicated)[0], LOOK_FOR, "person", jpeg(), jpeg(), SCENE)
        unsettled = {**REPORT, "decision": "recheck"}
        with self.assertRaisesRegex(AssertionError, "must name the action"):
            analyze(glimmer(unsettled)[0], LOOK_FOR, "person", jpeg(), jpeg(), SCENE)
        with self.assertRaises(ValidationError):
            analyze(
                glimmer({**REPORT, "decision": "maybe"})[0],
                LOOK_FOR,
                "person",
                jpeg(),
                jpeg(),
                SCENE,
            )

    def test_look_for_brief_is_bounded(self):
        check_look_for({**LOOK_FOR, "attributes": []})
        for invalid in (
            {**LOOK_FOR, "extra": 1},
            {**LOOK_FOR, "target": " "},
            {**LOOK_FOR, "attributes": [{"id": "Jacket", "text": "red"}]},
            {**LOOK_FOR, "attributes": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]},
            {**LOOK_FOR, "ignore": ["x"] * 5},
        ):
            with self.assertRaises(AssertionError):
                check_look_for(invalid)
        self.assertEqual(
            schema({**LOOK_FOR, "attributes": []})["properties"]["observations"]["maxItems"], 0
        )


if __name__ == "__main__":
    unittest.main()

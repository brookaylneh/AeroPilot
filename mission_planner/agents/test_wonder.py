"""Wonder replays typed history and fails without retrying provider errors."""

import json
import unittest
from unittest.mock import patch

import httpx

from mission_planner.agents.models import Wonder


class WonderContract(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.status = 200
        self.response = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Ready."}],
                }
            ],
        }
        self.history = (
            {"role": "user", "content": [{"type": "input_text", "text": "Read the fleet."}]},
        )

        def respond(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(self.status, json=self.response)

        with patch.dict("os.environ", {"MODEL_API_KEY": "fixture"}):
            self.model = Wonder()
        self.model.http.close()
        self.model.http = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.model.http.close)

    def test_high_effort_has_room_for_reasoning_and_plan(self):
        self.model.respond("Plan a mission", [], "high", [])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["reasoning"], {"effort": "high"})
        self.assertEqual(self.requests[0]["max_output_tokens"], 16384)

    def test_existing_stored_continuation_contract_is_preserved(self):
        output = {"type": "function_call_output", "call_id": "call_1", "output": "{}"}
        self.model.respond(None, [], "low", [], "resp_stored", (output,))
        self.assertEqual(self.requests[0]["input"], [output])
        self.assertEqual(self.requests[0]["previous_response_id"], "resp_stored")
        self.assertIs(self.requests[0]["store"], True)
        self.assertNotIn("include", self.requests[0])
        self.assertNotIn("prompt_cache_key", self.requests[0])
        self.assertNotIn("prompt_cache_retention", self.requests[0])

    def test_explicit_history_preserves_reasoning_phase_and_tool_pairing(self):
        reasoning = {
            "type": "reasoning",
            "id": "rs_prior",
            "summary": [],
            "encrypted_content": "encrypted-prior",
        }
        commentary = {
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "Checking the fleet."}],
        }
        call = {
            "type": "function_call",
            "name": "get_fleet",
            "arguments": "{}",
            "call_id": "call_1",
        }
        output = {"type": "function_call_output", "call_id": "call_1", "output": "{}"}
        history = (*self.history, reasoning, commentary, call, output)
        self.response["output"].insert(0, {**reasoning, "id": "rs_next"})
        response = self.model.respond(
            None,
            [],
            "high",
            [],
            instructions="Current fleet facts",
            history=history,
            cache_key="mission-1",
        )
        self.assertEqual(response, self.response)
        self.assertEqual(self.requests[0]["input"], list(history))
        self.assertEqual(self.requests[0]["prompt_cache_key"], "mission-1")
        self.assertEqual(self.requests[0]["prompt_cache_retention"], "24h")
        self.assertIs(self.requests[0]["store"], False)
        self.assertEqual(self.requests[0]["include"], ["reasoning.encrypted_content"])
        self.assertEqual(self.requests[0]["instructions"], "Current fleet facts")
        self.assertNotIn("previous_response_id", self.requests[0])

    def test_explicit_history_rejects_mixed_context_before_sending(self):
        for arguments in (
            {"text": "Additional input"},
            {"images": [b"image"]},
            {"tool_outputs": ({"type": "function_call_output"},)},
            {"previous_response_id": "resp_old"},
            {"history": ()},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(AssertionError):
                self.model.respond(
                    **{
                        "text": None,
                        "images": [],
                        "effort": "high",
                        "tools": [],
                        "history": self.history,
                        **arguments,
                    }
                )
        self.assertEqual(self.requests, [])

    def test_explicit_history_requires_replayable_reasoning(self):
        for reasoning in (
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "reasoning", "summary": [], "encrypted_content": ""},
            {"type": "reasoning", "encrypted_content": "encrypted"},
            {"type": "reasoning", "summary": "wrong", "encrypted_content": "encrypted"},
        ):
            self.response["output"] = [reasoning, {"type": "function_call"}]
            before = len(self.requests)
            with (
                self.subTest(reasoning=reasoning),
                self.assertRaisesRegex(AssertionError, "Wonder omitted"),
            ):
                self.model.respond(None, [], "high", [], history=self.history)
            self.assertEqual(len(self.requests), before + 1)

    def test_reasoning_requires_a_following_assistant_or_function_output(self):
        reasoning = {"type": "reasoning", "summary": [], "encrypted_content": "encrypted"}
        for prefix in ([], self.response["output"], [{"type": "function_call"}]):
            self.response["output"] = [*prefix, reasoning]
            before = len(self.requests)
            with (
                self.subTest(prefix=prefix),
                self.assertRaisesRegex(
                    AssertionError, "must end with an assistant message or function call"
                ),
            ):
                self.model.respond(None, [], "high", [], history=self.history)
            self.assertEqual(len(self.requests), before + 1)

    def test_provider_error_does_not_retry_or_change_context_mode(self):
        self.status = 400
        self.response = {"error": {"message": "referenced response not found or expired"}}
        with self.assertRaises(httpx.HTTPStatusError):
            self.model.respond(None, [], "high", [], history=self.history)
        self.assertEqual(len(self.requests), 1)
        self.assertIs(self.requests[0]["store"], False)
        self.assertNotIn("previous_response_id", self.requests[0])

    def test_incomplete_response_is_an_exact_failure(self):
        self.response = {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
        }
        with self.assertRaisesRegex(AssertionError, "incomplete.*max_output_tokens"):
            self.model.respond(None, [], "high", [], history=self.history)
        self.assertEqual(len(self.requests), 1)


if __name__ == "__main__":
    unittest.main()

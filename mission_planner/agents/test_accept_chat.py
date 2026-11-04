"""Paid-script failures are recorded once; later distinct scripts still run."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from mission_planner.agents.accept_chat import SCRIPTS, conversation, run


class PlannerConversations(unittest.TestCase):
    def test_request_failure_does_not_retry_or_stop_distinct_script(self):
        requests = []

        def respond(text, *args, history, **kwargs):
            self.assertIsNone(text)
            message = history[-2]["content"][0]["text"]
            requests.append(message)
            if message == "first":
                raise httpx.ReadTimeout("Recorded test timeout")
            return {
                "id": "second-response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Outside the fence."}],
                    }
                ],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 1},
                },
            }

        model = SimpleNamespace(respond=respond, http=SimpleNamespace(close=Mock()))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            recorded = path / "recorded.json"
            recorded.write_text(
                json.dumps({"vehicle": {"armed": True, "mode": "AUTO", "ground_speed_mps": 5}})
            )
            with (
                patch("mission_planner.agents.accept_chat.Wonder", return_value=model),
                patch(
                    "mission_planner.agents.accept_chat.SCRIPTS",
                    [
                        ("failed", "first", None, "high", False),
                        ("impossible", "second", None, "high", False),
                    ],
                ),
            ):
                asyncio.run(run(path / "run", recorded, None, 100.0))
            results = json.loads((path / "run/results.json").read_text())
            self.assertEqual(requests, ["first", "second"])
            self.assertIn("ReadTimeout: Recorded test timeout", results[0]["error"])
            self.assertTrue(results[1]["automatic_passed"])
            self.assertTrue((path / "run/failed/conversation.json").is_file())
        model.http.close.assert_called_once()

    def test_followup_and_tools_replay_the_same_ordered_transcript(self):
        reasoning = {"type": "reasoning", "summary": [], "encrypted_content": "encrypted"}
        question = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Which drone?"}],
        }
        commentary = {
            **question,
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "Checking current facts."}],
        }
        calls = [
            {"type": "function_call", "name": name, "arguments": "{}", "call_id": name}
            for name in ("get_mission", "get_fleet")
        ]
        outputs = [[reasoning, question], [reasoning, commentary, *calls], [question]]
        responses = [
            {
                "output": output,
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 1},
                },
            }
            for output in outputs
        ]
        model = SimpleNamespace(respond=Mock(side_effect=responses))
        with tempfile.TemporaryDirectory() as temporary:
            asyncio.run(
                conversation(
                    model,
                    ("context", "First request", "Drone1", "high", False),
                    {},
                    Path(temporary) / "conversation",
                )
            )
        requests = [call.kwargs for call in model.respond.call_args_list]
        first, second, third = (request["history"] for request in requests)
        live = first[-1]
        self.assertTrue(live["content"][0]["text"].startswith("Current mission context"))
        self.assertEqual(second, (*first[:-1], *outputs[0], second[-2], live))
        self.assertEqual(second[-2]["content"][0]["text"], "Drone1")
        self.assertEqual(third[:-3], (*second[:-1], *outputs[1]))
        self.assertEqual(
            [item["call_id"] for item in third[-3:-1]], [call["call_id"] for call in calls]
        )
        self.assertTrue(all(item["type"] == "function_call_output" for item in third[-3:-1]))
        self.assertEqual(third[-1], live)
        self.assertTrue(all('"places":' in request["instructions"] for request in requests))
        self.assertTrue(all(request["cache_key"] == "conversation" for request in requests))
        self.assertTrue(all(call.args[0] is None for call in model.respond.call_args_list))

    def test_effort_comparison_uses_the_same_script(self):
        high = next(script for script in SCRIPTS if script[0] == "change_high")
        medium = next(script for script in SCRIPTS if script[0] == "change_medium")
        self.assertEqual((high[1:3], high[4:]), (medium[1:3], medium[4:]))
        self.assertEqual((high[3], medium[3]), ("high", "medium"))
        self.assertEqual(len(SCRIPTS), 11)

    def test_conversation_records_more_than_twelve_tool_turns(self):
        outputs = [
            [
                {
                    "type": "function_call",
                    "name": "get_fleet",
                    "arguments": "{}",
                    "call_id": str(turn),
                }
            ]
            for turn in range(13)
        ]
        outputs.append(
            [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Outside the fence."}],
                }
            ]
        )
        model = SimpleNamespace(
            respond=Mock(
                side_effect=[
                    {
                        "output": output,
                        "usage": {
                            "input_tokens": 1,
                            "output_tokens": 2,
                            "input_tokens_details": {"cached_tokens": 0},
                            "output_tokens_details": {"reasoning_tokens": 1},
                        },
                    }
                    for output in outputs
                ]
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "conversation"
            result = asyncio.run(
                conversation(
                    model, ("impossible", "Check the boundary", None, "high", False), {}, directory
                )
            )
            recorded = json.loads((directory / "conversation.json").read_text())
        self.assertTrue(result["automatic_passed"])
        self.assertEqual([turn["turn"] for turn in recorded["turns"]], list(range(14)))
        self.assertEqual(len(recorded["events"]), 13)
        self.assertEqual(
            [call.kwargs["trace_path"].name for call in model.respond.call_args_list],
            [f"0-{turn}" for turn in range(14)],
        )


if __name__ == "__main__":
    unittest.main()

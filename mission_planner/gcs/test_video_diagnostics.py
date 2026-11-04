"""Browser diagnostics remain bounded records with no command authority."""

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mission_planner.gcs.service import Mission
from mission_planner.gcs.video_diagnostics import record


class VideoDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.sample = {
            "source": "video",
            "drone_id": "Drone1",
            "timestamp_ms": 1000,
            "metrics": {"framesDecoded": 30, "jitter": None, "hidden": False},
        }

    async def test_recording_never_starts_a_mission_or_calls_a_model(self):
        mission = SimpleNamespace(directory=self.directory, begin=AsyncMock(), clearing=False)
        await Mission.handle(mission, {"type": "video_diagnostic", "sample": self.sample}, None)
        mission.begin.assert_not_awaited()
        recorded = json.loads((self.directory / "browser-video.jsonl").read_text())
        self.assertEqual(recorded["metrics"], self.sample["metrics"])
        self.assertGreater(recorded["received_at_ms"], 0)

    def test_invalid_numbers_nested_payloads_and_unknown_drone_fail_without_a_write(self):
        for changes in (
            {"drone_id": "Drone5"},
            {"source": "command"},
            {"timestamp_ms": math.nan},
            {"metrics": {"jitter": math.inf}},
            {"metrics": {"request": {"type": "chat"}}},
            {"metrics": {"trace": "x" * 1001}},
            {"metrics": {f"metric{i}": i for i in range(25)}},
        ):
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                record(self.directory, {**copy.deepcopy(self.sample), **changes})
        self.assertFalse((self.directory / "browser-video.jsonl").exists())

    def test_rotation_retains_only_the_current_and_previous_bounded_log(self):
        current = self.directory / "browser-video.jsonl"
        previous = self.directory / "browser-video-previous.jsonl"
        previous.write_text("old")
        current.write_bytes(b"x" * (4 * 1024 * 1024))
        record(self.directory, self.sample)
        self.assertEqual(previous.stat().st_size, 4 * 1024 * 1024)
        self.assertLess(current.stat().st_size, 8192)
        self.assertEqual(json.loads(current.read_text())["drone_id"], "Drone1")

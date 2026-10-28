"""Scenario subprocess failures retain the native error and any earlier capture error."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from mission_planner.agents.perception.calibrate import close_scenario, scenario_response


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_eof_reports_native_stderr(self):
        process = Mock()
        process.stdout.readline = AsyncMock(return_value=b"")
        process.wait = AsyncMock(return_value=1)
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "scenario.log"
            log.write_text("Place the person before saving")
            with self.assertRaisesRegex(RuntimeError, "exited 1.*Place the person before saving"):
                await scenario_response(process, log)
        process.stdout.readline.assert_awaited_once()

    async def test_restore_failure_preserves_initial_error(self):
        process = Mock()
        process.wait = AsyncMock(return_value=1)
        original = ValueError("Capture geometry rejected")
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "scenario.log"
            log.write_text("Native reset failed")
            with self.assertRaisesRegex(
                RuntimeError, "geometry rejected.*Native reset failed"
            ) as caught:
                await close_scenario(process, log, original)
        self.assertIs(caught.exception.__cause__, original)
        process.stdin.close.assert_called_once()

    async def test_restore_timeout_preserves_initial_error(self):
        process = Mock()
        process.wait = AsyncMock(side_effect=asyncio.TimeoutError)
        original = ValueError("Capture geometry rejected")
        with self.assertRaisesRegex(TimeoutError, "geometry rejected.*exceeded 30 s") as caught:
            await close_scenario(process, Path("unused"), original)
        self.assertIs(caught.exception.__cause__, original)

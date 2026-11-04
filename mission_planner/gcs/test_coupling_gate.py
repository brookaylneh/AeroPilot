import asyncio
import unittest

from mission_planner.gcs.coupling_gate import command_batch


class CommandBatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_failure_cancels_queued_command_before_returning(self):
        queued = asyncio.Event()
        release = asyncio.Event()
        cancelled = asyncio.Event()
        executed = []

        async def rejected():
            await queued.wait()
            raise ValueError("Arm rejected")

        async def waiting():
            queued.set()
            try:
                await release.wait()
                executed.append("takeoff")
            finally:
                cancelled.set()

        with self.assertRaisesRegex(ValueError, "Arm rejected"):
            await command_batch(rejected(), waiting())
        self.assertTrue(cancelled.is_set())
        release.set()
        await asyncio.sleep(0)
        self.assertEqual(executed, [])

    async def test_caller_cancellation_waits_for_commands(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def waiting():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        batch = asyncio.create_task(command_batch(waiting()))
        await started.wait()
        batch.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await batch
        self.assertTrue(cancelled.is_set())

    async def test_success_preserves_result_order(self):
        async def result(value):
            await asyncio.sleep(0)
            return value

        self.assertEqual(
            await command_batch(result("Drone1"), result("Drone2")), ["Drone1", "Drone2"]
        )


if __name__ == "__main__":
    unittest.main()

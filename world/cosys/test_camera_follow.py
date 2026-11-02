"""Camera guidance uses MAVLink, with independent capture and cancellation."""

import asyncio
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import cosysairsim as airsim

from world.cosys.inspection import ForwardCapture


class CameraFollowTests(unittest.IsolatedAsyncioTestCase):
    def camera(self):
        camera = ForwardCapture("Drone1", Path(".local"))
        camera.follow_rpc = Mock()
        camera.follow_rpc.simGetCameraInfo.return_value = SimpleNamespace(
            pose=airsim.Pose(), fov=53.13
        )
        camera.gimbal = SimpleNamespace(
            claimed=True,
            acquire=AsyncMock(),
            release=AsyncMock(),
            center=Mock(return_value=({"status": "moved"}, {"centered": False})),
        )
        return camera

    async def test_capture_work_does_not_block_mavlink_guidance(self):
        camera = self.camera()
        blocked, release = threading.Event(), threading.Event()

        def capture_work():
            blocked.set()
            assert release.wait(2)

        pending = camera.executor.submit(capture_work)
        try:
            self.assertTrue(blocked.wait(1))
            generation = camera.begin_follow()
            box, orientation = [700, 180, 750, 300], [1, 0, 0, 0]
            result = await asyncio.wait_for(
                camera.follow(box, generation, time.monotonic() + 1, orientation), 0.5
            )
            self.assertEqual(result["status"], "moved")
            camera.gimbal.center.assert_called_once_with(box, 53.13, orientation)
            camera.follow_rpc.simSetCameraPose.assert_not_called()
            self.assertFalse(pending.done())
            camera._aim({"x": 1, "y": 2, "z": 3})
            self.assertIsNone(camera.rpc)
        finally:
            release.set()
            pending.result()
            await camera.close()

    async def test_expired_or_cancelled_guidance_never_sends_an_actuator_target(self):
        camera = self.camera()
        try:
            generation = camera.begin_follow()
            box, orientation = [700, 180, 750, 300], [1, 0, 0, 0]
            self.assertEqual(
                (await camera.follow(box, generation, time.monotonic() - 1, orientation))["status"],
                "stale",
            )
            camera.cancel_follow()
            self.assertEqual(
                (await camera.follow(box, generation, time.monotonic() + 1, orientation))["status"],
                "stopped",
            )
            camera.gimbal.center.assert_not_called()
        finally:
            await camera.close()

    async def test_delayed_release_cannot_revoke_a_new_camera_owner(self):
        camera = self.camera()
        try:
            camera.begin_follow()
            camera.cancel_follow()
            old_generation = camera.follow_generation
            camera.begin_follow()
            await camera.end_follow(old_generation)
            camera.gimbal.release.assert_not_awaited()
        finally:
            await camera.close()

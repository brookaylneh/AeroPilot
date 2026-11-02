"""Camera feedback uses image axes, bounded slew and a capture-independent RPC worker."""

import asyncio
import math
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cosysairsim as airsim
import numpy as np
from scipy.spatial.transform import Rotation

from world.cosys.inspection import ForwardCapture, follow_step


class CameraFollowTests(unittest.IsolatedAsyncioTestCase):
    def camera(self, body_yaw=0):
        camera = ForwardCapture("Drone1", Path(".local"))
        body_rotation = Rotation.from_euler("Z", body_yaw, degrees=True)
        rotation = body_rotation * Rotation.from_euler("Y", -25, degrees=True)
        camera.original_pose = airsim.Pose(airsim.Vector3r(0.6, 0, 0.05))
        rpc = camera.follow_rpc = Mock()
        rpc.simGetVehiclePose.return_value = airsim.Pose(
            airsim.Vector3r(), airsim.Quaternionr(*body_rotation.as_quat())
        )
        rpc.simGetCameraInfo.return_value = SimpleNamespace(
            fov=53.13,
            pose=airsim.Pose(airsim.Vector3r(), airsim.Quaternionr(*rotation.as_quat())),
        )
        return camera, rpc, rotation, body_rotation

    async def test_right_and_below_box_moves_the_optical_axis_right_and_down_for_every_heading(
        self,
    ):
        for heading in (0, 90, 180, 270):
            camera, rpc, before, body = self.camera(heading)
            try:
                generation = camera.begin_follow()
                result = await camera.follow([700, 360, 780, 450], generation, time.monotonic() + 1)
                self.assertEqual(result["status"], "moved")
                relative = rpc.simSetCameraPose.call_args.args[1]
                quat = [getattr(relative.orientation, k + "_val") for k in "xyzw"]
                after = body * Rotation.from_quat(quat)
                camera_ray = before.inv().apply(after.apply([1, 0, 0]))
                self.assertGreater(camera_ray[1], 0)
                self.assertGreater(camera_ray[2], 0)
                np.testing.assert_allclose(
                    [getattr(relative.position, k + "_val") for k in "xyz"], [0.6, 0, 0.05]
                )
                rpc.simSetCameraFov.assert_not_called()
                self.assertEqual(camera.view_revision, 0)
            finally:
                await camera.close()

    async def test_slow_image_worker_does_not_delay_camera_guidance(self):
        camera, rpc, _, _ = self.camera()
        blocked, release = threading.Event(), threading.Event()

        def capture_work():
            blocked.set()
            assert release.wait(2)

        pending = camera.executor.submit(capture_work)
        try:
            self.assertTrue(blocked.wait(1))
            generation = camera.begin_follow()
            await asyncio.wait_for(
                camera.follow([700, 180, 750, 300], generation, time.monotonic() + 1), 0.5
            )
            rpc.simSetCameraPose.assert_called_once()
            self.assertFalse(pending.done())
            camera._aim({"x": 1, "y": 2, "z": 3})
            self.assertIsNone(camera.rpc, "SAM aiming cannot overwrite a visual camera owner")
        finally:
            release.set()
            pending.result()
            await camera.close()

    async def test_expired_or_cancelled_commands_do_not_touch_the_camera(self):
        camera, rpc, _, _ = self.camera()
        try:
            generation = camera.begin_follow()
            box = [700, 180, 750, 300]
            self.assertEqual(
                (await camera.follow(box, generation, time.monotonic() - 1))["status"], "stale"
            )
            camera.cancel_follow()
            self.assertEqual(
                (await camera.follow(box, generation, time.monotonic() + 1))["status"], "stopped"
            )
            rpc.simSetCameraPose.assert_not_called()
        finally:
            await camera.close()

    def test_center_dead_zone_is_still_and_slew_remains_bounded_at_all_zoom_levels(self):
        delta, rates = follow_step([450, 240, 510, 300], 90, 0.1, [0, 0])
        self.assertEqual(delta, [0, 0])
        for fov in (90, 53.13, 28.07):
            rates = [0, 0]
            for _ in range(50):
                delta, rates = follow_step([880, 440, 950, 530], fov, 0.1, rates)
                self.assertTrue(all(abs(value) <= math.radians(8) * 0.1 for value in delta))
                self.assertGreater(delta[0], 0)
                self.assertLess(delta[1], 0)

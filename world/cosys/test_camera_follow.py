"""Direct camera actuation, body compensation, readback and cancellation."""

import asyncio
import math
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cosysairsim as airsim
import numpy as np
from scipy.spatial.transform import Rotation

from mission_planner.gcs.centering import quaternion_rotation
from world.cosys.inspection import ForwardCapture


class CameraRPC:
    def __init__(self):
        self.position = np.array([12.0, 45.0, -20.0])
        self.attitude = Rotation.from_euler("ZYX", [82, 3, 5], degrees=True)
        self.mount = np.array([0.6, 0, 0.05])
        self.relative = Rotation.from_euler("ZYX", [10, -45, 0], degrees=True)
        self.fov = 53.13
        self.writes = []
        self.client = SimpleNamespace(close=Mock())

    def simGetVehiclePose(self, *, vehicle_name):
        assert vehicle_name == "Drone1"
        return airsim.Pose(
            airsim.Vector3r(*self.position), airsim.Quaternionr(*self.attitude.as_quat())
        )

    def simGetCameraInfo(self, name, *, vehicle_name):
        assert (name, vehicle_name) == ("fpv", "Drone1")
        return SimpleNamespace(
            fov=self.fov,
            pose=airsim.Pose(
                airsim.Vector3r(*(self.position + self.attitude.apply(self.mount))),
                airsim.Quaternionr(*(self.attitude * self.relative).as_quat()),
            ),
        )

    def simSetCameraPose(self, name, pose, *, vehicle_name):
        assert (name, vehicle_name) == ("fpv", "Drone1")
        self.mount = np.array([getattr(pose.position, key + "_val") for key in "xyz"])
        q = pose.orientation
        self.relative = quaternion_rotation([q.w_val, q.x_val, q.y_val, q.z_val])
        self.writes.append(pose)


class CameraFollowTests(unittest.IsolatedAsyncioTestCase):
    def camera(self):
        camera = ForwardCapture("Drone1", Path(".local"))
        rpc = camera.follow_rpc = CameraRPC()
        camera.original_pose = airsim.Pose(
            airsim.Vector3r(*rpc.mount), airsim.Quaternionr(*rpc.relative.as_quat())
        )
        self.addAsyncCleanup(camera.close)
        return camera

    def observed(self, camera):
        rpc = camera.follow_rpc
        return (rpc.attitude * rpc.relative).as_quat()[[3, 0, 1, 2]].tolist()

    def test_delayed_rays_converge_while_aircraft_turns_and_banks_without_mount_or_zoom_drift(self):
        camera = self.camera()
        rpc, observed = camera.follow_rpc, self.observed(camera)
        generation = camera.begin_follow()
        previous = None
        for tick in range(350):
            rpc.attitude = Rotation.from_euler(
                "ZYX",
                [82 + tick * 0.5, 3 * math.sin(tick / 20), 5 * math.cos(tick / 20)],
                degrees=True,
            )
            with patch("world.cosys.inspection.time.monotonic", return_value=10 + tick * 0.1):
                result, diagnostic = camera._follow([680, 260, 740, 350], generation, 100, observed)
            self.assertEqual(result["status"], "moved")
            self.assertLess(diagnostic["readback_error_deg"], 1e-6)
            actual = rpc.attitude * rpc.relative
            if previous is not None:
                self.assertLessEqual(math.degrees((previous.inv() * actual).magnitude()), 1.8)
            previous = actual
            np.testing.assert_allclose(rpc.mount, [0.6, 0, 0.05], atol=1e-7)
        self.assertTrue(diagnostic["centered"], diagnostic)
        self.assertEqual(rpc.fov, 53.13)
        self.assertEqual(camera.view_revision, 0)

    def test_ignored_camera_command_fails_readback_instead_of_reporting_success(self):
        camera = self.camera()
        rpc, observed = camera.follow_rpc, self.observed(camera)
        rpc.simSetCameraPose = Mock()
        generation = camera.begin_follow()
        with patch("world.cosys.inspection.time.monotonic", return_value=10):
            camera._follow([680, 260, 740, 350], generation, 100, observed)
        with (
            patch("world.cosys.inspection.time.monotonic", return_value=10.2),
            self.assertRaisesRegex(AssertionError, "camera pose did not follow command"),
        ):
            camera._follow([680, 260, 740, 350], generation, 100, observed)
        self.assertEqual(rpc.simSetCameraPose.call_count, 2)

    async def test_capture_work_does_not_block_direct_camera_guidance(self):
        camera = self.camera()
        blocked, release = threading.Event(), threading.Event()

        def capture_work():
            blocked.set()
            assert release.wait(2)

        pending = camera.executor.submit(capture_work)
        try:
            self.assertTrue(blocked.wait(1))
            generation = camera.begin_follow()
            result = await asyncio.wait_for(
                camera.follow(
                    [700, 180, 750, 300], generation, time.monotonic() + 1, self.observed(camera)
                ),
                0.5,
            )
            self.assertEqual(result["status"], "moved")
            self.assertEqual(len(camera.follow_rpc.writes), 1)
            self.assertFalse(pending.done())
            camera._aim({"x": 1, "y": 2, "z": 3})
            self.assertIsNone(camera.rpc)
        finally:
            release.set()
            pending.result()

    async def test_expired_or_cancelled_guidance_never_sends_an_actuator_target(self):
        camera = self.camera()
        generation = camera.begin_follow()
        box, orientation = [700, 180, 750, 300], self.observed(camera)
        self.assertEqual(
            (await camera.follow(box, generation, time.monotonic() - 1, orientation))["status"],
            "stale",
        )
        camera.cancel_follow()
        self.assertEqual(
            (await camera.follow(box, generation, time.monotonic() + 1, orientation))["status"],
            "stopped",
        )
        self.assertEqual(camera.follow_rpc.writes, [])

    async def test_rpc_failure_propagates_without_retry(self):
        camera = self.camera()
        observed = self.observed(camera)
        camera.follow_rpc.simGetVehiclePose = Mock(side_effect=TimeoutError("RPC deadline"))
        generation = camera.begin_follow()
        with self.assertRaisesRegex(TimeoutError, "RPC deadline"):
            await camera.follow([700, 180, 750, 300], generation, time.monotonic() + 1, observed)
        camera.follow_rpc.simGetVehiclePose.assert_called_once()
        self.assertEqual(camera.follow_rpc.writes, [])

    async def test_cancel_during_pose_read_prevents_dispatch_and_drains_before_new_view(self):
        camera = self.camera()
        observed = self.observed(camera)
        entered, release = threading.Event(), threading.Event()
        read = camera.follow_rpc.simGetVehiclePose

        def delayed(**kwargs):
            entered.set()
            assert release.wait(2)
            return read(**kwargs)

        camera.follow_rpc.simGetVehiclePose = delayed
        generation = camera.begin_follow()
        pending = asyncio.create_task(
            camera.follow([700, 180, 750, 300], generation, time.monotonic() + 2, observed)
        )
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            camera.cancel_follow()
            pending.cancel()
            drained = asyncio.create_task(camera.end_follow())
            await asyncio.sleep(0.02)
            self.assertFalse(pending.done())
            self.assertFalse(drained.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        await drained
        self.assertEqual(camera.follow_rpc.writes, [])

    async def test_delayed_release_cannot_revoke_a_new_camera_owner(self):
        camera = self.camera()
        camera.begin_follow()
        camera.cancel_follow()
        old_generation = camera.follow_generation
        camera.begin_follow()
        diagnostic = camera.follow_diagnostic = {"centered": True}
        await camera.end_follow(old_generation)
        self.assertIs(camera.follow_diagnostic, diagnostic)

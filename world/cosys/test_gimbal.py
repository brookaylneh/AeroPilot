"""The virtual gimbal speaks MAVLink and preserves the existing fpv mount."""

import io
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import cosysairsim as airsim
import numpy as np
from pymavlink.dialects.v20 import ardupilotmega as MAV
from scipy.spatial.transform import Rotation

from mission_planner.gcs.gimbal import LOCK_FLAGS, quaternion_rotation
from mission_planner.gcs.test_gimbal import quat, wire
from world.cosys.gimbal import CosysGimbal


class CameraRPC:
    def __init__(self):
        self.position = np.array([12.0, 45.0, -20.0])
        self.attitude = Rotation.from_euler("ZYX", [82, 3, 5], degrees=True)
        self.mount = np.array([0.6, 0, 0.05])
        self.relative = Rotation.from_euler("ZYX", [10, -45, 0], degrees=True)
        self.writes = 0
        self.client = SimpleNamespace(close=Mock())

    def simGetVehiclePose(self, *, vehicle_name):
        assert vehicle_name == "Drone1"
        return airsim.Pose(
            airsim.Vector3r(*self.position), airsim.Quaternionr(*self.attitude.as_quat())
        )

    def simGetCameraInfo(self, name, *, vehicle_name):
        assert (name, vehicle_name) == ("fpv", "Drone1")
        return SimpleNamespace(
            fov=70,
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
        self.writes += 1


class VirtualGimbalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.output = io.BytesIO()
        self.connection = SimpleNamespace(
            write=self.output.write,
            mav=MAV.MAVLink(self.output, srcSystem=255, srcComponent=190),
        )
        self.gimbal = CosysGimbal("Drone1", 1, self.connection)
        self.rpc = self.gimbal.rpc = CameraRPC()
        self.addCleanup(self.gimbal.executor.shutdown, wait=True)

    def attitude(self, yaw, pitch, flags=LOCK_FLAGS):
        return wire(
            MAV.MAVLink_gimbal_device_set_attitude_message(
                1, MAV.MAV_COMP_ID_GIMBAL, flags, quat(yaw, pitch), math.nan, math.nan, math.nan
            )
        )

    def test_protocol_discovery_and_unowned_neutral_do_not_move_the_main_camera(self):
        self.gimbal._receive(
            wire(
                MAV.MAVLink_command_long_message(
                    1,
                    MAV.MAV_COMP_ID_GIMBAL,
                    MAV.MAV_CMD_REQUEST_MESSAGE,
                    0,
                    MAV.MAVLINK_MSG_ID_GIMBAL_DEVICE_INFORMATION,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            )
        )
        packets = MAV.MAVLink(None).parse_buffer(self.output.getvalue())
        self.assertEqual(
            [p.get_type() for p in packets], ["GIMBAL_DEVICE_INFORMATION", "COMMAND_ACK"]
        )
        self.assertEqual(packets[0].get_srcComponent(), MAV.MAV_COMP_ID_GIMBAL)
        self.assertAlmostEqual(packets[0].pitch_min, -math.pi / 2)
        before = self.rpc.relative.as_quat()
        self.gimbal._receive(self.attitude(0, 0, flags=0))
        self.gimbal._sample()
        self.assertEqual(self.rpc.writes, 0)
        np.testing.assert_allclose(self.rpc.relative.as_quat(), before)

    def test_earth_target_remains_stable_while_vehicle_moves_without_mount_drift(self):
        self.gimbal._sample()
        self.gimbal._receive(
            wire(MAV.MAVLink_gimbal_manager_status_message(1, 12, 1, 255, 190, 0, 0))
        )
        target = quat(math.radians(170), math.radians(-60))
        for yaw in (82, 100, 130, 179, -140):
            self.rpc.position += [1, 2, -0.5]
            self.rpc.attitude = Rotation.from_euler("ZYX", [yaw, 7, -3], degrees=True)
            self.gimbal._receive(self.attitude(math.radians(170), math.radians(-60)))
            feedback, _ = self.gimbal._sample()
            actual = self.rpc.attitude * self.rpc.relative
            self.assertLess((actual.inv() * quaternion_rotation(target)).magnitude(), 1e-7)
            reconstructed = Rotation.from_euler("Z", yaw, degrees=True) * quaternion_rotation(
                feedback
            )
            self.assertLess((actual.inv() * reconstructed).magnitude(), 1e-7)
            np.testing.assert_allclose(self.rpc.mount, [0.6, 0, 0.05], atol=1e-7)
        self.gimbal._receive(
            wire(MAV.MAVLink_gimbal_manager_status_message(2, LOCK_FLAGS, 1, 0, 0, 0, 0))
        )
        count = self.rpc.writes
        self.gimbal._sample()
        self.assertEqual(self.rpc.writes, count)

    async def test_rpc_failure_closes_worker_and_propagates_with_camera_context(self):
        self.rpc.simGetVehiclePose = Mock(side_effect=TimeoutError("RPC deadline exceeded"))
        with self.assertRaisesRegex(
            RuntimeError, "Drone1: main-camera MAVLink gimbal failed: RPC deadline"
        ):
            await self.gimbal.run()
        self.rpc.client.close.assert_called_once()
        self.assertFalse(self.gimbal.running)

    def test_invalid_quaternion_or_rate_command_never_writes_a_pose(self):
        message = self.attitude(0, -0.5)
        message.q = [0, 0, 0, 0]
        with self.assertRaisesRegex(AssertionError, "normalized"):
            self.gimbal._receive(message)
        message = self.attitude(0, -0.5)
        message.angular_velocity_z = 1
        with self.assertRaisesRegex(AssertionError, "persistent rate commands"):
            self.gimbal._receive(message)
        self.assertEqual(self.rpc.writes, 0)

"""Image-based centering and real MAVLink encoding without a running simulator."""

import asyncio
import io
import json
import math
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pymavlink.dialects.v20 import ardupilotmega as MAV
from scipy.spatial.transform import Rotation

from mission_planner.gcs.gimbal import LOCK_FLAGS, Centering, Gimbal, visible_box, wrapped


def quat(yaw, pitch):
    value = Rotation.from_euler("ZYX", [yaw, pitch, 0]).as_quat()
    return list(value[[3, 0, 1, 2]])


def wire(message, sysid=1, component=1):
    encoder = MAV.MAVLink(None, srcSystem=sysid, srcComponent=component)
    return MAV.MAVLink(None).parse_char(message.pack(encoder))


class CenteringTests(unittest.TestCase):
    def test_delayed_camera_observations_converge_at_every_heading_and_zoom(self):
        for heading in (0, 90, 179, 270):
            for pitch in (-25, -65, -88):
                for fov in (90, 53.13, 28.07):
                    controller = Centering()
                    initial = quat(math.radians(heading), math.radians(pitch))
                    current = initial
                    previous_yaw, previous_pitch = math.radians(heading), math.radians(pitch)
                    for tick in range(400):
                        result = controller.step(
                            [690, 300, 750, 390], fov, current, initial, 10 + tick * 0.1
                        )
                        self.assertLessEqual(
                            abs(wrapped(result["yaw_rad"] - previous_yaw)), math.radians(0.6) + 1e-8
                        )
                        self.assertLessEqual(
                            abs(result["pitch_rad"] - previous_pitch), math.radians(0.6) + 1e-8
                        )
                        previous_yaw, previous_pitch = result["yaw_rad"], result["pitch_rad"]
                        current = quat(previous_yaw, previous_pitch)
                    with self.subTest(heading=heading, pitch=pitch, fov=fov):
                        self.assertTrue(result["centered"], result)
                        self.assertLessEqual(abs(result["error_x_px"]), 9.6)
                        self.assertLessEqual(abs(result["error_y_px"]), 5.4)

    def test_centered_target_does_not_move_and_image_edges_are_valid(self):
        result = Centering().step([460, 240, 500, 300], 70, quat(0, -0.5), quat(0, -0.5), 1)
        self.assertEqual((result["yaw_delta_deg"], result["pitch_delta_deg"]), (0, 0))
        self.assertEqual(visible_box([940, -10, 980, 40]), [940, 0, 960, 40])
        self.assertIsNone(visible_box([970, 10, 1000, 40]))
        with self.assertRaisesRegex(AssertionError, "positive width"):
            visible_box([10, 0, 1, 10])

    def test_invalid_quaternion_is_rejected_before_control(self):
        with self.assertRaisesRegex(AssertionError, "normalized"):
            Centering().step([500, 300, 520, 340], 70, [0, 0, 0, 0], [1, 0, 0, 0], 1)


class GimbalProtocolTests(unittest.TestCase):
    def setUp(self):
        self.output = io.BytesIO()
        self.connection = SimpleNamespace(
            mav=MAV.MAVLink(self.output, srcSystem=255, srcComponent=190)
        )
        self.gimbal = Gimbal(1, self.connection, AsyncMock(), AsyncMock())
        self.gimbal.claimed = True
        capabilities = (
            MAV.GIMBAL_MANAGER_CAP_FLAGS_HAS_PITCH_AXIS
            | MAV.GIMBAL_MANAGER_CAP_FLAGS_HAS_PITCH_LOCK
            | MAV.GIMBAL_MANAGER_CAP_FLAGS_HAS_YAW_AXIS
            | MAV.GIMBAL_MANAGER_CAP_FLAGS_HAS_YAW_LOCK
        )
        info = MAV.MAVLink_gimbal_manager_information_message(
            1, capabilities, 1, -math.pi, math.pi, -math.pi / 2, math.pi / 2, -math.pi, math.pi
        )
        status = MAV.MAVLink_gimbal_manager_status_message(1, LOCK_FLAGS, 1, 255, 190, 0, 0)
        pose = MAV.MAVLink_gimbal_device_attitude_status_message(
            0, 0, 1, LOCK_FLAGS, quat(0, -0.5), math.nan, math.nan, math.nan, 0, gimbal_device_id=1
        )
        for message in (info, status, pose):
            self.gimbal.receive(wire(message))

    def test_acquisition_accepts_ardupilot_manager_rc_flags_and_device_yaw_lock(self):
        self.gimbal.claimed = False
        self.gimbal.receive(
            wire(MAV.MAVLink_gimbal_manager_status_message(1, 12, 1, 255, 190, 0, 0))
        )
        self.gimbal.until.side_effect = lambda predicate, timeout: self.assertTrue(predicate())
        asyncio.run(self.gimbal.acquire(quat(0, -0.5)))
        self.assertTrue(self.gimbal.claimed)

    def test_camera_control_emits_radians_and_finite_angles_not_unbounded_rate_commands(self):
        pose = quat(0, -0.5)
        motion, centering = self.gimbal.center([670, 200, 720, 300], 70, pose)
        encoded = json.dumps({"motion": motion, "centering": centering}, allow_nan=False)
        self.assertIs(json.loads(encoded)["centering"]["centered"], False)
        messages = MAV.MAVLink(None).parse_buffer(self.output.getvalue())
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message.get_type(), "GIMBAL_MANAGER_SET_PITCHYAW")
        self.assertEqual(
            (message.target_system, message.target_component, message.gimbal_device_id), (1, 1, 1)
        )
        self.assertTrue(math.isfinite(message.pitch) and math.isfinite(message.yaw))
        self.assertTrue(math.isnan(message.pitch_rate) and math.isnan(message.yaw_rate))
        self.assertEqual(message.flags, LOCK_FLAGS)

    def test_stale_feedback_or_changed_owner_never_sends_a_camera_target(self):
        self.gimbal.times["GIMBAL_DEVICE_ATTITUDE_STATUS"] = time.monotonic() - 2
        with self.assertRaisesRegex(AssertionError, "Stale"):
            self.gimbal.center([670, 200, 720, 300], 70, quat(0, -0.5))
        self.gimbal.times["GIMBAL_DEVICE_ATTITUDE_STATUS"] = time.monotonic()
        self.gimbal.receive(
            wire(MAV.MAVLink_gimbal_manager_status_message(2, LOCK_FLAGS, 1, 254, 190, 0, 0))
        )
        with self.assertRaisesRegex(AssertionError, "ownership changed"):
            self.gimbal.center([670, 200, 720, 300], 70, quat(0, -0.5))
        self.assertEqual(self.output.getvalue(), b"")

    def test_vehicle_frame_feedback_is_rotated_using_aircraft_heading(self):
        self.gimbal.receive(
            wire(MAV.MAVLink_global_position_int_message(1, 0, 0, 0, 0, 0, 0, 0, 12300))
        )
        self.gimbal.receive(
            wire(
                MAV.MAVLink_gimbal_device_attitude_status_message(
                    0,
                    0,
                    1,
                    LOCK_FLAGS | MAV.GIMBAL_DEVICE_FLAGS_YAW_IN_VEHICLE_FRAME,
                    quat(math.radians(15), -0.5),
                    math.nan,
                    math.nan,
                    math.nan,
                    0,
                    gimbal_device_id=1,
                )
            )
        )
        _, movement = self.gimbal.center([460, 240, 500, 300], 70, quat(math.radians(138), -0.5))
        self.assertTrue(movement["centered"])
        self.assertAlmostEqual(movement["yaw_rad"], math.radians(138), places=6)

    def test_bad_gimbal_feedback_does_not_crash_the_telemetry_receiver(self):
        self.gimbal.receive(
            wire(
                MAV.MAVLink_gimbal_device_attitude_status_message(
                    0,
                    0,
                    1,
                    LOCK_FLAGS,
                    [0, 0, 0, 0],
                    math.nan,
                    math.nan,
                    math.nan,
                    4,
                    gimbal_device_id=1,
                )
            )
        )
        with self.assertRaisesRegex(AssertionError, "failure flags"):
            self.gimbal.center([460, 240, 500, 300], 70, quat(0, -0.5))
        self.assertEqual(self.output.getvalue(), b"")

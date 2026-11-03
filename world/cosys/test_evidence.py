"""The zoom camera acquires its own pixels without commanding the tracking camera."""

import asyncio
import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cosysairsim as airsim
import numpy as np
from scipy.spatial.transform import Rotation

from world.cosys.evidence import CAMERA_ID, FOV_DEG, EvidenceCapture, target_direction


class EvidenceCameraTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.camera = EvidenceCapture("Drone2", Path(temporary.name))
        self.source = {
            "drone_id": "Drone2",
            "camera_id": "fpv",
            "width": 3840,
            "height": 2160,
            "horizontal_fov_deg": 90,
            "frame_id": "source",
            "sha256": "a" * 64,
            "timestamp_ns": 10,
            "captured_at_ms": time.time() * 1000,
            "camera_pose_local_ned": {"orientation": {"x": 0, "y": 0, "z": 0, "w": 1}},
        }
        self.detection = {"track_id": 3, "box_xyxy": [2700, 980, 3060, 1180]}
        self.rpc = Mock()
        self.camera.rpc = self.rpc
        self.pose = airsim.Pose(airsim.Vector3r(), airsim.Quaternionr())
        self.rpc.simGetCameraInfo.side_effect = lambda name, **kwargs: SimpleNamespace(
            fov=90 if name == "fpv" else FOV_DEG, pose=self.pose
        )

        def aimed(name, pose, **kwargs):
            self.assertEqual(name, CAMERA_ID)
            self.assertEqual(kwargs, {"vehicle_name": "Drone2"})
            self.pose = pose

        self.rpc.simSetCameraPose.side_effect = aimed

    def tearDown(self):
        asyncio.run(self.camera.close())

    def test_target_ray_accounts_for_the_main_cameras_world_orientation(self):
        self.source["camera_pose_local_ned"]["orientation"] = dict(
            zip("xyzw", Rotation.from_euler("z", 90, degrees=True).as_quat())
        )
        ray = target_direction(self.source, self.detection["box_xyxy"])
        np.testing.assert_allclose(ray, np.array([-0.5, 1, 0]) / np.sqrt(1.25), atol=1e-12)
        with self.assertRaises(AssertionError):
            target_direction(self.source, [-1, 0, 100, 100])

    def test_one_native_capture_is_aimed_and_does_not_touch_main_camera_control(self):
        original = copy.deepcopy(self.source)

        def captured(rpc, name, camera_id, directory, revision, *, quality):
            self.assertIs(rpc, self.rpc)
            self.assertEqual((name, camera_id, quality), ("Drone2", CAMERA_ID, 100))
            q = self.pose.orientation
            return (
                {
                    "camera_id": camera_id,
                    "timestamp_ns": 11,
                    "camera_pose_local_ned": {
                        "orientation": {k: getattr(q, k + "_val") for k in "xyzw"}
                    },
                },
                b"original-camera-jpeg",
                None,
            )

        with patch("world.cosys.evidence.capture_frame", side_effect=captured) as capture:
            metadata, jpeg = self.camera._capture(self.source, self.detection)
        capture.assert_called_once()
        self.rpc.simSetCameraPose.assert_called_once()
        self.rpc.simSetCameraFov.assert_not_called()
        self.assertEqual(jpeg, b"original-camera-jpeg")
        self.assertEqual(metadata["source_frame_id"], "source")
        self.assertEqual(metadata["source_track_id"], 3)
        self.assertEqual(metadata["zoom"], 4)
        self.assertLess(metadata["pointing_error_deg"], 1e-6)
        self.assertEqual(self.source, original)

    def test_old_target_and_wrong_optics_are_rejected_before_capture(self):
        with patch("world.cosys.evidence.capture_frame") as capture:
            self.source["captured_at_ms"] -= 3000
            with self.assertRaisesRegex(AssertionError, "too old"):
                self.camera._capture(self.source, self.detection)
            self.source["captured_at_ms"] = time.time() * 1000
            self.rpc.simGetCameraInfo.side_effect = None
            self.rpc.simGetCameraInfo.return_value = SimpleNamespace(fov=90)
            with self.assertRaisesRegex(AssertionError, "not fixed at 4x"):
                self.camera._capture(self.source, self.detection)
            capture.assert_not_called()
            self.rpc.simSetCameraPose.assert_not_called()

    def test_failed_capture_is_not_retried_or_replaced_with_main_pixels(self):
        with patch(
            "world.cosys.evidence.capture_frame", side_effect=TimeoutError("capture timed out")
        ) as capture:
            with self.assertRaisesRegex(TimeoutError, "capture timed out"):
                self.camera._capture(self.source, self.detection)
            capture.assert_called_once()
        self.rpc.simSetCameraPose.assert_called_once()


if __name__ == "__main__":
    unittest.main()

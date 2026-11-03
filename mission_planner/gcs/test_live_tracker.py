"""Native OpenCV tracks moving pixels, never a remembered display rectangle."""

import unittest

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from mission_planner.gcs.live_tracker import AXES, LiveTracker, agreement, optics


class NativeTrackerTests(unittest.TestCase):
    def setUp(self):
        self.texture = np.random.default_rng(27).integers(20, 235, (160, 100, 3), dtype=np.uint8)

    def image(self, x, y):
        image = np.full((2160, 3840, 3), 40, np.uint8)
        image[y : y + 160, x : x + 100] = self.texture
        return image

    def jpeg(self, x, y):
        ok, data = cv2.imencode(".jpg", self.image(x, y), [cv2.IMWRITE_JPEG_QUALITY, 95])
        assert ok
        return data.tobytes()

    def metadata(self, stamp, yaw=0, pitch=0):
        q = Rotation.from_euler("ZYX", [yaw, pitch, 0], degrees=True).as_quat()
        return {
            "width": 3840,
            "height": 2160,
            "timestamp_ns": stamp,
            "horizontal_fov_deg": 90,
            "camera_pose_local_ned": {"orientation": dict(zip("xyzw", q))},
        }

    def test_tiny_subject_stays_tracked_across_large_camera_rotations(self):
        source = np.full((2160, 3840, 3), 40, np.uint8)
        source[900:932, 1600:1624] = self.texture[:32, :24]
        box = [1600, 900, 1624, 932]
        tracker = LiveTracker()
        first = self.metadata(1)
        tracker.seed(source, box, first)
        K, R = optics(first)
        for tick in range(1, 5):
            metadata = self.metadata(tick + 1, tick * 3, tick)
            current_K, current_R = optics(metadata)
            homography = current_K @ AXES @ current_R.T @ R @ AXES.T @ np.linalg.inv(K)
            frame = cv2.warpPerspective(source, homography, (3840, 2160))
            points = (
                cv2.perspectiveTransform(
                    np.array([[[1600, 900], [1624, 900], [1624, 932], [1600, 932]]], np.float32),
                    homography,
                )[0]
                / 4
            )
            truth = [points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max()]
            actual = tracker.advance(frame, metadata)
            self.assertGreater(agreement(actual, truth), 0.5, (tick, actual, truth))

    def test_native_csrt_follows_a_translating_subject_without_more_sam_requests(self):
        tracker = LiveTracker()
        tracker.seed(self.image(1600, 900), [1600, 900, 1700, 1060], self.metadata(1))
        for frame in range(1, 12):
            x, y = 1600 + 16 * frame, 900 + 8 * frame
            box = tracker.advance(self.image(x, y), self.metadata(frame + 1))
            self.assertIsNotNone(box)
            self.assertLess(abs((box[0] + box[2]) / 2 - (x + 50) / 4), 6)
            self.assertLess(abs((box[1] + box[3]) / 2 - (y + 80) / 4), 6)

    def test_out_of_order_frames_do_not_mutate_tracking(self):
        tracker = LiveTracker()
        image = self.image(1600, 900)
        tracker.seed(image, [1600, 900, 1700, 1060], self.metadata(10))
        with self.assertRaisesRegex(AssertionError, "increasing capture timestamps"):
            tracker.advance(image, self.metadata(10))
        self.assertEqual(tracker.timestamp_ns, 10)

"""Native OpenCV tracks moving pixels, never a remembered display rectangle."""

import unittest

import cv2
import numpy as np

from mission_planner.gcs.live_tracker import LiveTracker


class NativeTrackerTests(unittest.TestCase):
    def setUp(self):
        self.texture = np.random.default_rng(27).integers(20, 235, (160, 100, 3), dtype=np.uint8)

    def jpeg(self, x, y):
        image = np.full((2160, 3840, 3), 40, np.uint8)
        image[y : y + 160, x : x + 100] = self.texture
        ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        assert ok
        return data.tobytes()

    def test_native_csrt_follows_a_translating_subject_without_more_sam_requests(self):
        tracker = LiveTracker()
        tracker.seed(self.jpeg(1600, 900), [1600, 900, 1700, 1060], 1)
        for frame in range(1, 12):
            x, y = 1600 + 16 * frame, 900 + 8 * frame
            box = tracker.advance(self.jpeg(x, y), frame + 1)
            self.assertIsNotNone(box)
            self.assertLess(abs((box[0] + box[2]) / 2 - (x + 50) / 4), 6)
            self.assertLess(abs((box[1] + box[3]) / 2 - (y + 80) / 4), 6)

    def test_out_of_order_frames_do_not_mutate_tracking(self):
        tracker = LiveTracker()
        image = self.jpeg(1600, 900)
        tracker.seed(image, [1600, 900, 1700, 1060], 10)
        with self.assertRaisesRegex(AssertionError, "increasing capture timestamps"):
            tracker.advance(image, 10)
        self.assertEqual(tracker.timestamp_ns, 10)

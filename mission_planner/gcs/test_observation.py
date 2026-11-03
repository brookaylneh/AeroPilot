"""Recorded speckles must not become a false subject boundary crossing."""

import base64
import copy
import io
import json
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from mission_planner.gcs.observation import coherent_detection
from mission_planner.gcs.tracking import ground_observation


def detection(mask):
    buffer = io.BytesIO()
    Image.fromarray(mask).save(buffer, format="PNG")
    return {
        "box_xyxy": [0, 0, mask.shape[1], mask.shape[0]],
        "mask": {"origin_xy": [0, 0], "png_base64": base64.b64encode(buffer.getvalue()).decode()},
    }


class MaskGeometryTests(unittest.TestCase):
    def test_recorded_island_cannot_move_person_outside_search_perimeter(self):
        record = json.loads(
            (Path(__file__).parent / "fixtures/remote-mask-island.json").read_text()
        )
        original = copy.deepcopy(record["detection"])
        cleaned = coherent_detection(original, 3840, 2160)
        self.assertEqual(cleaned["box_xyxy"], [1854, 910, 1878, 954])
        self.assertEqual(cleaned["mask_geometry"]["excluded_pixels"], 8)
        self.assertEqual(original, record["detection"])
        self.assertEqual(cleaned["mask"], original["mask"])
        before = ground_observation(record["metadata"], original["box_xyxy"])["ground"]
        after = ground_observation(record["metadata"], cleaned["box_xyxy"])["ground"]
        self.assertLess(before["north_m"], 52)
        self.assertTrue(52 < after["north_m"] < 72 and 16 < after["east_m"] < 36)
        moved = copy.deepcopy(record["metadata"])
        moved["camera_position_world_m"]["x"] -= 30
        outside = ground_observation(moved, cleaned["box_xyxy"])["ground"]
        self.assertLess(outside["north_m"], 52)

    def test_tiny_whole_object_has_no_minimum_area_cutoff(self):
        mask = np.zeros((20, 20), np.uint8)
        mask[7, 11] = 255
        result = coherent_detection(detection(mask), 20, 20)
        self.assertEqual(result["box_xyxy"], [11, 7, 12, 8])
        self.assertEqual(result["mask_geometry"]["excluded_pixels"], 0)

    def test_nearby_detached_limb_and_substantial_remote_component_are_retained(self):
        mask = np.zeros((200, 200), np.uint8)
        mask[10:30, 10:30] = 255
        mask[35, 20] = 255
        mask[150:160, 150:160] = 255
        result = coherent_detection(detection(mask), 200, 200)
        self.assertEqual(result["box_xyxy"], [10, 10, 160, 160])
        self.assertEqual(result["mask_geometry"]["excluded_pixels"], 0)

    def test_empty_or_nonbinary_sensor_masks_fail(self):
        for value in (0, 23):
            with self.subTest(value=value), self.assertRaisesRegex(AssertionError, "binary"):
                coherent_detection(detection(np.full((2, 2), value, np.uint8)), 2, 2)

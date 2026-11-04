"""Mask pixels must survive transport without changing coordinate frame or area."""

import base64
import copy
import io
import unittest

import numpy as np
from PIL import Image

from mission_planner.agents.segmentation import decode_mask, encode_mask


class MaskTests(unittest.TestCase):
    def test_irregular_mask_roundtrips_with_tile_offset(self):
        pixels = np.array([[0, 1, 0], [1, 1, 1]], dtype=bool)
        value = encode_mask(pixels, [17, 23])
        x, y, decoded = decode_mask(value, 20, 25, 4)
        self.assertEqual((x, y), (17, 23))
        np.testing.assert_array_equal(decoded, pixels)

    def test_rejects_wrong_area_and_out_of_image_tile(self):
        value = encode_mask(np.ones((2, 3), dtype=bool), [8, 5])
        for width, height, area in ((10, 10, 6), (20, 6, 6), (20, 10, 5)):
            with (
                self.subTest(width=width, height=height, area=area),
                self.assertRaises(AssertionError),
            ):
                decode_mask(value, width, height, area)

    def test_rejects_nonbinary_pixels_and_unknown_fields(self):
        image = Image.fromarray(np.array([[0, 127, 255]], dtype=np.uint8))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        value = {"origin_xy": [0, 0], "png_base64": base64.b64encode(buffer.getvalue()).decode()}
        with self.assertRaisesRegex(AssertionError, "binary"):
            decode_mask(value, 3, 1, 1)
        value = encode_mask(np.ones((2, 2), dtype=bool), [0, 0])
        altered = copy.deepcopy(value)
        altered["width"] = 2
        with self.assertRaisesRegex(AssertionError, "fields"):
            decode_mask(altered, 2, 2, 4)


if __name__ == "__main__":
    unittest.main()

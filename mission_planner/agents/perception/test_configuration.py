"""Provider benchmark image transforms match the recorded calibration format."""

import io
import unittest

from PIL import Image

from mission_planner.agents.perception.configuration import crop, jpeg, prepare_frame


class Configuration(unittest.TestCase):
    def test_transforms_match_calibration_bytes_including_black_padding(self):
        payload = jpeg(Image.linear_gradient("L").resize((3840, 2160)))
        with Image.open(io.BytesIO(payload)) as original:
            expected = jpeg(original.resize((1920, 1080), Image.Resampling.LANCZOS))
            self.assertEqual(prepare_frame(payload), expected)
            for box in ([950, 500, 970, 550], [0, 0, 10, 20], [1900, 1060, 1920, 1080]):
                center = [(box[0] + box[2]), (box[1] + box[3])]
                native = [
                    round(value)
                    for value in [
                        center[0] - 256,
                        center[1] - 256,
                        center[0] + 256,
                        center[1] + 256,
                    ]
                ]
                expected = jpeg(
                    original.crop(tuple(native)).resize((512, 512), Image.Resampling.LANCZOS)
                )
                actual, actual_box, padding = crop(payload, box)
                self.assertEqual(actual, expected)
                self.assertEqual(actual_box, native)
                self.assertEqual(any(padding), box[0] in (0, 1900))
        native1080 = jpeg(Image.new("RGB", (1920, 1080), (255, 255, 255)))
        actual, box, padding = crop(native1080, [0, 0, 10, 10])
        self.assertEqual(box, [-123, -123, 133, 133])
        self.assertEqual(padding, [123, 123, 0, 0])
        with Image.open(io.BytesIO(actual)) as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()

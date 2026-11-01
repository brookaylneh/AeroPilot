"""Tracking evidence keeps the exact observed JPEG, including return captures."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from mission_planner.gcs.inspection import tracking_frame
from mission_planner.gcs.test_inspection import image_bytes


class TrackingEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_hash_are_retained_at_a_relative_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "review-frames"
            directory.mkdir()
            jpeg = image_bytes()
            reference = tracking_frame(directory, {"timestamp_ns": 123456}, jpeg)
            self.assertEqual(reference["frame_ref"], "review-frames/123456.jpg")
            self.assertEqual((Path(folder) / reference["frame_ref"]).read_bytes(), jpeg)
            self.assertEqual(reference["sha256"], hashlib.sha256(jpeg).hexdigest())
            self.assertEqual(
                json.loads((directory / "123456.json").read_text()), {"timestamp_ns": 123456}
            )

    def test_repeated_timestamp_never_overwrites_existing_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            jpeg = image_bytes()
            tracking_frame(directory, {"timestamp_ns": 1}, jpeg)
            with self.assertRaises(FileExistsError):
                tracking_frame(directory, {"timestamp_ns": 1}, b"replacement")
            self.assertEqual((directory / "1.jpg").read_bytes(), jpeg)


if __name__ == "__main__":
    unittest.main()

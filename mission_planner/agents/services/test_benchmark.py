"""A compile speedup cannot hide lost detections or numeric drift."""

import json
import tempfile
import unittest
from pathlib import Path

from mission_planner.agents.services.benchmark import equivalence


class Equivalence(unittest.TestCase):
    def compare(self, candidate):
        original = {
            "box_xyxy": [10, 20, 30, 40],
            "confidence": 0.8,
            "mask_area_px": 100,
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, detections, concurrency in (
                ("baseline", [original], 1),
                ("compile", candidate, 1),
                ("compile", candidate, 4),
            ):
                (directory / f"sam-{name}-{concurrency}.jsonl").write_text(
                    "".join(
                        json.dumps(
                            {
                                "stream": lane,
                                "request": i,
                                "error": None,
                                "result": {"person": detections},
                            }
                        )
                        + "\n"
                        for lane in range(concurrency)
                        for i in range(40)
                    )
                )
            equivalence(directory)
            return json.loads((directory / "equivalence.json").read_text())

    def test_missing_detection_rejects_compile(self):
        result = self.compare([])
        self.assertFalse(result["accepted"])
        self.assertEqual(len(result["failures"]), 200)

    def test_mask_area_drift_rejects_compile(self):
        result = self.compare(
            [{"box_xyxy": [10, 20, 30, 40], "confidence": 0.8, "mask_area_px": 109}]
        )
        self.assertFalse(result["accepted"])

    def test_declared_tolerance_accepts_small_drift(self):
        result = self.compare(
            [{"box_xyxy": [10.25, 20, 30, 40], "confidence": 0.8005, "mask_area_px": 108}]
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(len(result["differences"]), 200)


if __name__ == "__main__":
    unittest.main()

"""CSV boundary handling must preserve all complete timing frames and reject interior gaps."""

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "engine_profile", Path(__file__).with_name("profile-engine.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CsvTimingTests(unittest.TestCase):
    def summarize(self, frames):
        header = ["GPUTime", "FrameTime", "GameThreadTime", "RenderThreadTime", "RHIThreadTime"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "engine.csv"
            with path.open("w") as stream:
                csv.writer(stream).writerows(
                    [header, *frames, header, ["[HasHeaderRowAtEnd]", "1"]]
                )
            return module.summarize(path)

    def test_incomplete_final_frame_is_explicitly_counted(self):
        result = self.summarize([[8, 16, 1, 3, 2], [10, 17, 2, 4, 3], [0, 54, 0, 0, 0]])
        self.assertEqual(result["captured_frames"], 3)
        self.assertEqual(result["complete_timing_frames"], 2)
        self.assertEqual(result["incomplete_final_frame"], [{"index": 2, "FrameTime": 54}])
        self.assertEqual(result["milliseconds"]["GPUTime"]["mean"], 9)
        self.assertEqual(result["milliseconds"]["GPUTime"]["p99"], 10)

    def test_missing_interior_gpu_timing_fails(self):
        with self.assertRaisesRegex(AssertionError, "GPUTime"):
            self.summarize([[8, 16, 1, 3, 2], [0, 17, 0, 0, 0], [10, 17, 2, 4, 3]])

    def test_nonfinite_gpu_timing_fails(self):
        with self.assertRaisesRegex(AssertionError, "GPUTime"):
            self.summarize([[float("nan"), 16, 1, 3, 2]])


if __name__ == "__main__":
    unittest.main()

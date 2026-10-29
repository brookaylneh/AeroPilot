import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from world.unreal import measure_performance as perf


class MeasurePerformanceTest(unittest.TestCase):
    def test_plan_is_pinned_and_staggered(self):
        routes = perf.plan()
        digest = hashlib.sha256(json.dumps(routes, sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, perf.PLAN_SHA256)
        firsts = [r["captureNorthM"][0] for r in routes.values()]
        self.assertEqual([b - a for a, b in zip(firsts, firsts[1:])], [4.0, 4.0, 4.0])
        for route in routes.values():
            gaps = {b - a for a, b in zip(route["captureNorthM"], route["captureNorthM"][1:])}
            self.assertEqual(gaps, {perf.SPACING_M})

    def test_frames_anchor_on_stop_event(self):
        header = "EVENTS,FrameTime,GPUTime"
        rows = [",20,10", ",20,12", "Cmd: CsvProfile STOP##0.055000,20,11", f"{header},New", "[HasHeaderRowAtEnd],1"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p.csv"
            path.write_text("\n".join([header, *rows]) + "\n")
            timeline = perf.frames(path, [99.99, 100.0], [100.05, 100.06])
            with self.assertRaises(AssertionError):
                perf.frames(path, [101.0, 101.1], [100.05, 100.06])
        self.assertEqual(timeline["gpuMs"].tolist(), [10, 12, 11])
        self.assertAlmostEqual(timeline["beginS"][0], 100.0)
        self.assertAlmostEqual(timeline["endS"][-1], 100.06)


if __name__ == "__main__":
    unittest.main()

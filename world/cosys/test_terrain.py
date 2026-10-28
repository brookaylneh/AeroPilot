"""Rectangular terrain orientation, preserved calibration geometry and one extent source."""

import importlib
import json
import re
import subprocess
import unittest

import numpy as np

from mission_planner.gcs.geodesy import fence_vertices
from mission_planner.search.grid import CELL_M, make_grid
from world.cosys.terrain import (
    BOUNDS,
    COLUMNS,
    EAST_MAX_M,
    EAST_MIN_M,
    FIELD,
    NORTH_MAX_M,
    NORTH_MIN_M,
    ROOT,
    ROWS,
    WORLD,
    heights,
    terrain_height,
)


class Terrain(unittest.TestCase):
    def test_grid_covers_both_axes(self):
        grid = make_grid(fence_vertices())
        low = np.array([NORTH_MIN_M, EAST_MIN_M])
        high = np.array([NORTH_MAX_M, EAST_MAX_M])
        np.testing.assert_array_equal(grid["low"].min(axis=0), low)
        np.testing.assert_array_equal(grid["high"].max(axis=0), high)
        np.testing.assert_array_equal(grid["shape"], np.ceil((high - low) / CELL_M).astype(int))
        self.assertAlmostEqual(float(grid["areas"].sum()), float(np.prod(high - low)), delta=1e-6)

    def test_asymmetric_sample_indices_and_edges(self):
        values = heights()
        self.assertNotEqual(ROWS, COLUMNS)
        for row, col in (
            (0, 0),
            (0, COLUMNS - 1),
            (ROWS - 1, 0),
            (ROWS - 1, COLUMNS - 1),
            (ROWS - 1, 17),
            (31, COLUMNS - 1),
            (23, 47),
            (ROWS - 37, COLUMNS - 19),
        ):
            north = NORTH_MIN_M + col * (NORTH_MAX_M - NORTH_MIN_M) / (COLUMNS - 1)
            east = EAST_MIN_M + row * (EAST_MAX_M - EAST_MIN_M) / (ROWS - 1)
            expected = (values[row * COLUMNS + col] - FIELD["zeroSample"]) / FIELD[
                "samplesPerMetre"
            ]
            self.assertAlmostEqual(terrain_height(north, east), expected, places=10)
        with self.assertRaisesRegex(ValueError, "Outside"):
            terrain_height(NORTH_MAX_M + FIELD["sampleSpacingMetres"], 0)
        with self.assertRaisesRegex(ValueError, "Outside"):
            terrain_height(0, EAST_MIN_M - FIELD["sampleSpacingMetres"])

    def test_original_square_bit_identical(self):
        generator = importlib.import_module("world.unreal.scripts.generate-open-meadow")
        old = np.fromfile(ROOT / ".local/vendor/open-meadow/height.r16", dtype="<u2").reshape(
            generator.ORIGINAL_SAMPLES, generator.ORIGINAL_SAMPLES
        )
        first = [
            round(
                (-generator.ORIGINAL_WIDTH_M / 2 - BOUNDS["min"][a] / 100)
                / FIELD["sampleSpacingMetres"]
            )
            for a in "xy"
        ]
        raw = np.array(heights()).reshape(ROWS, COLUMNS)
        central = raw[
            first[1] : first[1] + generator.ORIGINAL_SAMPLES,
            first[0] : first[0] + generator.ORIGINAL_SAMPLES,
        ]
        self.assertEqual(central.shape, old.shape)
        self.assertTrue(np.array_equal(central, old))

    def test_world_dimensions_have_one_source(self):
        north_cm = BOUNDS["max"]["x"] - BOUNDS["min"]["x"]
        east_cm = BOUNDS["max"]["y"] - BOUNDS["min"]["y"]
        numbers = {
            north_cm,
            north_cm / 100,
            north_cm / 2,
            north_cm / 200,
            east_cm,
            east_cm / 100,
            east_cm / 2,
            east_cm / 200,
            FIELD["rows"],
            FIELD["columns"],
        }
        pattern = re.compile(
            r"(?<![\w.])(?:" + "|".join(re.escape(f"{n:.15g}") for n in numbers) + r")(?![\w.])"
        )
        allowed = {"world/cosys/world.json", "world/unreal/scripts/generate-open-meadow.py"}
        paths = (
            subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
            .stdout.decode()
            .split("\0")
        )
        for name in paths:
            if (
                not name
                or name in allowed
                or name.startswith(("mission_planner/measurements/", "web/public/fonts/"))
            ):
                continue
            path = ROOT / name
            if path.suffix not in {".py", ".ts", ".tsx", ".mjs", ".cpp", ".h", ".json", ".md"}:
                continue
            self.assertIsNone(pattern.search(path.read_text()), name)
        self.assertNotIn("widthMetres", WORLD)
        self.assertNotIn("depthMetres", WORLD)
        self.assertEqual(json.loads((ROOT / "world/cosys/world.json").read_text()), WORLD)


if __name__ == "__main__":
    unittest.main()

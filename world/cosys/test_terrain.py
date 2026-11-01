"""Rectangular terrain orientation, preserved calibration geometry and one extent source."""

import importlib
import json
import re
import subprocess
import unittest

import numpy as np

from mission_planner.gcs.geodesy import fence_vertices
from mission_planner.search.grid import CELL_M, make_grid
from world.cosys.landmarks import validate
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
    launch_breaks,
    launch_triangles,
    terrain_height,
)


class Terrain(unittest.TestCase):
    def test_interior_slab_edges_are_checked_on_legs_inside_the_heightfield(self):
        from mission_planner.gcs.validation import minimum_clearance

        port = WORLD["port"]["centerUeCm"]
        centre = {"north_m": port["x"] / 100, "east_m": port["y"] / 100}
        for axis in ("north_m", "east_m"):
            a = {**centre, axis: centre[axis] - 10, "altitude_above_home_m": 20.85}
            b = {**centre, axis: centre[axis] + 10, "altitude_above_home_m": 20.85}
            cuts = launch_breaks(a, b)
            self.assertIn(0.15, cuts)
            self.assertIn(0.85, cuts)
            self.assertAlmostEqual(minimum_clearance(a, b, 0, 1, 0, 0), 9.92)
        self.assertLess(terrain_height(centre["north_m"] - 7.001, centre["east_m"]), 10.93)
        self.assertEqual(terrain_height(centre["north_m"] - 7, centre["east_m"]), 10.93)

    def test_launch_slab_and_authored_apron_cover_the_corridor(self):
        port = WORLD["port"]["centerUeCm"]
        self.assertAlmostEqual(terrain_height(port["x"] / 100, port["y"] / 100), port["z"] / 100)
        for north in (NORTH_MIN_M - 40, NORTH_MIN_M - 20, NORTH_MIN_M - 0.01):
            for east in (-25, 0, 25):
                self.assertTrue(np.isfinite(terrain_height(north, east)))
        triangles = launch_triangles()
        self.assertGreater(len(triangles), 2)
        for triangle in triangles[:-2]:
            north, east, height = np.mean(triangle, axis=0)
            if NORTH_MIN_M - 40 <= north < NORTH_MIN_M and 7 < abs(east) <= 25:
                self.assertAlmostEqual(terrain_height(north, east), height, places=8)
        cuts = launch_breaks(
            {"north_m": NORTH_MIN_M - 40, "east_m": 10},
            {"north_m": NORTH_MIN_M, "east_m": 10},
        )
        self.assertGreater(len(cuts), 2)

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
        with self.assertRaisesRegex(
            ValueError, "Outside mapped terrain at north -820.000 m, east 50.000 m"
        ):
            terrain_height(-820, 50)

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
        north, east = np.meshgrid(
            np.linspace(NORTH_MIN_M, NORTH_MAX_M, COLUMNS),
            np.linspace(EAST_MIN_M, EAST_MAX_M, ROWS),
        )
        raw = np.rint(
            FIELD["zeroSample"] + generator.height(north, east) * FIELD["samplesPerMetre"]
        ).astype("<u2")
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
            subprocess.run(["rg", "--files", "--null"], cwd=ROOT, check=True, capture_output=True)
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
            text = path.read_text()
            if name == "world/unreal/landmarks.json":
                # Authored coordinates can meet a boundary without defining the world's extent.
                document = json.loads(text)
                validate(document, WORLD)
                for landmark in document["landmarks"]:
                    del landmark["geometry"]["points"]
                text = json.dumps(document)
            self.assertIsNone(pattern.search(text), name)
        self.assertNotIn("widthMetres", WORLD)
        self.assertNotIn("depthMetres", WORLD)
        self.assertEqual(json.loads((ROOT / "world/cosys/world.json").read_text()), WORLD)


if __name__ == "__main__":
    unittest.main()

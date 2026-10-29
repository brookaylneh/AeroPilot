"""Hydrology, authored landmark constraints and deterministic valley preservation."""

import copy
import hashlib
import json
import unittest

import numpy as np

from world.cosys.landmarks import LANDMARKS_PATH
from world.cosys.terrain import FIELD, ROOT, WORLD
from world.unreal.valley import (
    SOUTH_BUFFER_M,
    axes,
    baseline_samples,
    build,
    catalogue,
    drainage,
    lookup,
    points,
)


class Drainage(unittest.TestCase):
    def test_depressions_fill_and_all_upstream_samples_reach_boundary(self):
        row, column = np.mgrid[:9, :13]
        elevation = np.hypot(row - 4, column - 6)
        original = elevation.copy()
        receiver, accumulation, filled = drainage(elevation, 2)
        self.assertEqual(filled[4, 6], 4)
        self.assertTrue(np.all(filled >= elevation))
        self.assertEqual(accumulation[receiver == -1].sum(), elevation.size)
        np.testing.assert_array_equal(elevation, original)
        for start in range(elevation.size):
            visited = set()
            index = start
            while index != -1:
                self.assertNotIn(index, visited)
                visited.add(index)
                index = receiver.ravel()[index]

    def test_steepest_descent_respects_asymmetric_axes(self):
        row, column = np.mgrid[:7, :11]
        elevation = 100 - row - 5 * column
        receiver, accumulation, filled = drainage(elevation.astype(float), 3)
        self.assertEqual(receiver[3, 5], 3 * elevation.shape[1] + 6)
        self.assertGreater(accumulation[3, 6], accumulation[3, 5])
        np.testing.assert_array_equal(filled, elevation)


class Valley(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(LANDMARKS_PATH.read_text())
        cls.raw, cls.report = build(cls.document)

    def test_generated_output_matches_active_version_and_south_is_unchanged(self):
        self.assertEqual(hashlib.sha256(self.raw.tobytes()).hexdigest(), WORLD["heightfieldSha256"])
        self.assertEqual(self.raw.tobytes(), (ROOT / FIELD["path"]).read_bytes())
        baseline = baseline_samples()
        north, _ = axes()
        south = north <= north[0] + SOUTH_BUFFER_M
        np.testing.assert_array_equal(self.raw[:, south], baseline[:, south])
        self.assertGreater(self.report["changedSamples"], 0)
        self.assertLess(self.report["maxSlopeDegrees"], 12)
        self.assertLess(self.report["reliefM"], 65)

    def test_catalogue_z_matches_final_heightfield(self):
        elevation = (self.raw.astype(float) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
        for item in self.document["landmarks"]:
            np.testing.assert_allclose(
                [p["z"] for p in item["geometry"]["points"]],
                lookup(elevation, points(item)) * 100,
                atol=0.001,
                err_msg=item["id"],
            )

    def test_authored_height_caps_and_required_geometry(self):
        for kind, height in (("structure", 15.1), ("forest", 18.1)):
            document = copy.deepcopy(self.document)
            item = next(item for item in document["landmarks"] if item["kind"] == kind)
            item["heightM"] = height
            with self.assertRaisesRegex(AssertionError, "height envelope"):
                catalogue(document)
        document = copy.deepcopy(self.document)
        pond = next(item for item in document["landmarks"] if item["kind"] == "pond")
        pond["geometry"] = {"type": "point", "points": pond["geometry"]["points"][:1]}
        with self.assertRaises(AssertionError):
            catalogue(document)

    def test_same_kind_polygons_cannot_touch_or_contain_one_another(self):
        for offset in (0, 4):
            document = copy.deepcopy(self.document)
            structures = [item for item in document["landmarks"] if item["kind"] == "structure"]
            source = structures[0]
            structures[1]["geometry"] = copy.deepcopy(source["geometry"])
            for point in structures[1]["geometry"]["points"]:
                point["y"] += offset * 100
            with self.assertRaisesRegex(AssertionError, "Same-kind"):
                catalogue(document)

    def test_creek_geometry_must_follow_computed_receiver_edges(self):
        document = copy.deepcopy(self.document)
        creek = next(item for item in document["landmarks"] if item["kind"] == "creek")
        creek["geometry"]["points"][2]["x"] += FIELD["sampleSpacingMetres"] * 800
        with self.assertRaises(AssertionError):
            build(document)

    def test_route_crossings_require_a_bridge_at_the_crossing(self):
        document = copy.deepcopy(self.document)
        bridge = next(item for item in document["landmarks"] if item["kind"] == "bridge")
        for point in bridge["geometry"]["points"]:
            point["y"] -= 5000
        with self.assertRaisesRegex(AssertionError, "Unbridged creek crossing"):
            catalogue(document)


if __name__ == "__main__":
    unittest.main()

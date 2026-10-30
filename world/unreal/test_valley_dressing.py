import copy
import unittest

import numpy as np

from world.unreal.valley_dressing import (
    HOLDERS,
    LINE_HALF_M,
    SCALE_JITTER,
    TALL_M,
    Site,
    load,
    measured,
    plan,
    scatter,
)


class DressingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dressing, cls.document, cls.receipt = load()
        cls.rows = plan(cls.dressing, cls.document, cls.receipt)
        cls.site = Site(cls.document, cls.receipt)

    def row(self, name):
        (row,) = [r for r in self.dressing["rows"] if r["name"] == name]
        return row

    def test_scatter_repeats_exactly_for_its_seed(self):
        row = self.row("dome-erratics")
        again = scatter(self.site, row, self.dressing["seed"])
        for key in ("north", "east", "yaw", "scale"):
            np.testing.assert_array_equal(again[key], self.rows["dome-erratics"][key])
        self.assertEqual(again["mesh"], self.rows["dome-erratics"]["mesh"])
        moved = scatter(self.site, row, self.dressing["seed"] + 1)
        self.assertFalse(np.array_equal(moved["north"], again["north"]))

    def test_instances_take_any_yaw_and_scale_within_15_percent(self):
        for row in self.dressing["rows"]:
            placed = self.rows[row["name"]]
            self.assertTrue(((placed["yaw"] >= 0) & (placed["yaw"] < 360)).all(), row["name"])
            ratio = placed["scale"] / row["scale"]
            self.assertTrue((abs(ratio - 1) <= SCALE_JITTER + 1e-12).all(), row["name"])
            self.assertLessEqual(set(placed["mesh"]), set(self.dressing["meshes"]))

    def test_scattered_instances_keep_trails_roads_creek_ponds_and_structures_clear(self):
        for row in self.dressing["rows"]:
            if "at" in row:
                continue
            placed = self.rows[row["name"]]
            p = np.column_stack((placed["north"], placed["east"]))
            self.assertTrue(self.site.clear(p, row["clearM"] - 1e-6).all(), row["name"])
        self.assertEqual(LINE_HALF_M, {"trail": 1.2, "road": 2.8})

    def test_forest_heights_are_the_measured_tops(self):
        heights = measured(self.rows, self.document)
        for item in self.document["landmarks"]:
            if item["kind"] == "forest" or item["id"] == "granite-dome":
                self.assertEqual(item["heightM"], heights[item["id"]], item["id"])
            if item["kind"] in HOLDERS:
                self.assertGreaterEqual(item["heightM"], heights[item["id"]], item["id"])

    def test_a_tall_instance_outside_a_high_enough_polygon_fails(self):
        dressing = dict(self.dressing, rows=[self.row("east-canopy")])
        document = copy.deepcopy(self.document)
        (forest,) = [i for i in document["landmarks"] if i["id"] == "east-lodgepoles"]
        forest["heightM"] = TALL_M + 6
        with self.assertRaises(AssertionError):
            plan(dressing, document, self.receipt)

    def test_explicit_footprints_outside_their_area_fail(self):
        row = copy.deepcopy(self.row("survey-hut"))
        row["at"][0][2] += 90
        with self.assertRaises(AssertionError):
            plan(dict(self.dressing, rows=[row]), self.document, self.receipt)

    def test_landmark_polygons_of_one_kind_may_not_overlap(self):
        document = copy.deepcopy(self.document)
        (a,) = [i for i in document["landmarks"] if i["id"] == "lake-trail-sign"]
        (b,) = [i for i in document["landmarks"] if i["id"] == "hut-junction-sign"]
        a["geometry"]["points"] = copy.deepcopy(b["geometry"]["points"])
        with self.assertRaises(AssertionError):
            Site(document, self.receipt)


if __name__ == "__main__":
    unittest.main()

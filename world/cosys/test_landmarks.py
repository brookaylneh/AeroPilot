"""Inline public-landmark fixtures; no scenario placements or authored world data."""

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from world.cosys.landmarks import directions, find, load, validate


def feature(identifier, name, kind, geometry, coordinates, aliases=()):
    return {
        "id": identifier,
        "name": name,
        "aliases": list(aliases),
        "kind": kind,
        "geometry": {
            "type": geometry,
            "points": [{"x": n * 100, "y": e * 100, "z": 0} for n, e in coordinates],
        },
    }


class LandmarkTests(unittest.TestCase):
    def setUp(self):
        self.world = {
            "worldId": "inline-test-world",
            "coordinateSystem": "UE_LH_Z_UP_CENTIMETERS",
            "north": "+X",
            "east": "+Y",
            "searchBoundsCm": {
                "min": {"x": -10000, "y": -20000, "z": 0},
                "max": {"x": 10000, "y": 20000, "z": 1000},
            },
        }
        self.document = {
            "schemaVersion": 1,
            "worldId": self.world["worldId"],
            "coordinateSystem": self.world["coordinateSystem"],
            "landmarks": [
                feature("bridge", "Footbridge", "bridge", "point", [(0, 0)], ["Bridge"]),
                feature("creek", "Creek", "creek", "polyline", [(-80, 40), (80, 40)]),
            ],
        }

    def test_load_uses_supplied_world_bounds_and_rejects_missing_files(self):
        self.document["landmarks"][0]["geometry"]["points"][0]["y"] = 19000
        with tempfile.TemporaryDirectory() as directory:
            path, world = Path(directory) / "landmarks.json", Path(directory) / "world.json"
            path.write_text(json.dumps(self.document))
            world.write_text(json.dumps(self.world))
            self.assertEqual(load(path, world), self.document["landmarks"])
            with self.assertRaises(FileNotFoundError):
                load(Path(directory) / "absent.json", world)
        self.document["landmarks"][0]["geometry"]["points"][0]["x"] = 19000
        with self.assertRaisesRegex(AssertionError, "bounds"):
            validate(self.document, self.world)

    def test_exact_case_insensitive_names_aliases_and_unknowns(self):
        landmarks = validate(self.document, self.world)
        for query in ("Footbridge", "FOOTBRIDGE", "bridge"):
            self.assertEqual([item["id"] for item in find(landmarks, query)["matches"]], ["bridge"])
        for query in ("Foot bridge", "Footbridg", " Footbridge "):
            result = find(landmarks, query)
            self.assertEqual(result, {"matches": [], "known_names": ["Creek", "Footbridge"]})
        with self.assertRaises(AssertionError):
            find(landmarks, " ")

    def test_ambiguous_alias_keeps_all_candidates_in_stable_order(self):
        self.document["landmarks"].insert(
            0, feature("other-bridge", "Upper Crossing", "bridge", "point", [(20, 0)], ["Bridge"])
        )
        result = find(validate(self.document, self.world), "BRIDGE")
        self.assertEqual([item["id"] for item in result["matches"]], ["bridge", "other-bridge"])
        self.assertEqual(result["known_names"], [])

    def test_schema_rejects_private_missing_unknown_and_duplicate_fields(self):
        variants = []
        value = copy.deepcopy(self.document)
        value["subject"] = {"x": 1, "y": 2}
        variants.append(value)
        for key, item in (
            ("clue", {}),
            ("kind", "person"),
            ("id", "Bridge with spaces"),
            ("aliases", ["footbridge"]),
            ("name", " Footbridge"),
        ):
            value = copy.deepcopy(self.document)
            value["landmarks"][0][key] = item
            variants.append(value)
        value = copy.deepcopy(self.document)
        del value["landmarks"][0]["aliases"]
        variants.append(value)
        value = copy.deepcopy(self.document)
        value["landmarks"].append(copy.deepcopy(value["landmarks"][0]))
        variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(AssertionError):
                validate(value, self.world)

    def test_rejects_wrong_world_frame_nonfinite_boolean_or_out_of_bounds_coordinates(self):
        for key, value in (("worldId", "other-world"), ("coordinateSystem", "NED_METRES")):
            document = copy.deepcopy(self.document)
            document[key] = value
            with self.assertRaises(AssertionError):
                validate(document, self.world)
        for value in (math.inf, math.nan, True, "3", 10001, -10001):
            document = copy.deepcopy(self.document)
            document["landmarks"][0]["geometry"]["points"][0]["x"] = value
            with self.subTest(value=value), self.assertRaises(AssertionError):
                validate(document, self.world)

    def test_world_id_and_bound_types_are_explicit(self):
        for value in (None, 1, True, "", " "):
            document, world = copy.deepcopy(self.document), copy.deepcopy(self.world)
            document["worldId"] = world["worldId"] = value
            with self.subTest(value=value), self.assertRaises(AssertionError):
                validate(document, world)
        for value in (True, math.inf):
            world = copy.deepcopy(self.world)
            world["searchBoundsCm"]["min"]["x"] = value
            with self.subTest(value=value), self.assertRaises(AssertionError):
                validate(self.document, world)
        self.world["searchBoundsCm"]["max"]["private"] = 1
        with self.assertRaises(AssertionError):
            validate(self.document, self.world)

    def test_geometry_rejects_linear_points_repeated_segments_and_invalid_polygons(self):
        invalid = [
            feature("a", "A", "trail", "point", [(0, 0)]),
            feature("a", "A", "road", "polyline", [(0, 0)]),
            feature("a", "A", "creek", "polyline", [(0, 0), (0, 0)]),
            feature("a", "A", "pond", "polygon", [(0, 0), (1, 0), (2, 0)]),
            feature("a", "A", "pond", "polygon", [(0, 0), (1, 0), (0, 1), (0, 0)]),
            feature("a", "A", "pond", "polygon", [(0, 0), (4, 4), (0, 4), (3, 0)]),
        ]
        for item in invalid:
            self.document["landmarks"] = [item]
            with self.subTest(item=item), self.assertRaises(AssertionError):
                validate(self.document, self.world)

    def test_projection_uses_segment_interior_and_true_horizontal_metres(self):
        result = directions(validate(self.document, self.world), 7, 38)
        self.assertEqual(result["nearest"]["id"], "creek")
        self.assertAlmostEqual(result["nearest"]["distance_m"], 2)
        self.assertEqual(result["nearest"]["point"], {"north_m": 7, "east_m": 40})
        self.assertEqual(result["linear"]["side"], "west")
        self.assertIn("west bank of Creek", result["text"])
        self.document["landmarks"][1]["geometry"]["points"].reverse()
        self.assertEqual(directions(validate(self.document, self.world), 7, 38), result)

    def test_nearest_point_compass_and_linear_side_are_separate(self):
        result = directions(validate(self.document, self.world), 3, 4)
        self.assertEqual(result["nearest"]["id"], "bridge")
        self.assertAlmostEqual(result["nearest"]["distance_m"], 5)
        self.assertEqual(result["nearest"]["compass"], "north-east")
        self.assertEqual(result["linear"]["side"], "west")
        self.assertEqual(result["text"], "5.0 m north-east of Footbridge; west bank of Creek")

    def test_line_endpoints_on_line_and_non_creek_sides(self):
        self.document["landmarks"] = [
            feature("track", "Track", "trail", "polyline", [(-10, 0), (10, 0)])
        ]
        landmarks = validate(self.document, self.world)
        self.assertEqual(directions(landmarks, 0, 0)["linear"]["side"], "on")
        result = directions(landmarks, 11, 0)
        self.assertEqual(result["linear"]["side"], "beyond endpoint")
        self.assertAlmostEqual(result["nearest"]["distance_m"], 1)
        self.assertIn("east side of Track", directions(landmarks, 0, 1)["text"])

    def test_internal_bends_and_closed_loops_are_not_line_endpoints(self):
        for coordinates in (
            [(-10, 0), (0, 0), (0, 10)],
            [(0, 0), (-10, 0), (-10, 10), (0, 10), (0, 0)],
        ):
            self.document["landmarks"] = [
                feature("track", "Track", "trail", "polyline", coordinates)
            ]
            landmarks = validate(self.document, self.world)
            result = directions(landmarks, 1, -1)
            self.assertAlmostEqual(result["nearest"]["distance_m"], math.sqrt(2))
            self.assertEqual(result["linear"]["side"], "north-west")

    def test_polygon_distance_is_zero_inside_and_exact_to_boundary_outside(self):
        self.document["landmarks"] = [
            feature("pond", "Pond", "pond", "polygon", [(0, 0), (10, 0), (10, 10), (0, 10)])
        ]
        landmarks = validate(self.document, self.world)
        for point in ((5, 5), (0, 4), (10, 10)):
            result = directions(landmarks, *point)
            self.assertEqual(result["nearest"]["distance_m"], 0)
            self.assertEqual(result["text"], "At Pond")
            self.assertIsNone(result["linear"])
        self.assertAlmostEqual(directions(landmarks, 13, 14)["nearest"]["distance_m"], 5)

    def test_ties_are_stable_and_invalid_direction_inputs_fail(self):
        self.document["landmarks"] = [
            feature("b", "B", "bridge", "point", [(0, 0)]),
            feature("a", "A", "bridge", "point", [(0, 0)]),
        ]
        landmarks = validate(self.document, self.world)
        self.assertEqual(directions(landmarks, 1, 1)["nearest"]["id"], "a")
        with self.assertRaisesRegex(AssertionError, "nonempty"):
            directions([], 0, 0)
        with self.assertRaisesRegex(AssertionError, "nonempty"):
            find([], "bridge")
        with self.assertRaises(AssertionError):
            directions(landmarks, math.nan, 0)


if __name__ == "__main__":
    unittest.main()

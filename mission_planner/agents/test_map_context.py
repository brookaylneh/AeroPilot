"""Public map compression preserves names, geometry bounds and ground coordinates."""

import math
import unittest

from mission_planner.agents.map_context import compact_map, ground_height, ground_profile
from mission_planner.gcs.geodesy import segment_distance
from world.cosys.landmarks import load
from world.cosys.terrain import terrain_height


class MapContext(unittest.TestCase):
    def test_every_place_retains_bounded_geometry_and_metadata(self):
        landmarks = load()
        compact = compact_map(landmarks)
        self.assertEqual(len(compact["places"]), len(landmarks))
        for original, place in zip(landmarks, compact["places"]):
            for key in ("id", "name", "aliases", "kind"):
                self.assertEqual(original[key], place[key])
            self.assertEqual(place["height_m"], original["heightM"])
            self.assertEqual(len(place["nearby"]), 3)
            self.assertNotIn(place["id"], [item["id"] for item in place["nearby"]])
            self.assertTrue(0 <= place["from_launch"]["bearing_deg"] <= 360)
            points = [(*point, 0) for point in place["geometry"]["points_ne_m"]]
            if original["geometry"]["type"] == "polygon":
                self.assertGreaterEqual(len(points), 3)
                points.append(points[0])
            for point in original["geometry"]["points"]:
                p = (point["x"] / 100, point["y"] / 100, 0)
                distance = min(segment_distance(p, p, a, b) for a, b in zip(points, points[1:]))
                self.assertLessEqual(distance, compact["geometry_tolerance_m"])

    def test_profile_has_endpoints_measured_heights_and_distance(self):
        points = [{"north_m": 610, "east_m": 310}, {"north_m": 615, "east_m": 337}]
        result = ground_profile(points)
        self.assertEqual(result["samples"][0]["distance_m"], 0)
        self.assertEqual(result["samples"][-1]["distance_m"], math.hypot(5, 27))
        for sample in result["samples"]:
            self.assertEqual(
                sample["ground_ue_up_m"], terrain_height(sample["north_m"], sample["east_m"])
            )
        for a, b in zip(result["samples"], result["samples"][1:]):
            self.assertLessEqual(b["distance_m"] - a["distance_m"], 10)

    def test_invalid_ground_requests_raise(self):
        with self.assertRaises(ValueError):
            ground_height(10000, 0)
        with self.assertRaisesRegex(AssertionError, "zero-length"):
            ground_profile([{"north_m": 0, "east_m": 0}] * 2)
        with self.assertRaises(AssertionError):
            ground_height(math.nan, 0)


if __name__ == "__main__":
    unittest.main()

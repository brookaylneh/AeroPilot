"""Terrain projection and bounded following without simulator or inference calls."""

import math
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from mission_planner.gcs.geodesy import ORIGIN
from mission_planner.gcs.service import planning_inspection
from mission_planner.gcs.tracking import follow_target, ground_observation
from world.cosys.landmarks import load
from world.cosys.terrain import terrain_height


class GroundFollowing(unittest.TestCase):
    def metadata(self, north, east, target):
        camera = np.array([north, east, terrain_height(north, east) + 35])
        vector = np.array([target[0], target[1], terrain_height(*target)]) - camera
        yaw = math.atan2(vector[1], vector[0])
        pitch = math.atan2(vector[2], math.hypot(vector[0], vector[1]))
        quaternion = Rotation.from_euler("ZYX", [yaw, pitch, 0]).as_quat()
        return {
            "width": 1920,
            "height": 1080,
            "horizontal_fov_deg": 90,
            "camera_position_world_m": dict(zip("xyz", camera)),
            "camera_pose_local_ned": {"orientation": dict(zip("xyzw", quaternion))},
        }

    def test_oblique_ground_ray_projects_to_public_terrain_across_map(self):
        for north, east in ((-600, 0), (0, -400), (590, 310), (100, 600)):
            with self.subTest(north=north, east=east):
                target = (north + 20, east + 10)
                metadata = self.metadata(north, east, target)
                point = ground_observation(metadata, [950, 400, 970, 540])["ground"]
                self.assertLess(math.dist([point["north_m"], point["east_m"]], target), 0.01)
                self.assertAlmostEqual(
                    point["ground_m"], terrain_height(point["north_m"], point["east_m"])
                )

    def test_horizon_and_invalid_boxes_fail(self):
        metadata = self.metadata(0, 0, (20, 0))
        metadata["camera_pose_local_ned"]["orientation"] = {"x": 0, "y": 0, "z": 0, "w": 1}
        observation = ground_observation(metadata, [950, 400, 970, 540])
        self.assertIsNone(observation["ground"])
        self.assertIn("horizon", observation["location_error"])
        with self.assertRaisesRegex(AssertionError, "Invalid tracked box"):
            ground_observation(metadata, [-1, 0, 10, 20])

    def test_projected_candidate_enters_wonder_context_without_serialization(self):
        observation = ground_observation(self.metadata(32, 228, (52, 238)), [950, 400, 970, 540])
        frame = {"detections": [observation]}
        result = planning_inspection(
            {
                "latest": frame,
                "rescue": {"track": observation, "live_frame": frame, "tracking_error": None},
            },
            load(),
        )
        for candidate in (
            result["latest"]["detections"][0],
            result["rescue"]["track"],
            result["rescue"]["live_frame"]["detections"][0],
        ):
            self.assertIn("Aspen Bend", candidate["landmark_location"])
            self.assertEqual(candidate["ground"], observation["ground"])
        self.assertTrue(all(type(value) is float for value in observation["ground"].values()))

    def test_following_moves_with_person_and_preserves_agreed_stand_off(self):
        state = {
            "north_m": 0,
            "east_m": 0,
            "home": {"altitude_msl_m": ORIGIN["altitude_msl_m"] + 11.45},
        }
        perimeter = [
            {"north_m": n, "east_m": e}
            for n, e in ((-100, -100), (100, -100), (100, 100), (-100, 100))
        ]
        first, heading = follow_target(state, {"north_m": 30, "east_m": 0}, perimeter, 15, 35)
        second, _ = follow_target(state, {"north_m": 35, "east_m": 0}, perimeter, 15, 35)
        self.assertEqual((first["north_m"], second["north_m"], heading), (15, 20, 0))
        self.assertLess(
            abs(second["altitude_above_home_m"] + 11.45 - terrain_height(20, 0) - 35), 0.126
        )

    def test_following_rejects_outside_target_or_unachievable_stand_off(self):
        state = {
            "north_m": 0,
            "east_m": 0,
            "home": {"altitude_msl_m": ORIGIN["altitude_msl_m"] + 11.45},
        }
        perimeter = [
            {"north_m": n, "east_m": e} for n, e in ((-10, -10), (10, -10), (10, 10), (-10, 10))
        ]
        with self.assertRaisesRegex(AssertionError, "Candidate left"):
            follow_target(state, {"north_m": 20, "east_m": 0}, perimeter, 5, 35)
        with self.assertRaisesRegex(AssertionError, "stand-off leaves"):
            follow_target(state, {"north_m": 5, "east_m": 0}, perimeter, 30, 35)


if __name__ == "__main__":
    unittest.main()

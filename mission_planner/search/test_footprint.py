"""Bounded terrain intersections preserve interior geometry and clip edge captures."""

import copy
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from mission_planner.agents.perception.geometry import Camera
from mission_planner.environment import ROOT
from mission_planner.search.coverage import capture_geometry, capture_pod
from mission_planner.search.footprint import domain, footprint
from world.cosys.terrain import terrain_height


class Footprints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = json.loads(
            (ROOT / ".local/data/calibration/adult-kelvin/frame-00-+00-Drone1.json").read_text()
        )

    def camera(self, north, east, altitude=4):
        frame = copy.deepcopy(self.frame)
        frame["localNedOriginUeCm"] = dict(x=0, y=0, z=0)
        frame["cameraPositionWorldM"] = dict(x=north, y=east, z=altitude)
        frame["cameraPoseLocalNed"] = {
            "position": dict(x=north, y=east, z=-altitude),
            "orientation": dict(x=0, y=-math.sqrt(0.5), z=0, w=math.sqrt(0.5)),
        }
        return Camera(frame)

    def flat_footprint(self, camera):
        border = np.array([[-5, -5, 0], [5, -5, 0], [5, 5, 0], [-5, 5, 0]])
        flat = (np.full(2, -5), np.full(2, 5), 0, 0, np.zeros(2), border)

        def ground(north, east):
            self.assertTrue(-5 <= north <= 5 and -5 <= east <= 5)
            return 0

        with (
            patch("mission_planner.search.footprint.domain", return_value=flat),
            patch("mission_planner.search.footprint.terrain_height", side_effect=ground),
            patch("mission_planner.agents.perception.geometry.terrain_height", side_effect=ground),
        ):
            return footprint(camera)

    def test_interior_footprint_matches_original_camera_exactly(self):
        camera = Camera(self.frame)
        old = [camera.ground(pixel) for pixel in ((0, 0), (3839, 0), (3839, 2159), (0, 2159))]
        self.assertEqual(footprint(camera), old)

    def test_side_and_corner_clip_without_sampling_outside_domain(self):
        for north, east, clipped_axes in ((-4.5, 0, 1), (-4.5, -4.5, 2), (-5.5, 0, 1)):
            camera = self.camera(north, east)
            vertices = self.flat_footprint(camera)
            self.assertEqual(len(vertices), 4)
            self.assertEqual(min(p["x"] for p in vertices), -5)
            if clipped_axes == 2:
                self.assertEqual(min(p["y"] for p in vertices), -5)
            for point in vertices:
                pixel = camera.project(point)
                self.assertTrue(np.all(pixel >= -1e-8))
                self.assertTrue(np.all(pixel <= [3839 + 1e-8, 2159 + 1e-8]))

    def test_wholly_outside_frame_has_no_in_world_footprint(self):
        self.assertEqual(self.flat_footprint(self.camera(-20, 0)), [])

    def test_empty_footprint_has_zero_pod_without_height_extrapolation(self):
        with patch("mission_planner.search.coverage.capture_geometry", return_value=(None, [])):
            pod, vertices = capture_pod({}, {"centers": np.zeros((4, 2))}, None)
        np.testing.assert_array_equal(pod, np.zeros(4))
        self.assertEqual(vertices, [])

    def test_measured_southern_boundary_cameras_intersect_real_heightfield(self):
        frames = json.loads(Path(__file__).with_name("test_boundary_frames.json").read_text())
        low, high, *_ = domain()
        for frame in frames:
            camera, vertices = capture_geometry(frame)
            self.assertGreaterEqual(len(vertices), 3)
            self.assertEqual(min(p["north_m"] for p in vertices), low[0])
            for vertex in vertices:
                xy = np.array([vertex["north_m"], vertex["east_m"]])
                self.assertTrue(np.all(xy >= low) and np.all(xy <= high))
                point = dict(x=xy[0], y=xy[1], z=terrain_height(*xy))
                pixel = camera.project(point)
                self.assertTrue(np.all(pixel >= -0.001), pixel)
                self.assertTrue(np.all(pixel <= [3839.001, 2159.001]), pixel)

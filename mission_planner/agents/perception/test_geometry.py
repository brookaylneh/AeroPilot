"""Camera geometry on synthetic frames."""

import math
import unittest
from unittest.mock import patch

import numpy as np

from mission_planner.agents.perception import geometry
from mission_planner.agents.perception.geometry import Camera
from world.cosys import client as sim


def frame():
    q = sim.airsim.euler_to_quaternion(0, -math.pi / 2, 0)
    return dict(
        schemaVersion=1,
        coordinateSystem="UE_LH_Z_UP_CENTIMETERS",
        vehicleId="Drone2",
        localNedOriginUeCm=dict(x=1000, y=2000, z=3000),
        width=1920,
        height=1080,
        horizontalFovDegrees=70,
        cameraPoseLocalNed=dict(position=dict(x=0, y=0, z=0), orientation=sim.quat(q)),
        cameraPositionWorldM=dict(x=10, y=20, z=30),
    )


class GeometryTests(unittest.TestCase):
    def test_cardinal_axes_and_terrain(self):
        camera = Camera(frame())
        np.testing.assert_allclose(camera.project(dict(x=10, y=20, z=0)), [960, 540])
        self.assertLess(camera.project(dict(x=11, y=20, z=0))[1], 540)
        self.assertGreater(camera.project(dict(x=10, y=21, z=0))[0], 960)
        with patch.object(geometry, "terrain_height", lambda x, y: 0.02 * x + 0.03 * y):
            self.assertLess(camera.check(), 0.5)
            ground = camera.ground([100, 900])
            self.assertLess(abs(ground["z"] - (0.02 * ground["x"] + 0.03 * ground["y"])), 0.005)

    def test_recorded_origin_and_horizon_fail(self):
        metadata = frame()
        metadata["localNedOriginUeCm"]["x"] += 100
        with self.assertRaises(AssertionError):
            Camera(metadata)
        metadata = frame()
        metadata["cameraPoseLocalNed"]["orientation"] = dict(x=0, y=0, z=0, w=1)
        with self.assertRaises(AssertionError):
            Camera(metadata).ground([960, 540])


if __name__ == "__main__":
    unittest.main()

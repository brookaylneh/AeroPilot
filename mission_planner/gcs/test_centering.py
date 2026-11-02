"""Centering converges on delayed image rays with bounded camera rotation."""

import math
import unittest

from scipy.spatial.transform import Rotation

from mission_planner.gcs.centering import Centering, visible_box, wrapped


def quat(yaw, pitch):
    value = Rotation.from_euler("ZYX", [yaw, pitch, 0]).as_quat()
    return list(value[[3, 0, 1, 2]])


class CenteringTests(unittest.TestCase):
    def test_delayed_camera_observations_converge_at_every_heading_and_zoom(self):
        for heading in (0, 90, 179, 270):
            for pitch in (-25, -65, -88):
                for fov in (90, 53.13, 28.07):
                    controller = Centering()
                    initial = quat(math.radians(heading), math.radians(pitch))
                    current = initial
                    previous_yaw, previous_pitch = math.radians(heading), math.radians(pitch)
                    for tick in range(400):
                        result = controller.step(
                            [690, 300, 750, 390], fov, current, initial, 10 + tick * 0.1
                        )
                        self.assertLessEqual(
                            abs(wrapped(result["yaw_rad"] - previous_yaw)), math.radians(0.6) + 1e-8
                        )
                        self.assertLessEqual(
                            abs(result["pitch_rad"] - previous_pitch), math.radians(0.6) + 1e-8
                        )
                        previous_yaw, previous_pitch = result["yaw_rad"], result["pitch_rad"]
                        current = quat(previous_yaw, previous_pitch)
                    with self.subTest(heading=heading, pitch=pitch, fov=fov):
                        self.assertTrue(result["centered"], result)
                        self.assertLessEqual(abs(result["error_x_px"]), 9.6)
                        self.assertLessEqual(abs(result["error_y_px"]), 5.4)

    def test_centered_target_does_not_move_and_image_edges_are_valid(self):
        result = Centering().step([460, 240, 500, 300], 70, quat(0, -0.5), quat(0, -0.5), 1)
        self.assertEqual((result["yaw_delta_deg"], result["pitch_delta_deg"]), (0, 0))
        self.assertEqual(visible_box([940, -10, 980, 40]), [940, 0, 960, 40])
        self.assertIsNone(visible_box([970, 10, 1000, 40]))
        with self.assertRaisesRegex(AssertionError, "positive width"):
            visible_box([10, 0, 1, 10])

    def test_invalid_quaternion_is_rejected_before_control(self):
        with self.assertRaisesRegex(AssertionError, "normalized"):
            Centering().step([500, 300, 520, 340], 70, [0, 0, 0, 0], [1, 0, 0, 0], 1)

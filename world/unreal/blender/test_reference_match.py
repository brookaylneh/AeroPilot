import unittest

import numpy as np

from world.unreal.blender.reference_match import (
    PANEL,
    basis,
    composite,
    corners,
    eye,
    fit_master,
    fit_panel,
    foreground,
    iou,
    nadir,
    project,
)
from world.unreal.blender.spec import SIZE, VIEWS, load

BOUNDS = np.array([[-4.93, -3.33, 0.0], [4.72, 3.33, 5.78]])


class CameraTests(unittest.TestCase):
    def test_master_fit_recovers_a_known_camera(self):
        truth = np.array([15.2, 11.9, 2.0, np.radians(-129.3), 4570.0, 198.0])
        model = [c["modelM"] for c in load("structures")["ranger-cabin"]["correspondences"]]
        pixels = project(model, truth, SIZE)
        points = [
            {"name": str(i), "modelM": m, "pixel": p} for i, (m, p) in enumerate(zip(model, pixels))
        ]
        camera = fit_master(points, SIZE)
        self.assertLess(camera["rmsPixels"], 1e-3)
        np.testing.assert_allclose(camera["positionM"], truth[:3], atol=1e-3)
        self.assertAlmostEqual(camera["yawDegrees"], np.degrees(truth[3]), places=3)

    def test_basis_is_right_handed_with_up_on_z_for_level_views(self):
        for yaw, pitch in VIEWS.values():
            right, up, forward = basis(*np.radians([yaw, pitch]))
            np.testing.assert_allclose(np.cross(right, forward), up, atol=1e-12)
            np.testing.assert_allclose(np.linalg.det([right, up, forward]), -1, atol=1e-12)
        right, up, forward = basis(*np.radians(VIEWS["front"]))
        np.testing.assert_allclose(
            [right, up, forward], [[0, 1, 0], [0, 0, 1], [-1, 0, 0]], atol=1e-12
        )

    def test_panel_camera_maps_bounds_onto_the_silhouette_box(self):
        bounds = np.array([[-5.0, -2.0, 0.0], [4.6, 3.0, 4.8]])
        mask = np.zeros((PANEL, PANEL), bool)
        mask[400:1000, 100:1300] = True
        camera = fit_panel(mask, "left", bounds)
        self.assertAlmostEqual(camera["metresPerPixel"], 0.008)
        right, up, _ = basis(*np.radians(VIEWS["left"]))
        offsets = (corners(bounds) - camera["positionM"]) / camera["metresPerPixel"]
        pixels = np.stack([PANEL / 2 + offsets @ right, PANEL / 2 - offsets @ up], axis=1)
        np.testing.assert_allclose([pixels.min(0), pixels.max(0)], [[100, 400], [1300, 1000]])

    def test_nadir_and_eye_frame_the_bounds(self):
        camera = nadir(BOUNDS)
        self.assertAlmostEqual(camera["groundMetresPerPixel"], 30 * np.tan(np.radians(35)) / 1920)
        self.assertEqual(camera["positionM"][2], 30)
        width, height = camera["resolution"]
        # Image up is the asset front, so the 9.65 m depth spans the crop height.
        self.assertGreater(height, width)
        self.assertLess(height, 2160)
        camera = eye(BOUNDS)
        self.assertEqual(camera["positionM"][2], 1.7)
        right, up, forward = basis(np.radians(camera["yawDegrees"]), 0)
        offsets = corners(BOUNDS) - camera["positionM"]
        depth = offsets @ forward
        x, y = camera["focalPixels"] * np.stack([offsets @ right, offsets @ up]) / depth
        self.assertTrue((depth > 0).all())
        self.assertAlmostEqual(max(np.abs(x).max() / 1920, np.abs(y).max() / 1080), 0.45)


class CompareTests(unittest.TestCase):
    def test_composite_and_masks(self):
        rgba = np.zeros((200, 200, 4), np.uint8)
        rgba[50:150, 50:150] = (40, 40, 40, 255)
        colour = np.array([208.0, 209.0, 208.0])
        image = composite(rgba, colour)
        self.assertEqual(tuple(image[0, 0]), (208, 209, 208))
        mask = foreground(image, colour)
        self.assertEqual(mask.sum(), mask[50:150, 50:150].sum())
        self.assertGreater(mask.sum(), 0.99 * 100 * 100)
        square = np.zeros((10, 10), bool)
        square[:, :6] = True
        self.assertAlmostEqual(iou(square, square[:, ::-1]), 2 / 10)


if __name__ == "__main__":
    unittest.main()

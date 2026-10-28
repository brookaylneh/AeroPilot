import unittest

from world.unreal.horizon import outside_rectangle


class HorizonClippingTests(unittest.TestCase):
    def test_south_triangle_is_returned_unchanged(self):
        triangle = ((-4.0, 0.0, 9.0), (-2.0, 1.0, 7.0), (-2.0, -3.0, 4.0))
        clipped = outside_rectangle(triangle, (-1, -2), (1, 2))
        self.assertIs(clipped[0], triangle)

    def test_inside_is_removed(self):
        self.assertEqual(outside_rectangle(((0, 0), (0, 1), (0.5, 0)), (-1, -2), (1, 2)), [])

    def test_crossing_interpolates_height_and_uv_without_changing_outside_surface(self):
        triangle = ((-2.0, 0.0, 0.0, 0.0), (2.0, 0.0, 4.0, 1.0), (0.0, 4.0, 10.0, 0.5))
        clipped = outside_rectangle(triangle, (-1, -1), (1, 1))
        area = 0
        for a, b, c in clipped:
            for x, y, height, uv in (a, b, c):
                self.assertAlmostEqual(height, x + 2 * y + 2)
                self.assertAlmostEqual(uv, (x + 2) / 4)
            center_x = (a[0] + b[0] + c[0]) / 3
            center_y = (a[1] + b[1] + c[1]) / 3
            self.assertFalse(-1 < center_x < 1 and -1 < center_y < 1)
            area += ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
        self.assertAlmostEqual(area, 6)

    def test_boundary_triangle_remains(self):
        triangle = ((-1, -3), (-1, 3), (-2, 0))
        self.assertEqual(outside_rectangle(triangle, (-1, -2), (1, 2)), [triangle])

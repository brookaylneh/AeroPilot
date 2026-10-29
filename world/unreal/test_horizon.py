import math
import unittest

from world.cosys.terrain import BOUNDS
from world.unreal.horizon import CREST_LAYERS, CREST_ROWS, crests, outside_rectangle


class CrestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lower = (BOUNDS["min"]["x"], BOUNDS["min"]["y"])
        cls.upper = (BOUNDS["max"]["x"], BOUNDS["max"]["y"])
        cls.layers = crests(cls.lower, cls.upper)

    def test_seeded_layers_repeat_exactly(self):
        self.assertEqual(len(self.layers), len(CREST_LAYERS))
        self.assertEqual(crests(self.lower, self.upper), self.layers)

    def test_every_vertex_stays_2_km_outside_the_rectangle_within_its_height_range(self):
        for (vertices, _), (_, _, high) in zip(self.layers, CREST_LAYERS):
            gap = min(
                math.hypot(
                    max(self.lower[0] - x, 0, x - self.upper[0]),
                    max(self.lower[1] - y, 0, y - self.upper[1]),
                )
                for x, y, _ in vertices
            )
            self.assertGreaterEqual(gap, 200000)
            self.assertTrue(all(0 <= z <= high * 100 for *_, z in vertices))

    def test_triangles_index_vertices_and_face_upward(self):
        for vertices, triangles in self.layers:
            for triangle in triangles:
                a, b, c = (vertices[i] for i in triangle)
                u = [b[k] - a[k] for k in range(3)]
                v = [c[k] - a[k] for k in range(3)]
                self.assertGreater(u[0] * v[1] - u[1] * v[0], 0)

    def test_farther_crests_show_between_nearer_ones(self):
        centre = [(self.lower[k] + self.upper[k]) / 2 for k in (0, 1)]
        rows = len(CREST_ROWS)
        angles = [
            [
                math.atan2(
                    vertices[i + rows // 2][2], math.dist(vertices[i + rows // 2][:2], centre)
                )
                for i in range(0, len(vertices), rows)
            ]
            for vertices, _ in self.layers
        ]
        for near, far in zip(angles, angles[1:]):
            shown = sum(f > n for n, f in zip(near, far)) / len(near)
            self.assertTrue(0.1 < shown < 0.9, shown)


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

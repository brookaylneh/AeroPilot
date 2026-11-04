import math
import unittest

from world.cosys.terrain import BOUNDS, terrain_height
from world.unreal.horizon import APRON_RINGS, CREST_LAYERS, CREST_ROWS, apron, crests


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


class ApronTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.radius = 2_000_000
        cls.vertices, cls.triangles = apron(cls.radius)
        cls.count = APRON_RINGS + 2

    def test_spokes_start_on_edge_samples_over_a_skirt_under_the_landscape(self):
        self.assertEqual(len(self.vertices) % self.count, 0)
        for k in range(0, len(self.vertices), self.count):
            (sx, sy, sz), (x, y, z) = self.vertices[k : k + 2]
            self.assertTrue(
                x in (BOUNDS["min"]["x"], BOUNDS["max"]["x"])
                or y in (BOUNDS["min"]["y"], BOUNDS["max"]["y"])
            )
            self.assertEqual(z, 100 * terrain_height(x / 100, y / 100))
            self.assertLess(sz, 100 * terrain_height(sx / 100, sy / 100) - 40)
            self.assertAlmostEqual(
                math.hypot(*self.vertices[k + self.count - 1][:2]), self.radius, places=3
            )

    def test_triangles_index_vertices_and_face_upward(self):
        for triangle in self.triangles:
            a, b, c = (self.vertices[i] for i in triangle)
            self.assertGreater((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]), 0)

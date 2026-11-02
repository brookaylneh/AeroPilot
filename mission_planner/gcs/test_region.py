"""Doctrine region construction against the public landmark catalogue and default fences."""

import math
import unittest

import numpy as np
from shapely import Point, Polygon, union_all

from mission_planner.gcs.fences import default_fences
from mission_planner.gcs.region import (
    DISTANCE_KM,
    SUB_RING_M,
    bands,
    cells,
    compass_degrees,
    dispersion_mass,
    distance_cdf,
    ipp_geometry,
    polygon_points,
    rationale,
    samples,
    searchable_area,
    sector,
)
from mission_planner.gcs.rejection import PlanRejected
from world.cosys.landmarks import load

LANDMARKS = {landmark["id"]: landmark for landmark in load()}
FENCES = default_fences()
AREA = searchable_area(FENCES)
RATE = 4 * 20 * 5.0  # four drones, 20 m spacing, 5 m/s


class Doctrine(unittest.TestCase):
    def test_distance_cdf_hits_the_published_quantiles(self):
        for category, (_, _, radii) in DISTANCE_KM.items():
            with self.subTest(category=category):
                values = distance_cdf(category, None, np.array(radii) * 1000)
                np.testing.assert_allclose(values, [0.25, 0.5, 0.75, 0.95], atol=1e-9)
                grid = distance_cdf(category, None, np.linspace(0, 30000, 301))
                self.assertTrue(np.all(np.diff(grid) >= 0))
                self.assertEqual(float(distance_cdf(category, None, 0)), 0)
                self.assertGreater(float(distance_cdf(category, None, 60000)), 0.99)
        self.assertAlmostEqual(float(distance_cdf("elderly", 400, 200)), 0.25)
        self.assertEqual(float(distance_cdf("elderly", 400, 800)), 1)

    def test_dispersion_mass_follows_the_hiker_quartiles_and_child_uses_them_as_proxy(self):
        for category in ("hiker", "child"):
            self.assertAlmostEqual(dispersion_mass(category, 0, 2), 0.25)
            self.assertAlmostEqual(dispersion_mass(category, 2, 23), 0.25)
            self.assertAlmostEqual(dispersion_mass(category, 0, 180), 1.0)
        self.assertAlmostEqual(dispersion_mass("dementia", 0, 11), 0.25)
        self.assertEqual(compass_degrees("south-east"), 135)
        with self.assertRaisesRegex(AssertionError, "Unknown compass"):
            compass_degrees("upwards")

    def test_point_ipp_cells_tile_without_overlap_and_rank_the_core_first(self):
        geometry = Point(0, 0)
        result = cells(geometry, 0.0, "child", None, AREA)
        total = sum(c["polygon"].area for c in result)
        self.assertAlmostEqual(
            union_all([c["polygon"] for c in result]).area, total, delta=total * 0.01
        )
        # Density is uniform inside the first quartile disc, so ties resolve nearest-first.
        first = bands(result, 10 * 60, RATE)[0]["cells"][0]
        self.assertEqual((first["ring"], first["sector"]), (0, 0))
        core = sorted((c for c in result if c["sector"] == 0), key=lambda c: c["ring"])
        peak = max(c["density"] for c in core)
        self.assertTrue(
            all(a["density"] >= b["density"] - 0.01 * peak for a, b in zip(core, core[1:]))
        )
        self.assertGreater(core[0]["density"], core[-1]["density"])
        same_ring = {}
        for c in result:
            if c["ring"] == 2:
                same_ring.setdefault(c["sector"], c["density"])
        self.assertGreater(same_ring[0], same_ring[1])
        self.assertGreater(same_ring[1], same_ring[2])
        self.assertTrue(all(c["polygon"].area >= 400 for c in result))
        self.assertTrue(all(c["polygon"].geom_type == "Polygon" for c in result))
        self.assertEqual(result[0]["angle_deg"], [0.0, 23.0])

    def test_polyline_ipp_covers_the_direction_from_every_witness_point(self):
        geometry = ipp_geometry(LANDMARKS["fire-road"])
        self.assertEqual(geometry.geom_type, "LineString")
        result = cells(geometry, 45.0, "child", None, AREA)
        forward = union_all([c["polygon"] for c in result if c["sector"] <= 1])
        for north, east in samples(geometry):
            target = Point(
                north + 200 * math.cos(math.radians(45)), east + 200 * math.sin(math.radians(45))
            )
            if target.within(union_all([c["polygon"] for c in result])):
                self.assertTrue(forward.buffer(1).contains(target), (north, east))
        self.assertTrue(sector((0, 0), 90, 2, 23, 100).is_valid)

    def test_no_direction_gives_whole_rings_with_cdf_mass(self):
        result = cells(Point(0, 0), None, "dementia", None, AREA)
        self.assertTrue(all(c["sector"] == 0 for c in result))
        first = next(c for c in result if c["ring"] == 0)
        self.assertAlmostEqual(first["mass"], float(distance_cdf("dementia", None, SUB_RING_M)))
        self.assertAlmostEqual(first["polygon"].area, math.pi * SUB_RING_M**2, delta=300)

    def test_cells_respect_the_fence_inset_and_exclusions(self):
        fences = {
            **FENCES,
            "exclusions": [
                [
                    {"north_m": n, "east_m": e}
                    for n, e in ((50, 50), (50, 150), (150, 150), (150, 50))
                ]
            ],
        }
        result = cells(Point(0, 0), None, "child", None, searchable_area(fences))
        shape = union_all([c["polygon"] for c in result])
        # The inward buffer approximates round corners with chords; flight legs are checked exactly
        # against the fence later, and survey rows keep a further half-spacing margin.
        inclusion = Polygon([(p["north_m"], p["east_m"]) for p in FENCES["inclusion"]])
        self.assertGreaterEqual(inclusion.exterior.distance(shape), 10 - 0.05)
        exclusion = Polygon([(50, 50), (50, 150), (150, 150), (150, 50)])
        self.assertGreaterEqual(exclusion.distance(shape), 10 - 0.05)
        self.assertFalse(shape.contains(Point(100, 100)))

    def test_bands_fill_the_budget_in_density_order_and_stay_connected(self):
        result = cells(ipp_geometry(LANDMARKS["hut-junction-sign"]), 135.0, "child", None, AREA)
        budget = 20 * 60
        grouped = bands(result, budget, RATE)
        self.assertGreater(len(grouped), 1)
        self.assertEqual([b["index"] for b in grouped], list(range(1, len(grouped) + 1)))
        self.assertEqual(grouped[0]["status"], "planned")
        self.assertTrue(all(b["status"] == "provisional" for b in grouped[1:]))
        self.assertLessEqual(grouped[0]["survey_s"], budget)
        self.assertGreater(grouped[0]["survey_s"], budget * 0.5)
        self.assertEqual(sum(len(b["cells"]) for b in grouped), len(result))
        total = sum(c["polygon"].area for c in result)
        self.assertAlmostEqual(sum(b["area_m2"] for b in grouped), total, delta=1)
        for band in grouped:
            self.assertEqual(band["polygon"].geom_type, "Polygon")
            self.assertTrue(band["polygon"].is_valid)
        self.assertGreater(grouped[0]["mass"], grouped[1]["mass"] / 2)
        points = polygon_points(grouped[0]["polygon"])
        self.assertGreaterEqual(len(points), 3)
        sentences = rationale("child", None, 135.0, "south-east", None, grouped[0], len(result))
        self.assertIn("n=903", sentences[0])
        self.assertIn("hiker table stands in", sentences[1])
        self.assertIn("Band 1 holds", sentences[2])

    def test_unpublished_category_requires_an_explicit_radius(self):
        with self.assertRaises(PlanRejected) as caught:
            cells(Point(0, 0), 0.0, "elderly", None, AREA)
        self.assertEqual(caught.exception.rejection["rule"], "no_distance_table")
        result = cells(Point(0, 0), 0.0, "elderly", 400.0, AREA)
        self.assertTrue(all(c["outer_m"] <= 400 for c in result))
        self.assertAlmostEqual(sum(c["mass"] for c in result), 1.0, delta=0.02)
        sentences = rationale(
            "elderly", 400.0, 0.0, "north", None, bands(result, 600, RATE)[0], len(result)
        )
        wedge = cells(Point(0, 0), 0.0, "elderly", 400.0, AREA, half_angle_deg=30.0)
        self.assertTrue(all(c["angle_deg"] == [0.0, 30.0] for c in wedge))
        self.assertAlmostEqual(sum(c["mass"] for c in wedge), 1.0, delta=0.02)
        self.assertIn(
            "±30° wedge",
            rationale("elderly", 400.0, 0.0, "north", 30.0, bands(wedge, 600, RATE)[0], len(wedge))[
                1
            ],
        )
        self.assertIn("No published distance model", sentences[0])


if __name__ == "__main__":
    unittest.main()

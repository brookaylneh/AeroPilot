"""Published marginal values, exact feature geometry and normalized mixture boundaries."""

import copy
import json
import unittest

import numpy as np
from scipy.integrate import quad

from mission_planner.environment import ROOT
from mission_planner.search.behavior import (
    FEATURES,
    FIND_PERCENT,
    OFFSET_M,
    condition,
    domain_for,
    footprint_exposure,
    line_distance,
    offset,
)
from mission_planner.search.prior import QUANTILES, table


def feature(identity, kind, geometry, vertices):
    return {
        "id": identity,
        "name": identity,
        "aliases": [],
        "kind": kind,
        "heightM": 0,
        "geometry": {
            "type": geometry,
            "points": [{"x": n * 100, "y": e * 100, "z": 0} for n, e in vertices],
        },
    }


class Behavior(unittest.TestCase):
    def setUp(self):
        n, e = np.meshgrid(np.arange(0, 8, 2), np.arange(0, 8, 2), indexing="ij")
        low = np.column_stack([n.ravel(), e.ravel()]).astype(float)
        self.grid = {
            "low": low,
            "high": low + 2,
            "centers": low + 1,
            "areas": np.full(len(low), 4.0),
        }
        self.radial = np.arange(1, len(low) + 1, dtype=float)
        self.radial /= self.radial.sum()
        self.landmarks = [
            feature(
                "building", "structure", "polygon", [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5)]
            ),
            feature("wood", "forest", "polygon", [(4, 4), (8, 4), (8, 8), (4, 8)]),
            feature("water", "pond", "polygon", [(0, 6), (2, 6), (2, 8), (0, 8)]),
            *[
                feature(kind, kind, "polyline", [(0, east), (8, east)])
                for kind, east in (("road", 1), ("trail", 3), ("creek", 5))
            ],
        ]

    def test_source_find_offset_and_radial_numbers(self):
        self.assertEqual(
            FIND_PERCENT,
            {
                "Structure": 20,
                "Road": 18,
                "Linear": 9,
                "Drainage": 9,
                "Water": 7,
                "Brush": 6,
                "Woods": 17,
                "Field": 14,
            },
        )
        self.assertEqual(sum(FIND_PERCENT.values()), 100)
        np.testing.assert_array_equal(OFFSET_M, [0, 4, 15, 71, 307])
        np.testing.assert_array_equal(QUANTILES, [0, 0.25, 0.5, 0.75, 0.95])
        self.assertEqual(
            FEATURES,
            {
                "Structure": "structure",
                "Road": "road",
                "Linear": "trail",
                "Drainage": "creek",
                "Water": "pond",
                "Woods": "forest",
            },
        )
        metadata = table("dementia", "temperate_mountainous")[2]
        self.assertEqual(metadata["n"], 95)
        self.assertEqual(metadata["radius_m"], [300, 800, 1900, 8300])
        self.assertEqual(metadata["source"]["page"], 164)
        _, mixture = condition(self.grid, self.radial, self.landmarks)
        self.assertEqual(mixture["source"]["page"], 165)
        self.assertEqual(mixture["source"]["find_n"], 207)
        self.assertEqual(mixture["source"]["track_offset_n"], 110)

    def test_offset_knots_monotonicity_density_continuity_and_total_mass(self):
        np.testing.assert_allclose(offset(OFFSET_M), QUANTILES, rtol=0, atol=1e-15)
        distances = np.r_[np.linspace(0, 307, 10001), np.geomspace(307.01, 1e6, 10000)]
        values = offset(distances)
        self.assertTrue(np.isfinite(values).all())
        self.assertTrue((np.diff(values) >= 0).all())
        self.assertEqual(values[0], 0)
        self.assertEqual(values[-1], 1)
        self.assertTrue((offset(distances, True) >= 0).all())
        for knot in OFFSET_M[1:]:
            self.assertAlmostEqual(
                float(offset(knot - 1e-8, True)), float(offset(knot + 1e-8, True)), places=8
            )
        intervals = list(zip(OFFSET_M, np.r_[OFFSET_M[1:], np.inf]))
        integrated = sum(
            quad(lambda distance: float(offset(distance, True)), a, b, epsabs=1e-11)[0]
            for a, b in intervals
        )
        self.assertAlmostEqual(integrated, 1, places=10)
        tail = quad(lambda distance: float(offset(distance, True)), 307, np.inf, epsabs=1e-11)[0]
        self.assertAlmostEqual(tail, 0.05, places=10)
        for value in (-1, np.nan, np.inf):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                offset(value)

    def test_all_six_published_tables_are_preserved_in_measurements(self):
        tables = json.loads((ROOT / "mission_planner/measurements/places.json").read_text())[
            "behavior"
        ]["source_tables"]
        expected = {
            "find_location": {
                "n": 207,
                "percent": {name.lower(): value for name, value in FIND_PERCENT.items()},
                "percent_sum": 100,
            },
            "track_offset": {"n": 110, "metres": [4, 15, 71, 307]},
            "radial": {"n": 95, "kilometres": [0.3, 0.8, 1.9, 8.3]},
            "mobility": {"n": 42, "hours": [0, 0.25, 3.8, 18]},
            "dispersion": {"n": 11, "degrees": [11, 23, 66, 70]},
            "elevation": {
                "n": None,
                "percent": {"uphill": 19, "down": 42, "same": 39},
                "median_feet": {"uphill": 75, "down": 60},
            },
        }
        self.assertEqual(set(tables), set(expected))
        for name, fields in expected.items():
            with self.subTest(table=name):
                actual = tables[name]
                self.assertEqual(actual["source"], "dementia")
                page = 165 if name in ("find_location", "track_offset") else 164
                self.assertEqual(actual["printed_page"], page)
                self.assertEqual(actual["pdf_page"], page - 160)
                for key, value in fields.items():
                    self.assertEqual(actual[key], value)
                if name not in ("find_location", "elevation"):
                    self.assertEqual(actual["percentiles"], [25, 50, 75, 95])
        self.assertEqual(tables["radial"]["verbatim_column"], ["Temperate", "Mtn"])
        self.assertNotIn("verbatim_column", tables["track_offset"])

    def test_polyline_distance_uses_segment_interiors_and_endpoint_caps(self):
        path = feature("turn", "trail", "polyline", [(0, 0), (0, 4), (3, 4)])
        points = np.array([[-2, 0], [1, 2], [4, 6], [1, 4]])
        expected = [2, 1, np.sqrt(5), 0]
        np.testing.assert_allclose(line_distance(points, [path]), expected, atol=1e-14)
        path["geometry"]["points"].reverse()
        np.testing.assert_allclose(line_distance(points, [path]), expected, atol=1e-14)
        extra = feature("other", "trail", "polyline", [(3, 6), (5, 6)])
        self.assertEqual(line_distance(points, [path, extra])[2], 0)
        invalid = feature("repeat", "trail", "polyline", [(0, 0), (0, 0)])
        with self.assertRaises(AssertionError):
            line_distance(points, [invalid])

    def test_polygon_overlap_is_cell_area_fraction_not_center_membership(self):
        exposure = footprint_exposure(self.grid, self.landmarks[:1])
        expected = np.zeros(16)
        expected[0] = 0.25
        np.testing.assert_allclose(exposure, expected, rtol=0, atol=1e-15)
        self.assertEqual(float(self.grid["areas"] @ exposure), 1)
        clipped = feature("clipped", "structure", "polygon", [(-1, -1), (1, -1), (1, 1), (-1, 1)])
        np.testing.assert_allclose(
            footprint_exposure(self.grid, [clipped]), expected, rtol=0, atol=1e-15
        )

    def test_within_class_area_aggregates_disjoint_footprints_and_rejects_touching(self):
        first = feature("first", "structure", "polygon", [(0, 0), (1, 0), (1, 1), (0, 1)])
        second = feature("second", "structure", "polygon", [(4, 4), (7, 4), (7, 5), (4, 5)])
        exposure = footprint_exposure(self.grid, [first, second])
        self.assertEqual(float(self.grid["areas"] @ exposure), 4)
        for vertices in (
            [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5)],
            [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)],
            [(1, 0), (2, 0), (2, 1), (1, 1)],
        ):
            other = feature("invalid", "structure", "polygon", vertices)
            with (
                self.subTest(vertices=vertices),
                self.assertRaisesRegex(AssertionError, "non-touching"),
            ):
                footprint_exposure(self.grid, [first, other])

    def test_mixture_normalizes_each_class_and_retains_unmapped_twenty_percent(self):
        before = self.radial.copy()
        landmarks = copy.deepcopy(self.landmarks)
        probability, metadata = condition(self.grid, self.radial, self.landmarks)
        exposures = {}
        exposures["Structure"] = np.zeros(16)
        exposures["Structure"][0] = 0.25
        exposures["Water"] = np.zeros(16)
        exposures["Water"][3] = 1
        exposures["Woods"] = ((self.grid["low"] >= 4).all(axis=1)).astype(float)
        east = self.grid["centers"][:, 1]
        for label, location in (("Road", 1), ("Linear", 3), ("Drainage", 5)):
            exposures[label] = (
                offset(abs(east - 0.5 - location), True) + offset(abs(east + 0.5 - location), True)
            ) / (2 * offset(0, True))
        supported = np.zeros(16)
        for label, exposure in exposures.items():
            weighted = self.radial * exposure
            supported += FIND_PERCENT[label] / 100 * weighted / weighted.sum()
        np.testing.assert_allclose(probability - supported, 0.2 * self.radial, atol=1e-16)
        self.assertAlmostEqual(probability.sum(), 1)
        self.assertTrue(np.isfinite(probability).all())
        self.assertTrue(((probability >= 0) & (probability <= 1)).all())
        np.testing.assert_array_equal(before, self.radial)
        self.assertEqual(landmarks, self.landmarks)
        rows = {row["source_class"]: row for row in metadata["components"]}
        self.assertAlmostEqual(rows["Brush"]["weight"] + rows["Field"]["weight"], 0.2)
        self.assertEqual(rows["Structure"]["area_m2"], 1)
        self.assertEqual(rows["Water"]["area_m2"], 4)
        self.assertEqual(rows["Woods"]["area_m2"], 16)
        self.assertEqual(rows["Brush"]["area_m2"], 64)
        self.assertEqual(rows["Field"]["radial_normalizer"], 1)

    def test_missing_classes_and_invented_point_footprints_fail(self):
        for kind in FEATURES.values():
            landmarks = [item for item in self.landmarks if item["kind"] != kind]
            with self.subTest(kind=kind), self.assertRaisesRegex(AssertionError, "lacks authored"):
                condition(self.grid, self.radial, landmarks)
        with self.assertRaisesRegex(AssertionError, "public landmark"):
            condition(self.grid, self.radial, None)
        for kind in ("structure", "forest", "pond"):
            landmarks = [
                feature("point", kind, "point", [(1, 1)]) if item["kind"] == kind else item
                for item in self.landmarks
            ]
            with (
                self.subTest(kind=kind),
                self.assertRaisesRegex(AssertionError, "authored polygon"),
            ):
                condition(self.grid, self.radial, landmarks)
        self.landmarks[0] = feature(
            "outside", "structure", "polygon", [(20, 20), (21, 20), (21, 21), (20, 21)]
        )
        with self.assertRaisesRegex(AssertionError, "No in-world support"):
            condition(self.grid, self.radial, self.landmarks)

    def test_partial_fence_cells_fail_instead_of_double_discounting_area(self):
        self.grid["areas"][0] = 2
        with self.assertRaisesRegex(AssertionError, "partial fence clipping is unsupported"):
            condition(self.grid, self.radial, self.landmarks)

    def test_domain_is_consistent_and_unsupported_categories_fail(self):
        self.assertEqual(domain_for("dementia"), "temperate_mountainous")
        self.assertEqual(domain_for("child_td_1_16"), "temperate")
        for category in ("hiker", "elderly_non_dementia", "unknown"):
            with (
                self.subTest(category=category),
                self.assertRaisesRegex(AssertionError, "No sourced Sierra"),
            ):
                domain_for(category)


if __name__ == "__main__":
    unittest.main()

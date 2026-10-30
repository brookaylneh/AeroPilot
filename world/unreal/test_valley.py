"""Hydrology, landforms, layer masks, authored landmark constraints and valley preservation."""

import copy
import hashlib
import json
import unittest

import numpy as np
from scipy.ndimage import label
from scipy.spatial import ConvexHull

from world.cosys.landmarks import LANDMARKS_PATH
from world.cosys.terrain import COLUMNS, FIELD, ROOT, ROWS, WORLD
from world.unreal.valley import (
    BASINS,
    MASKS,
    MAX_SLOPE_DEGREES,
    MEADOW_M,
    ROAD_GRADE,
    SHAPED_SLOPE_DEGREES,
    SOUTH_BUFFER_M,
    TRAIL_SLOPE_DEGREES,
    axes,
    baseline_samples,
    build,
    catalogue,
    drainage,
    inside,
    limit_slope,
    line_field,
    lookup,
    points,
    region,
    signed_distance,
    triangle_slopes,
)


class Drainage(unittest.TestCase):
    def test_depressions_fill_and_all_upstream_samples_reach_boundary(self):
        row, column = np.mgrid[:9, :13]
        elevation = np.hypot(row - 4, column - 6)
        original = elevation.copy()
        receiver, accumulation, filled = drainage(elevation, 2)
        self.assertEqual(filled[4, 6], 4)
        self.assertTrue(np.all(filled >= elevation))
        self.assertEqual(accumulation[receiver == -1].sum(), elevation.size)
        np.testing.assert_array_equal(elevation, original)
        for start in range(elevation.size):
            visited = set()
            index = start
            while index != -1:
                self.assertNotIn(index, visited)
                visited.add(index)
                index = receiver.ravel()[index]

    def test_steepest_descent_respects_asymmetric_axes(self):
        row, column = np.mgrid[:7, :11]
        elevation = 100 - row - 5 * column
        receiver, accumulation, filled = drainage(elevation.astype(float), 3)
        self.assertEqual(receiver[3, 5], 3 * elevation.shape[1] + 6)
        self.assertGreater(accumulation[3, 6], accumulation[3, 5])
        np.testing.assert_array_equal(filled, elevation)


class TriangleSlopes(unittest.TestCase):
    def test_asymmetric_quad_includes_both_diagonals(self):
        elevation = np.array([[0.0, 0.0], [1.0, 2.0]])
        np.testing.assert_allclose(
            triangle_slopes(elevation, 2), [[np.degrees(np.arctan(np.sqrt(5) / 2))]]
        )

    def test_central_differences_underestimate_a_vertex_peak(self):
        elevation = np.zeros((3, 3))
        elevation[1, 1] = 1
        dy, dx = np.gradient(elevation)
        central = np.degrees(np.arctan(np.hypot(dx, dy))).max()
        triangles = triangle_slopes(elevation, 1)
        np.testing.assert_allclose(triangles, np.degrees(np.arctan(np.sqrt(2))))
        self.assertGreater(triangles.min(), central)


class LimitSlope(unittest.TestCase):
    def test_cliff_edges_are_cut_to_the_shaped_slope_inside_their_window(self):
        spacing = FIELD["sampleSpacingMetres"]
        row, column = np.mgrid[:41, :41] * spacing
        mesa = np.where((np.abs(row - 16) < 8) & (np.abs(column - 16) < 8), 4.0, 0.0)
        terrain = np.zeros(mesa.shape)
        limit_slope(terrain, np.s_[:, :], mesa.copy())
        self.assertTrue(np.all(terrain <= mesa))
        self.assertLessEqual(triangle_slopes(terrain, spacing).max(), SHAPED_SLOPE_DEGREES + 1e-9)
        self.assertEqual(terrain[20, 20], 4)
        self.assertTrue(np.all(terrain[mesa == 0] == 0))
        with self.assertRaisesRegex(AssertionError, "reached its window"):
            limit_slope(np.zeros((21, 21)), np.s_[:, :], mesa[10:31, 10:31].copy())


class Valley(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(LANDMARKS_PATH.read_text())
        cls.raw, cls.report, cls.masks = build(cls.document)
        cls.elevation = (cls.raw.astype(float) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
        cls.named = {item["id"]: item for item in cls.document["landmarks"]}

    def test_generated_output_matches_active_version_and_south_is_unchanged(self):
        self.assertEqual(hashlib.sha256(self.raw.tobytes()).hexdigest(), WORLD["heightfieldSha256"])
        self.assertEqual(self.raw.tobytes(), (ROOT / FIELD["path"]).read_bytes())
        baseline = baseline_samples()
        north, _ = axes()
        south = north <= north[0] + SOUTH_BUFFER_M
        np.testing.assert_array_equal(self.raw[:, south], baseline[:, south])
        self.assertGreater(self.report["changedSamples"], 0)
        slope = triangle_slopes(self.elevation, FIELD["sampleSpacingMetres"]).max()
        self.assertLessEqual(slope, MAX_SLOPE_DEGREES)
        self.assertEqual(slope, self.report["maxSlopeDegrees"])
        self.assertLess(self.report["reliefM"], 65)

    def test_catalogue_z_matches_final_heightfield(self):
        for item in self.document["landmarks"]:
            np.testing.assert_allclose(
                [p["z"] for p in item["geometry"]["points"]],
                lookup(self.elevation, points(item)) * 100,
                atol=0.001,
                err_msg=item["id"],
            )

    def test_hydrology_chain_descends_from_lake_through_pond_to_the_east_exit(self):
        creek = self.report["drainage"]["creek"]
        bed, water = np.array(creek["designBedM"]), np.array(creek["waterSurfaceM"])
        self.assertTrue(np.all(np.diff(bed) < 0))
        self.assertTrue(np.all(np.diff(water) <= 0))
        self.assertEqual(water[0], BASINS["granite-lake"]["surfaceM"])
        final = np.array(creek["sampledFinalBedM"])
        self.assertLessEqual(np.diff(final).max(), 1 / FIELD["samplesPerMetre"] + 1e-9)
        self.assertTrue(np.all(final <= water + 1e-9))
        self.assertGreater(creek["damStepM"], 0.2)
        vertices = points(self.named["meadow-creek"])
        lake = points(self.named["granite-lake"])
        self.assertTrue(np.isclose(lake, vertices[0], rtol=0, atol=1e-6).all(axis=1).any())

        def entered(identifier):
            return np.flatnonzero(inside(points(self.named[identifier]), *vertices.T))

        aspens, pond = entered("aspen-bend"), entered("beaver-pond")
        bridge = np.argmin(
            np.linalg.norm(vertices - points(self.named["footbridge"]).mean(0), axis=1)
        )
        self.assertTrue(aspens[-1] < bridge < pond[0] <= pond[-1] < creek["damNode"])
        self.assertEqual(vertices[-1][1], WORLD["searchBoundsCm"]["max"]["y"] / 100)

    def test_creek_banks_stay_above_the_water(self):
        self.assertGreater(self.report["drainage"]["creek"]["minimumBankFreeboardM"], 0)

    def test_basins_hold_flat_water_inside_their_footprints(self):
        creek = points(self.named["meadow-creek"])
        corridor = line_field(creek, np.zeros(len(creek)), 12)[0] < 12
        for identifier, spec in BASINS.items():
            with self.subTest(identifier):
                window, x, y = region(points(self.named[identifier]), 40)
                delta = signed_distance(inside(points(self.named[identifier]), x, y))
                local = self.elevation[window]
                self.assertTrue(np.all(local[delta <= -0.8] < spec["surfaceM"]))
                open_water = ~corridor[window]
                flooded, _ = label((local < spec["surfaceM"]) & open_water)
                deepest = np.unravel_index(
                    np.argmin(np.where((delta < 0) & open_water, local, np.inf)), local.shape
                )
                body = flooded == flooded[deepest]
                self.assertTrue(body.any())
                self.assertLessEqual(delta[body].max(), 1)

    def test_trails_are_walkable_and_the_fire_road_is_drivable(self):
        measured = self.report["routes"]["measured"]
        trails = {item["id"] for item in self.document["landmarks"] if item["kind"] == "trail"}
        self.assertTrue({"lake-trail", "lookout-trail"} <= trails <= set(measured))
        for identifier in trails:
            self.assertLessEqual(measured[identifier]["maxSlopeDegrees"], TRAIL_SLOPE_DEGREES)
        self.assertLessEqual(measured["fire-road"]["maxGrade"], ROAD_GRADE)

    def test_drone_pad_stays_clear(self):
        pad = self.report["pad"]
        self.assertLessEqual(pad["terrainAbovePadWithin20M"], 0.3)
        self.assertLessEqual(pad["terrainAbovePadWithin40M"], 4)
        self.assertLessEqual(pad["tallestLandmarkWithin20M"], 0.3)
        self.assertLessEqual(pad["tallestLandmarkWithin40M"], 4)

    def test_dome_summit_is_at_most_60_m_above_the_meadow_on_a_steep_shoulder(self):
        vertices = points(self.named["granite-dome"])
        window, x, y = region(vertices, 1)
        footprint = inside(vertices, x, y)
        dome = self.elevation[window]
        north, east = axes()
        (n0, n1), (e0, e1) = MEADOW_M
        meadow = np.median(
            self.elevation[np.ix_((east >= e0) & (east <= e1), (north >= n0) & (north <= n1))]
        )
        self.assertLessEqual(dome[footprint].max() - meadow, 60)
        self.assertGreater(dome[footprint].max() - meadow, 45)
        summit = np.unravel_index(np.argmax(np.where(footprint, dome, -np.inf)), dome.shape)
        self.assertLess(np.hypot(x[summit] - 600, y[summit] - 300), 25)
        slope = triangle_slopes(dome, FIELD["sampleSpacingMetres"])
        self.assertGreater(slope[footprint[:-1, :-1]].max(), SHAPED_SLOPE_DEGREES - 1)

    def test_layer_masks_partition_every_sample(self):
        self.assertEqual(self.masks.shape, (len(MASKS) + 1, ROWS, COLUMNS))
        self.assertEqual(self.masks.dtype, np.uint8)
        np.testing.assert_array_equal(self.masks.sum(axis=0, dtype=np.int64), 255)
        layers = dict(zip([*MASKS, "dry-meadow"], self.masks.astype(float) / 255))
        self.assertTrue(all(layer.max() == 1 for layer in layers.values()))
        vertices = points(self.named["granite-dome"])
        window, x, y = region(vertices, 1)
        self.assertGreater(layers["granite"][window][inside(vertices, x, y)].mean(), 0.9)
        cirque = np.vstack([points(self.named[i]) for i in ("lookout-tarn", "lookout-talus")])
        cirque = cirque[ConvexHull(cirque).vertices]
        window, x, y = region(cirque, 1)
        self.assertGreater(layers["granite"][window][inside(cirque, x, y)].mean(), 0.95)
        self.assertEqual(max(layers, key=lambda name: layers[name].mean()), "dry-meadow")

    def test_heroes_stay_clear_of_mission_map_panels(self):
        heroes = (
            "granite-dome",
            "granite-lake",
            "lookout-tarn",
            "lookout-talus",
            "aspen-bend",
            "footbridge",
            "beaver-pond",
            "fire-lookout",
            "snow-survey-hut",
        )
        for identifier in heroes:
            vertices = points(self.named[identifier])
            north, east = np.meshgrid(
                *(np.arange(vertices[:, a].min(), vertices[:, a].max() + 1) for a in (0, 1))
            )
            covered = inside(vertices, north, east)
            north, east = north[covered], east[covered]
            planner = (north > 370) & (east < -724)
            preview = (north > 370) & (north < 628) & (east > 797)
            self.assertFalse((planner | preview).any(), identifier)

    def test_authored_height_caps_and_required_geometry(self):
        for kind, height in (("structure", 15.1), ("forest", 18.1), ("obstacle", 18.1)):
            document = copy.deepcopy(self.document)
            item = next(item for item in document["landmarks"] if item["kind"] == kind)
            item["heightM"] = height
            with self.assertRaisesRegex(AssertionError, "height envelope"):
                catalogue(document)
        document = copy.deepcopy(self.document)
        pond = next(item for item in document["landmarks"] if item["kind"] == "pond")
        pond["geometry"] = {"type": "point", "points": pond["geometry"]["points"][:1]}
        with self.assertRaises(AssertionError):
            catalogue(document)

    def test_names_and_aliases_are_unique(self):
        document = copy.deepcopy(self.document)
        document["landmarks"][1]["aliases"].append("The Lake")
        with self.assertRaisesRegex(AssertionError, "unique"):
            catalogue(document)

    def test_same_kind_polygons_cannot_touch_or_contain_one_another(self):
        for offset in (0, 4):
            document = copy.deepcopy(self.document)
            structures = [item for item in document["landmarks"] if item["kind"] == "structure"]
            source = structures[0]
            structures[1]["geometry"] = copy.deepcopy(source["geometry"])
            for point in structures[1]["geometry"]["points"]:
                point["y"] += offset * 100
            with self.assertRaisesRegex(AssertionError, "Same-kind"):
                catalogue(document)

    def test_natural_reach_must_follow_computed_drainage(self):
        document = copy.deepcopy(self.document)
        creek = next(item for item in document["landmarks"] if item["kind"] == "creek")
        creek["geometry"]["points"][-20]["y"] += 3000
        with self.assertRaisesRegex(AssertionError, "Natural reach leaves computed drainage"):
            build(document)

    def test_creek_must_leave_the_lake_at_its_spillway(self):
        document = copy.deepcopy(self.document)
        creek = next(item for item in document["landmarks"] if item["kind"] == "creek")
        creek["geometry"]["points"][0]["y"] += 300
        with self.assertRaisesRegex(AssertionError, "lake spillway"):
            build(document)

    def test_route_crossings_require_a_bridge_at_the_crossing(self):
        document = copy.deepcopy(self.document)
        bridge = next(item for item in document["landmarks"] if item["kind"] == "bridge")
        for point in bridge["geometry"]["points"]:
            point["y"] -= 5000
        with self.assertRaisesRegex(AssertionError, "Unbridged creek crossing"):
            catalogue(document)


if __name__ == "__main__":
    unittest.main()

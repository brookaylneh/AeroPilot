"""Sector partition, lanes and layers compiled against the real pads, fence and terrain."""

import unittest

from shapely import Point, Polygon, box, union_all

from mission_planner.gcs.allocation import (
    APPROVAL_HOVER_S,
    LANE_ORDER,
    MAX_VERTICES,
    allocate,
    branches,
    check_ceiling,
    drone_budgets,
    fit,
    horizontal_seconds,
    partition,
    rotate,
    simplified,
    survey_remainder,
    survey_sectors,
    survey_step,
    sweep_bearings,
)
from mission_planner.gcs.fences import default_fences
from mission_planner.gcs.geodesy import NAMES
from mission_planner.gcs.planner import compile_plan, survey_points
from mission_planner.gcs.region import bands, cells, ipp_geometry, searchable_area
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.test_planner import states
from mission_planner.gcs.test_validation import readback
from world.cosys.landmarks import load
from world.cosys.terrain import terrain_height

LANDMARKS = load()
BY_ID = {landmark["id"]: landmark for landmark in LANDMARKS}
SETTINGS = {
    "height_m": 30,
    "speed_mps": 5,
    "transit_speed_mps": 10,
    "spacing_m": 20,
    "zoom": 1,
    "look_ahead_m": 20,
    "stand_off_m": 20,
    "max_lock_seconds": 180,
}
SEARCH = {
    "detector_prompt": "person",
    "threshold": 0.35,
    "look_for": {"objective": "Find the child", "target": "child", "attributes": [], "ignore": []},
    "stand_off_m": 20,
    "zoom": 1,
    "look_ahead_m": 20,
    "speed_mps": 5,
    "max_lock_seconds": 180,
}


def origins(fleet):
    return {
        s["id"]: {"north_m": s["home"]["north_m"], "east_m": s["home"]["east_m"]} for s in fleet
    }


def first_band(landmark_id, direction_deg, drones=LANE_ORDER, hover_s=0):
    geometry = ipp_geometry(BY_ID[landmark_id])
    result = cells(geometry, direction_deg, "child", None, searchable_area(default_fences()))
    centroid = geometry.centroid
    budget = min(
        drone_budgets(
            states(),
            list(drones),
            SETTINGS,
            {"north_m": centroid.x, "east_m": centroid.y},
            SETTINGS["max_lock_seconds"],
            hover_s,
        ).values()
    )
    # Open-loop sizing; the tasking layer closes the loop against compiled durations, so the
    # allocation tests keep a conservative share of the endurance budget.
    rate = len(drones) * SETTINGS["spacing_m"] * SETTINGS["speed_mps"]
    return bands(result, 0.6 * budget, rate)[0]


def compiled(band, drones, fleet, *, next_band=False, aftermath="return_home", direction=None):
    polygon = simplified(band["polygon"])
    bearing, sectors = allocate(polygon, drones, origins(fleet), SETTINGS, direction)
    plan_drones, metadata = branches(
        survey_sectors(sectors, SETTINGS, bearing),
        fleet,
        SETTINGS,
        LANDMARKS,
        next_band=next_band,
        aftermath=aftermath,
    )
    plan = {
        "version": 1,
        "search": {**SEARCH, "perimeter": polygon_points(polygon)},
        "drones": plan_drones,
    }
    receipt = compile_plan(plan, fleet, {name: readback() for name in NAMES}, LANDMARKS)
    return plan, receipt, metadata


def row_seconds(polygon, bearing):
    """Strips equalise flight time up to one sweep row: the longest row's duration."""
    along = [rotate(n, e, bearing)[0] for n, e in polygon.exterior.coords]
    return horizontal_seconds(
        {"north_m": 0, "east_m": 0},
        {"north_m": max(along) - min(along), "east_m": 0},
        SETTINGS["speed_mps"],
    )


def polygon_points(polygon):
    return [{"north_m": float(n), "east_m": float(e)} for n, e in polygon.exterior.coords[:-1]]


class Allocation(unittest.TestCase):
    def test_equal_time_strips_tile_the_band_and_match_within_tolerance(self):
        band = box(-500, -100, -100, 200)
        fleet = states()
        self.assertEqual([round(b) for b in sweep_bearings(band, 0.0)], [0, 90])
        bearing, sectors = partition(band, list(NAMES), origins(fleet), SETTINGS, 270.0)
        self.assertIn(bearing, (90.0, 270.0))
        self.assertEqual([s["drone_id"] for s in sectors], list(NAMES))
        parts = [part for s in sectors for part in s["polygons"]]
        self.assertAlmostEqual(union_all(parts).area, band.area, delta=1)
        times = [s["total_s"] for s in sectors]
        self.assertLess(max(times) - min(times), row_seconds(band, bearing) + 0.1 * max(times))
        for sector in sectors:
            self.assertEqual([part.geom_type for part in sector["polygons"]], ["Polygon"])
        _, one = partition(band, ["Drone2"], origins(fleet), SETTINGS, bearing)
        self.assertAlmostEqual(one[0]["polygons"][0].area, band.area, delta=1)

    def test_bands_from_real_landmarks_compile_for_four_drones_from_the_pads(self):
        cases = [
            ("fire-road", 45.0),
            ("campsite", 135.0),
            ("footbridge", 0.0),
            ("trail-fork-sign", 90.0),
            ("hut-junction-sign", 270.0),
        ]
        for landmark_id, direction in cases:
            with self.subTest(landmark=landmark_id):
                band = first_band(landmark_id, direction)
                plan, receipt, metadata = compiled(
                    band, list(LANE_ORDER), states(), direction=direction
                )
                # Slivers below one row pitch squared are dropped from strips.
                self.assertGreaterEqual(receipt["coverage"]["survey_percent"], 99.5)
                totals = [m["estimate_s"] for m in metadata]
                polygon = simplified(band["polygon"])
                self.assertLess(
                    max(totals) - min(totals),
                    row_seconds(polygon, metadata[0]["bearing_deg"]) + 0.1 * max(totals),
                )
                layers = sorted(m["layer_agl_m"] for m in metadata)
                self.assertTrue(all(b - a >= 8 for a, b in zip(layers, layers[1:])))
                for name, mission in receipt["missions"].items():
                    self.assertIsNotNone(mission["duration_s"])
                    self.assertLessEqual(mission["battery_required_percent"], 100)
                report = fit(receipt, states(), 0)
                self.assertTrue(all(row["headroom_s"] > 0 for row in report.values()))
                for branch in plan["drones"]:
                    kinds = [s["kind"] for s in branch["steps"]]
                    self.assertEqual(kinds[0], "takeoff")
                    self.assertIn("survey", kinds)
                    self.assertEqual(kinds[-1], "return_home")

    def test_operator_polygon_and_point_bands_compile_with_hover_for_approval(self):
        fleet = states()
        band = box(-650, -150, -450, 150)
        plan, receipt, metadata = compiled(
            {"polygon": band}, ["Drone1", "Drone3"], fleet, next_band=True, direction=None
        )
        for branch in plan["drones"]:
            kinds = [s["kind"] for s in branch["steps"]]
            self.assertEqual(kinds[kinds.index("survey") + 1], "hover")
            hover = branch["steps"][kinds.index("survey") + 1]
            self.assertEqual(hover["seconds"], APPROVAL_HOVER_S)
        for mission in receipt["missions"].values():
            self.assertGreater(mission["duration_s"], APPROVAL_HOVER_S)
        point_band = first_band("snow-survey-hut", 135.0, drones=["Drone1"])
        plan, receipt, _ = compiled(
            point_band, ["Drone1"], fleet, aftermath="hover", direction=135.0
        )
        self.assertEqual(plan["drones"][0]["steps"][-1], {"kind": "hover", "seconds": None})
        self.assertIsNone(receipt["missions"]["Drone1"]["duration_s"])

    def test_airborne_continuation_flies_straight_to_its_strip(self):
        fleet = states()
        fleet[0].update(
            north_m=-400,
            east_m=0,
            altitude_above_home_m=terrain_height(-400, 0)
            + 38
            - (fleet[0]["home"]["altitude_msl_m"] - 2621.28),
            armed=True,
            landed_state=2,
        )
        band = box(-300, -100, -100, 100)
        starts = origins(fleet)
        starts["Drone1"] = {"north_m": -400, "east_m": 0}
        bearing, sectors = allocate(band, ["Drone1"], starts, SETTINGS, 0.0)
        plan_drones, metadata = branches(
            survey_sectors(sectors, SETTINGS, bearing),
            fleet,
            SETTINGS,
            LANDMARKS,
            next_band=False,
            aftermath="return_home",
        )
        kinds = [s["kind"] for s in plan_drones[0]["steps"]]
        self.assertEqual(kinds[:2], ["fly", "survey"])
        self.assertEqual(plan_drones[0]["steps"][0]["north_m"], metadata[0]["entry"]["north_m"])

    def test_disconnected_strip_and_band_behind_pads_are_structured_rejections(self):
        fleet = states()
        u_shape = Polygon(
            [
                (-600, -200),
                (-600, 200),
                (-300, 200),
                (-300, 150),
                (-550, 150),
                (-550, -150),
                (-300, -150),
                (-300, -200),
            ]
        )
        bearing, sectors = partition(u_shape, ["Drone1", "Drone3"], origins(fleet), SETTINGS, 90.0)
        self.assertEqual(sorted(len(s["polygons"]) for s in sectors), [1, 2])
        plan_drones, _ = branches(
            survey_sectors(sectors, SETTINGS, bearing),
            fleet,
            SETTINGS,
            LANDMARKS,
            next_band=False,
            aftermath="return_home",
        )
        split = next(b for b in plan_drones if [s["kind"] for s in b["steps"]].count("survey") == 2)
        self.assertEqual(
            [s["kind"] for s in split["steps"]][:6],
            ["takeoff", "fly", "fly", "survey", "fly", "survey"],
        )
        join = split["steps"][4]
        self.assertEqual(join["height_m"], split["steps"][1]["height_m"])
        with self.assertRaises(PlanRejected) as caught:
            partition(box(-600, -40, -200, -10), list(LANE_ORDER), origins(fleet), SETTINGS, 0.0)
        self.assertEqual(caught.exception.rejection["rule"], "disconnected_sector")
        self.assertIn("sweep rows for 4 aircraft", str(caught.exception))
        behind = box(-750, -45, -730, -15)
        bearing, sectors = partition(behind, ["Drone1"], origins(fleet), SETTINGS, 90.0)
        with self.assertRaises(PlanRejected) as caught:
            branches(
                survey_sectors(sectors, SETTINGS, bearing),
                fleet,
                SETTINGS,
                LANDMARKS,
                next_band=False,
                aftermath="land",
            )
        self.assertEqual(caught.exception.rejection["rule"], "band_behind_pads")

    def test_transit_ceiling_names_the_lane_point(self):
        peak = max(
            (
                (terrain_height(n, e), n, e)
                for n in range(-780, 790, 20)
                for e in range(-1180, 1190, 20)
            ),
            key=lambda item: item[0],
        )
        home_z = states()[0]["home"]["altitude_msl_m"] - 2621.28
        legs = [
            ({"north_m": peak[1], "east_m": peak[2]}, {"north_m": peak[1] + 1, "east_m": peak[2]})
        ]
        with self.assertRaises(PlanRejected) as caught:
            check_ceiling("Drone4", legs, 100 - (peak[0] - home_z) + 1, home_z)
        self.assertEqual(caught.exception.rejection["rule"], "transit_ceiling")
        check_ceiling("Drone4", legs, 100 - (peak[0] - home_z) - 1, home_z)

    def test_survey_remainder_is_the_unswept_rows(self):
        step = survey_step(box(0, 0, 120, 100), SETTINGS, 90.0)
        points = survey_points(step)
        self.assertEqual(len(points), 12)
        remainder = survey_remainder(step, 4)
        self.assertAlmostEqual(remainder.area, 120 * 100 * 4 / 6, delta=1)
        self.assertAlmostEqual(survey_remainder(step, 0).area, 12000, delta=1)
        self.assertAlmostEqual(survey_remainder(step, 11).area, 120 * 100 / 6, delta=1)
        with self.assertRaisesRegex(AssertionError, "pattern point"):
            survey_remainder(step, 12)

    def test_simplified_band_keeps_the_vertex_limit(self):
        shape = Point(0, 0).buffer(300, quad_segs=256)
        self.assertGreater(len(shape.exterior.coords), MAX_VERTICES)
        reduced = simplified(shape)
        self.assertLessEqual(len(reduced.exterior.coords) - 1, MAX_VERTICES)
        self.assertAlmostEqual(reduced.area, shape.area, delta=shape.area * 0.01)


if __name__ == "__main__":
    unittest.main()

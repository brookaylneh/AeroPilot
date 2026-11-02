"""Plan conversion and safety against the authored valley and measured apply position."""

import copy
import math
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, ValidationError
from pymavlink.dialects.v20 import ardupilotmega as MAV

from mission_planner.gcs.battery import VOLTAGE_FLOOR_PERCENT, available_seconds, required_percent
from mission_planner.gcs.geodesy import (
    NAMES,
    ORIGIN,
    homes,
    to_geodetic,
    to_local,
)
from mission_planner.gcs.planner import (
    PLAN_SCHEMA,
    compile_plan,
    mission_item,
    mission_readback,
    survey_coverage,
    survey_points,
    validate_missions,
    validate_search,
)
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.terrain import ALTITUDE_STEP_M, PLANNED_AGL_TOLERANCE_M
from mission_planner.gcs.test_validation import readback
from mission_planner.gcs.validation import minimum_clearance
from world.cosys.landmarks import load
from world.cosys.terrain import terrain_height

HOME_UP_M = homes()["Drone1"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"]


def states():
    return [
        {
            "id": name,
            **home,
            "altitude_above_home_m": 0,
            "home": home,
            "battery_percent": 100,
            "armed": False,
            "landed_state": MAV.MAV_LANDED_STATE_ON_GROUND,
            "link_lost": False,
            "ekf_healthy": True,
            "gps_fix": 3,
            "mission_current": 0,
            "ground_speed_mps": 0,
        }
        for name, home in homes().items()
    ]


def airborne(north=-780, east=-2.5, height=40):
    fleet = states()
    fleet[0].update(
        north_m=north,
        east_m=east,
        altitude_above_home_m=terrain_height(north, east) + height - HOME_UP_M,
        armed=True,
        landed_state=MAV.MAV_LANDED_STATE_IN_AIR,
    )
    return fleet


def plan(steps, drone="Drone1", version=1):
    return {"version": version, "drones": [{"drone_id": drone, "steps": steps}]}


def hover():
    return {"kind": "hover", "seconds": None}


def encoded_coordinates(north, east):
    return to_local(*(round(value * 1e7) / 1e7 for value in to_geodetic(north, east)))


def fly(north, east, height=40, speed=5):
    return {"kind": "fly", "north_m": north, "east_m": east, "height_m": height, "speed_mps": speed}


def compile_steps(steps, fleet=None):
    return compile_plan(
        plan(steps),
        states() if fleet is None else fleet,
        {name: readback() for name in NAMES},
        load(),
    )["missions"]["Drone1"]


def rectangle(south, north, west, east):
    return [
        {"north_m": n, "east_m": e}
        for n, e in ((south, west), (north, west), (north, east), (south, east))
    ]


def search_plan():
    return {
        "version": 1,
        "search": {
            "perimeter": rectangle(-750, -700, -60, 60),
            "detector_prompt": "person",
            "threshold": 0.3,
            "look_for": {
                "objective": "Find the missing hiker",
                "target": "person",
                "attributes": [{"id": "jacket", "text": "red jacket"}],
                "ignore": [],
            },
            "stand_off_m": 15,
            "zoom": 1,
            "look_ahead_m": 20,
            "speed_mps": 3,
            "max_lock_seconds": 90,
        },
        "drones": [
            {
                "drone_id": name,
                "steps": [
                    {
                        "kind": "survey",
                        "observe": True,
                        "polygon": rectangle(-750, -700, -60 + i * 30, -30 + i * 30),
                        "spacing_m": 10,
                        "bearing_deg": 0,
                        "height_m": 40,
                        "speed_mps": 5,
                    },
                    hover(),
                ],
            }
            for i, name in enumerate(NAMES)
        ],
    }


class SearchPlan(unittest.TestCase):
    def test_one_drone_rotated_polygon_and_repeated_passes_are_choices(self):
        value = search_plan()
        value["drones"] = value["drones"][:1]
        step = value["drones"][0]["steps"][0]
        step["polygon"][1]["east_m"] += 5
        step["bearing_deg"] = 27
        value["drones"][0]["steps"].insert(1, copy.deepcopy(step))
        self.check(value)

    def test_only_selected_observation_steps_are_bound_to_the_perimeter(self):
        value = search_plan()
        value["drones"] = value["drones"][:1]
        step = value["drones"][0]["steps"][0]
        for point in step["polygon"]:
            point["east_m"] -= 100
        with self.assertRaisesRegex(AssertionError, "outside the perimeter"):
            self.check(value)
        del step["observe"]
        self.check(value)

    def test_observation_needs_a_policy_and_supported_movement_step(self):
        value = search_plan()
        del value["search"]
        with self.assertRaisesRegex(AssertionError, "Observed steps require plan.search"):
            self.check(value)
        value = plan([{**hover(), "observe": True}])
        with self.assertRaises(ValidationError):
            self.check(value)
        value = search_plan()
        value["drones"][0]["steps"][0]["observe"] = False
        with self.assertRaises(ValidationError):
            self.check(value)

    def test_return_only_version_keeps_policy_without_requiring_a_search(self):
        value = search_plan()
        for branch in value["drones"]:
            branch["steps"] = [{"kind": "return_home", "height_m": 40, "speed_mps": 5}]
        self.check(value)

    def test_bear_circle_and_route_use_the_same_compiler_and_policy(self):
        for step in (
            {
                "kind": "circle",
                "north_m": -720,
                "east_m": 15,
                "radius_m": 10,
                "height_m": 30,
                "speed_mps": 5,
                "turns": 1,
                "clockwise": True,
            },
            {
                "kind": "follow",
                "points": [{"north_m": -740, "east_m": 15}, {"north_m": -710, "east_m": 15}],
                "height_m": 30,
                "speed_mps": 5,
            },
            fly(-710, 15, 30, 5),
        ):
            with self.subTest(kind=step["kind"]):
                value = search_plan()
                value["search"]["detector_prompt"] = "bear"
                value["search"]["look_for"] = {
                    "objective": "Check a bear sighting",
                    "target": "bear",
                    "attributes": [],
                    "ignore": [],
                }
                value["drones"] = [
                    {"drone_id": "Drone1", "steps": [{**step, "observe": True}, hover()]}
                ]
                receipt = compile_plan(
                    value, airborne(-745, 15, 30), {name: readback() for name in NAMES}, load()
                )
                self.assertEqual(receipt["plan"], value)
                self.assertGreater(receipt["missions"]["Drone1"]["distance_m"], 0)

    def test_selected_pattern_cannot_exit_its_area_between_valid_endpoints(self):
        value = search_plan()
        value["search"]["perimeter"] = [
            {"north_m": n, "east_m": e}
            for n, e in (
                (-750, 0),
                (-750, 50),
                (-700, 50),
                (-700, 35),
                (-735, 35),
                (-735, 15),
                (-700, 15),
                (-700, 0),
            )
        ]
        value["drones"] = [
            {
                "drone_id": "Drone1",
                "steps": [
                    {
                        "kind": "follow",
                        "observe": True,
                        "points": [{"north_m": -710, "east_m": 5}, {"north_m": -710, "east_m": 45}],
                        "height_m": 30,
                        "speed_mps": 5,
                    },
                    hover(),
                ],
            }
        ]
        # The connector between the two points cuts outside the concave perimeter; sampling is
        # gated by aircraft position, so only the pattern points themselves must stay inside.
        compile_plan(value, airborne(-740, 5, 30), {name: readback() for name in NAMES}, load())
        value["drones"][0]["steps"][0]["points"][1] = {"north_m": -710, "east_m": 120}
        with self.assertRaises(PlanRejected) as caught:
            compile_plan(value, airborne(-740, 5, 30), {name: readback() for name in NAMES}, load())
        self.assertEqual(caught.exception.rejection["rule"], "observation_area")
        self.assertIn("leaves its observation area", str(caught.exception))

    def check(self, value):
        Draft202012Validator(PLAN_SCHEMA).validate(value)
        validate_search(value, {name: readback() for name in NAMES})

    def test_nominal_overlap_without_rewriting_the_plan(self):
        value = search_plan()
        for branch in value["drones"]:
            branch["steps"][0].update(height_m=30, speed_mps=5, spacing_m=20)
        original = copy.deepcopy(value)
        self.check(value)
        self.assertEqual(value, original)
        for branch in value["drones"]:
            branch["steps"][0]["spacing_m"] = 40.01
        original = copy.deepcopy(value)
        with self.assertRaisesRegex(AssertionError, "spacing 40.01 m exceeds 40 m.*1x zoom"):
            self.check(value)
        self.assertEqual(value, original)
        for branch in value["drones"]:
            branch["steps"][0]["spacing_m"] = 40
        self.check(value)

    def test_search_policy_accepts_only_supported_zoom_levels(self):
        for zoom in (1, 2, 4):
            value = search_plan()
            value["search"]["zoom"] = zoom
            self.assertTrue(Draft202012Validator(PLAN_SCHEMA).is_valid(value))
        for zoom in (0, 1.5, 3, 5, True, "4"):
            value = search_plan()
            value["search"]["zoom"] = zoom
            self.assertFalse(Draft202012Validator(PLAN_SCHEMA).is_valid(value))

    def test_2x_rejects_spacing_that_only_covers_at_1x(self):
        value = search_plan()
        value["search"]["zoom"] = 2
        for branch in value["drones"]:
            branch["steps"][0].update(height_m=30, spacing_m=20)
        self.check(value)
        value["drones"][0]["steps"][0]["spacing_m"] = 20.01
        original = copy.deepcopy(value)
        with self.assertRaisesRegex(
            PlanRejected, "spacing 20.01 m exceeds 20 m.*2x zoom"
        ) as caught:
            self.check(value)
        self.assertEqual(caught.exception.rejection["detail"]["zoom"], 2)
        self.assertEqual(value, original)

    def test_4x_rejects_spacing_that_only_covers_at_2x(self):
        value = search_plan()
        value["search"]["zoom"] = 4
        for branch in value["drones"]:
            branch["steps"][0].update(height_m=30, spacing_m=10)
        self.check(value)
        value["drones"][0]["steps"][0]["spacing_m"] = 20
        original = copy.deepcopy(value)
        with self.assertRaisesRegex(PlanRejected, "spacing 20 m exceeds 10 m.*4x zoom") as caught:
            self.check(value)
        self.assertEqual(caught.exception.rejection["detail"]["zoom"], 4)
        self.assertEqual(value, original)

    def test_each_remaining_survey_uses_its_own_height_for_camera_overlap(self):
        value = search_plan()
        value["drones"] = value["drones"][-1:]
        survey = value["drones"][0]["steps"][0]
        survey.update(height_m=15, spacing_m=20)
        self.check(value)
        survey["spacing_m"] = 20.01
        with self.assertRaisesRegex(PlanRejected, "Drone4 step 1 .*exceeds 20 m.*15 m AGL") as c:
            self.check(value)
        rejection = c.exception.rejection
        self.assertEqual(
            {k: rejection[k] for k in ("rule", "drone_id", "step_index", "step_kind")},
            {
                "rule": "spacing_overlap",
                "drone_id": "Drone4",
                "step_index": 0,
                "step_kind": "survey",
            },
        )
        self.assertEqual(rejection["detail"]["maximum_m"], 20)

    def test_unobserved_transit_legs_may_cross_when_their_windows_are_apart(self):
        fences = {name: readback() for name in NAMES}
        shared = (-650, 60)
        drone2 = [
            {"kind": "takeoff", "height_m": 30},
            fly(*shared, 30),
            fly(-600, 120, 30),
            fly(-550, 200, 30),
            hover(),
        ]

        def compile_pair(first):
            drone1 = [
                {"kind": "takeoff", "height_m": 30},
                *first,
                fly(*shared, 30),
                fly(-600, 120, 30),
                fly(-700, -60, 30),
                hover(),
            ]
            value = {
                "version": 1,
                "drones": [
                    {"drone_id": "Drone1", "steps": drone1},
                    {"drone_id": "Drone2", "steps": drone2},
                ],
            }
            return compile_plan(value, states(), fences, load())

        accepted = compile_pair([fly(-300, -40, 30)])
        windows = accepted["missions"]["Drone1"]["windows"]
        self.assertEqual(len(windows), len(accepted["missions"]["Drone1"]["route"]) - 1)
        self.assertIsNone(windows[0])
        timed = [w for w in windows if w is not None]
        self.assertTrue(timed)
        self.assertTrue(all(0 <= a < b for a, b in timed))
        self.assertTrue(all(b <= c for (_, b), (c, _) in zip(timed, timed[1:])))
        with self.assertRaises(PlanRejected) as caught:
            compile_pair([])
        self.assertEqual(caught.exception.rejection["rule"], "separation")
        self.assertTrue(caught.exception.rejection["conflict"]["timing"]["applied"])
        self.assertIn("time windows overlap", str(caught.exception))

    def test_return_only_branch_has_no_candidate_lock_allowance(self):
        value = search_plan()
        value["drones"] = [value["drones"][0], value["drones"][-1]]
        value["drones"][0]["steps"] = [{"kind": "return_home", "height_m": 60, "speed_mps": 5}]
        fleet = states()
        for index, north, east, height in ((0, -780, -2.5, 60), (3, -750, 35, 40)):
            fleet[index].update(
                north_m=north,
                east_m=east,
                altitude_above_home_m=terrain_height(north, east) + height - HOME_UP_M,
                armed=True,
                landed_state=MAV.MAV_LANDED_STATE_IN_AIR,
            )
        fences = {name: readback() for name in NAMES}
        receipt = compile_plan(value, fleet, fences, load())
        plain = copy.deepcopy(value)
        del plain["search"]
        for branch in plain["drones"]:
            for step in branch["steps"]:
                if "observe" in step:
                    del step["observe"]
        baseline = compile_plan(plain, fleet, fences, load())
        self.assertEqual(receipt["plan"], value)
        self.assertEqual(
            receipt["missions"]["Drone1"]["battery_required_percent"],
            baseline["missions"]["Drone1"]["battery_required_percent"],
        )
        self.assertAlmostEqual(
            baseline["missions"]["Drone4"]["available_hover_s"]
            - receipt["missions"]["Drone4"]["available_hover_s"],
            value["search"]["max_lock_seconds"],
        )

    def test_four_rectangles_cover_perimeter_without_another_plan_representation(self):
        value = search_plan()
        self.check(value)
        fleet = states()
        for i, state in enumerate(fleet):
            n, e = -750, -55 + i * 30
            state.update(
                north_m=n,
                east_m=e,
                altitude_above_home_m=terrain_height(n, e) + 40 - HOME_UP_M,
                armed=True,
                landed_state=MAV.MAV_LANDED_STATE_IN_AIR,
            )
        receipt = compile_plan(value, fleet, {name: readback() for name in NAMES}, load())
        self.assertEqual(receipt["plan"], value)
        plain = copy.deepcopy(value)
        del plain["search"]
        for branch in plain["drones"]:
            for step in branch["steps"]:
                if "observe" in step:
                    del step["observe"]
        baseline = compile_plan(plain, fleet, {name: readback() for name in NAMES}, load())
        for name in NAMES:
            mission, before = receipt["missions"][name], baseline["missions"][name]
            self.assertEqual(mission["route"], before["route"])
            self.assertEqual(mission["duration_s"], before["duration_s"])
            self.assertAlmostEqual(
                mission["battery_required_percent"] - before["battery_required_percent"], 2.5
            )
            self.assertAlmostEqual(before["available_hover_s"] - mission["available_hover_s"], 90)

    def test_perimeter_must_follow_its_corners_and_stay_inside_fence(self):
        value = search_plan()
        value["search"]["perimeter"][1:3] = reversed(value["search"]["perimeter"][1:3])
        with self.assertRaisesRegex(AssertionError, "self-intersects"):
            self.check(value)
        value = search_plan()
        for points in [
            value["search"]["perimeter"],
            *(branch["steps"][0]["polygon"] for branch in value["drones"]),
        ]:
            for point in points:
                point["north_m"] += 2000
        with self.assertRaisesRegex(AssertionError, "perimeter exits the fence"):
            self.check(value)

    def test_review_policy_requires_unique_attributes_nonblank_text_and_explicit_limits(self):
        value = search_plan()
        value["search"]["look_for"]["attributes"] *= 2
        with self.assertRaisesRegex(AssertionError, "ids must be unique"):
            self.check(value)
        for key in (
            "detector_prompt",
            "stand_off_m",
            "look_ahead_m",
            "speed_mps",
            "max_lock_seconds",
        ):
            value = search_plan()
            del value["search"][key]
            with self.assertRaises(ValidationError):
                self.check(value)
        value = search_plan()
        value["search"]["detector_prompt"] = " "
        with self.assertRaisesRegex(AssertionError, "prompt is empty"):
            self.check(value)


class SurveyCoverage(unittest.TestCase):
    def test_recorded_disconnected_sectors_report_the_unassigned_area(self):
        value = search_plan()
        value["search"]["perimeter"] = rectangle(30, 480, -130, 670)
        bounds = [
            (40, 130, 460, 660),
            (170, 260, 180, 380),
            (270, 355, 20, 200),
            (380, 470, -120, 60),
        ]
        for branch, area in zip(value["drones"], bounds):
            branch["steps"][0]["polygon"] = rectangle(*area)
        original = copy.deepcopy(value)
        self.assertEqual(
            survey_coverage(value),
            {
                "perimeter_area_m2": 360000,
                "survey_area_m2": 67500,
                "unassigned_area_m2": 292500,
                "survey_percent": 18.75,
            },
        )
        self.assertEqual(value, original)
        validate_search(value, {name: readback() for name in NAMES})

    def test_overlap_and_repeated_passes_do_not_double_count(self):
        value = search_plan()
        value["drones"] = value["drones"][:1]
        step = value["drones"][0]["steps"][0]
        step["polygon"] = rectangle(-750, -700, -60, 0)
        other = {**step, "polygon": rectangle(-750, -700, -30, 30)}
        value["drones"][0]["steps"] = [step, copy.deepcopy(step), other, hover()]
        self.assertEqual(survey_coverage(value)["survey_percent"], 75)
        self.assertEqual(survey_coverage(value)["unassigned_area_m2"], 1500)
        for branch in value["drones"]:
            branch["steps"] = [{"kind": "return_home", "height_m": 40, "speed_mps": 5}]
        self.assertIsNone(survey_coverage(value))
        self.assertIsNone(survey_coverage(plan([hover()])))

    def test_edge_passes_cover_short_and_non_divisible_sector_widths(self):
        for width in (1, 10, 20, 21, 40, 80, 85, 90, 100):
            step = {"polygon": rectangle(-750, -710, 0, width), "spacing_m": 20, "bearing_deg": 0}
            rows = [p["east_m"] for p in survey_points(step)[::2]]
            self.assertEqual(len(rows), math.ceil(width / 20))
            self.assertLessEqual(rows[0], 10)
            self.assertLessEqual(width - rows[-1], 10)
            self.assertAlmostEqual(rows[0], width - rows[-1])
            self.assertTrue(all(b - a <= 20 for a, b in zip(rows, rows[1:])))

    def test_continuation_keeps_unfinished_endpoints_without_replaying_old_rows(self):
        step = search_plan()["drones"][0]["steps"][0]
        points = survey_points(step)
        n = (points[2]["north_m"] + points[3]["north_m"]) / 2
        e = points[2]["east_m"]
        remaining = {**step, "start_at": 3, "speed_mps": 10}
        value = search_plan()
        value["drones"] = [{"drone_id": "Drone1", "steps": [remaining, hover()]}]
        original = copy.deepcopy(value)
        receipt = compile_plan(
            value, airborne(n, e, 40), {name: readback() for name in NAMES}, load()
        )
        route = receipt["missions"]["Drone1"]["route"]
        self.assertEqual((route[0]["north_m"], route[0]["east_m"]), (n, e))
        coordinates = [(p["north_m"], p["east_m"]) for p in route]
        for point in points[:3]:
            self.assertNotIn(encoded_coordinates(point["north_m"], point["east_m"]), coordinates)
        for point in points[3:]:
            self.assertIn(encoded_coordinates(point["north_m"], point["east_m"]), coordinates)
        self.assertEqual(value, original)
        remaining["start_at"] = len(points)
        with self.assertRaisesRegex(AssertionError, "start_at.*outside"):
            compile_plan(value, airborne(n, e, 40), {name: readback() for name in NAMES}, load())


class Planner(unittest.TestCase):
    def test_revalidation_uses_true_land_endpoint_after_home_changes(self):
        fleet = airborne(-700, 0)
        fences = {name: readback() for name in NAMES}
        value = plan([{"kind": "land"}])
        receipt = compile_plan(value, fleet, fences, [])
        original = copy.deepcopy(receipt)
        end = receipt["missions"]["Drone1"]["route"][-1]
        ground = terrain_height(end["north_m"], end["east_m"])
        fleet[0]["home"]["altitude_msl_m"] += 1
        fleet[0]["altitude_above_home_m"] -= 1
        fleet[1].update(
            north_m=end["north_m"],
            east_m=end["east_m"],
            altitude_above_home_m=ground
            - (fleet[1]["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"]),
        )
        with self.assertRaisesRegex(AssertionError, "conflicts with Drone2"):
            validate_missions(receipt, fleet, fences, [], ())
        self.assertEqual(receipt, original)

    def test_revalidation_checks_actual_takeoff_column_after_position_changes(self):
        fleet = states()
        fences = {name: readback() for name in NAMES}
        receipt = compile_plan(
            plan([{"kind": "takeoff", "height_m": 30}, hover()]), fleet, fences, []
        )
        original = copy.deepcopy(receipt)
        fleet[0]["north_m"] += 1
        north, east = fleet[0]["north_m"], fleet[0]["east_m"]
        obstacle = {
            "id": "moved-takeoff-column",
            "name": "Moved takeoff column",
            "heightM": 2,
            "geometry": {
                "type": "polygon",
                "points": [
                    {"x": 100 * (north + n), "y": 100 * (east + e)}
                    for n, e in ((-0.2, -0.2), (-0.2, 0.2), (0.2, 0.2), (0.2, -0.2))
                ],
            },
        }
        with self.assertRaisesRegex(AssertionError, "Ground column intersects raised landmark"):
            validate_missions(receipt, fleet, fences, [obstacle], ())
        self.assertEqual(receipt, original)

    def test_each_drone_can_launch_after_another_lands_with_a_different_home(self):
        fleet = states()
        fences = {name: readback() for name in NAMES}
        landmarks = load()
        for index, state in enumerate(fleet):
            with self.subTest(drone=state["id"]):
                value = plan(
                    [
                        {"kind": "takeoff", "height_m": 30},
                        fly(state["north_m"] + 15, state["east_m"], height=30),
                        {"kind": "return_home", "height_m": 30, "speed_mps": 5},
                    ],
                    state["id"],
                    version=index + 1,
                )
                compiled = compile_plan(value, fleet, fences, landmarks)
                mission = compiled["missions"][state["id"]]
                self.assertEqual(mission["items"][0]["z"], state["home"]["altitude_msl_m"])
                self.assertEqual(compiled["plan"], value)
                state["home"]["altitude_msl_m"] -= 0.01 * (index + 1)

    def test_other_home_drift_does_not_change_the_selected_drone_mission(self):
        fleet = states()
        value = plan(
            [{"kind": "takeoff", "height_m": 30}, fly(358, -124.1, height=30), hover()],
            "Drone2",
        )
        args = {"fences": {name: readback() for name in NAMES}, "landmarks": load()}
        original = compile_plan(value, fleet, **args)
        fleet[0]["home"]["altitude_msl_m"] -= 0.01
        self.assertEqual(compile_plan(value, fleet, **args), original)

    def test_takeoff_fly_hover_return_land_commands_and_ranges(self):
        steps = [
            {"kind": "takeoff", "height_m": 40},
            fly(-790, -2.5),
            {"kind": "hover", "seconds": 7},
            {"kind": "return_home", "height_m": 40, "speed_mps": 5},
        ]
        mission = compile_steps(steps)
        commands = [item["command"] for item in mission["items"]]
        self.assertEqual(commands[1], MAV.MAV_CMD_NAV_TAKEOFF)
        self.assertIn(MAV.MAV_CMD_DO_CHANGE_SPEED, commands)
        self.assertIn(MAV.MAV_CMD_NAV_LOITER_TIME, commands)
        self.assertEqual(commands[-1], MAV.MAV_CMD_NAV_LAND)
        self.assertEqual(mission["items"][0]["frame"], MAV.MAV_FRAME_GLOBAL)
        self.assertEqual(mission["items"][0]["z"], states()[0]["home"]["altitude_msl_m"])
        self.assertEqual(mission["items"][-1]["param4"], 1)
        timed = next(
            item for item in mission["items"] if item["command"] == MAV.MAV_CMD_NAV_LOITER_TIME
        )
        self.assertEqual(timed["param3"], 1)
        self.assertGreater(mission["duration_s"], 7)
        self.assertEqual(mission["step_ranges"][2]["duration_s"], 7)
        home = states()[0]
        self.assertEqual(
            mission["route"][-1]["north_m"], encoded_coordinates(home["north_m"], home["east_m"])[0]
        )
        self.assertLess(mission["route"][-1]["altitude_above_home_m"], 0)
        self.assertEqual([r["step_index"] for r in mission["step_ranges"]], list(range(4)))
        self.assertEqual(
            [r["first_seq"] for r in mission["step_ranges"]][1:],
            [r["last_seq"] + 1 for r in mission["step_ranges"]][:-1],
        )

    def test_indefinite_hover_has_unknown_duration_and_available_budget(self):
        mission = compile_steps([{"kind": "takeoff", "height_m": 30}, hover()])
        self.assertIsNone(mission["duration_s"])
        self.assertGreater(mission["available_hover_s"], 0)
        self.assertEqual(mission["items"][-1]["command"], MAV.MAV_CMD_NAV_LOITER_UNLIM)

    def test_return_home_explains_that_a_following_land_is_redundant(self):
        with self.assertRaisesRegex(
            PlanRejected,
            "Drone1 step 2 \\(return_home\\): return_home returns home and lands; "
            "it must be the last step",
        ) as caught:
            compile_steps(
                [
                    {"kind": "takeoff", "height_m": 40},
                    {"kind": "return_home", "height_m": 40, "speed_mps": 5},
                    {"kind": "land"},
                ]
            )
        rejection = caught.exception.rejection
        self.assertEqual(rejection["rule"], "aftermath")
        self.assertEqual((rejection["drone_id"], rejection["step_index"]), ("Drone1", 1))
        self.assertEqual(str(caught.exception), rejection["message"])

    def test_every_rejection_is_structured_with_zero_based_fields(self):
        fleet = airborne()
        fleet[0]["battery_percent"] = 1
        with self.assertRaises(PlanRejected) as battery:
            compile_steps([hover()], fleet)
        self.assertEqual(battery.exception.rejection["rule"], "battery")
        self.assertEqual(battery.exception.rejection["drone_id"], "Drone1")
        self.assertEqual(battery.exception.rejection["detail"]["battery_percent"], 1)
        self.assertGreater(battery.exception.rejection["detail"]["required_percent"], 1)
        with self.assertRaises(PlanRejected) as state:
            compile_steps([hover()], states())
        self.assertEqual(state.exception.rejection["rule"], "aircraft_state")
        self.assertEqual(state.exception.rejection["detail"]["armed"], False)
        with self.assertRaises(PlanRejected) as fence:
            compile_steps([{"kind": "takeoff", "height_m": 30}, fly(900, 0), hover()])
        rejection = fence.exception.rejection
        self.assertEqual(rejection["rule"], "fence")
        self.assertEqual((rejection["drone_id"], rejection["step_index"]), ("Drone1", 1))
        self.assertEqual(rejection["step_kind"], "fly")
        self.assertTrue(rejection["message"].startswith("Drone1 step 2 (fly): "))
        self.assertIsNone(rejection["conflict"])
        with self.assertRaises(PlanRejected) as hover_step:
            compile_steps([{"kind": "takeoff", "height_m": 30}, hover(), fly(-700, 0), hover()])
        self.assertEqual(hover_step.exception.rejection["rule"], "aftermath")
        self.assertEqual(hover_step.exception.rejection["step_index"], 2)

    def test_land_here_uses_local_ground_column(self):
        mission = compile_steps([{"kind": "land"}], airborne())
        self.assertEqual(mission["items"][-1]["command"], MAV.MAV_CMD_NAV_LAND)
        self.assertAlmostEqual(
            mission["route"][-1]["altitude_above_home_m"],
            terrain_height(*encoded_coordinates(-780, -2.5)) - HOME_UP_M,
        )

    def test_circle_direction_radius_and_turns_expand_at_requested_height(self):
        for clockwise in (False, True):
            step = {
                "kind": "circle",
                "north_m": -720,
                "east_m": 0,
                "radius_m": 20,
                "height_m": 40,
                "speed_mps": 5,
                "turns": 1,
                "clockwise": clockwise,
            }
            mission = compile_steps([step, hover()], airborne(-740, 0))
            points = mission["route"][1:]
            self.assertGreater(len(points), 10)
            perimeter = [
                p for p in points if abs(math.hypot(p["north_m"] + 720, p["east_m"]) - 20) < 0.01
            ]
            self.assertGreater(len(perimeter), 10)
            first_arc = next(point for point in perimeter if abs(point["east_m"]) > 0.01)
            self.assertEqual(first_arc["east_m"] < 0, clockwise)
            for p in points:
                agl = (
                    p["altitude_above_home_m"]
                    + HOME_UP_M
                    - terrain_height(p["north_m"], p["east_m"])
                )
                self.assertAlmostEqual(agl, 40, delta=1)

    def test_follow_keeps_operator_order(self):
        points = [{"north_m": -750, "east_m": -20}, {"north_m": -700, "east_m": -25}]
        mission = compile_steps(
            [{"kind": "follow", "points": points, "height_m": 40, "speed_mps": 5}, hover()],
            airborne(),
        )
        route = [(p["north_m"], p["east_m"]) for p in mission["route"]]
        self.assertLess(
            route.index(encoded_coordinates(-750, -20)), route.index(encoded_coordinates(-700, -25))
        )

    def test_survey_rotated_clipped_lawnmower(self):
        step = {
            "kind": "survey",
            "polygon": [
                {"north_m": n, "east_m": e}
                for n, e in [(-750, -20), (-710, -20), (-710, 20), (-750, 20)]
            ],
            "spacing_m": 10,
            "bearing_deg": 0,
            "height_m": 40,
            "speed_mps": 5,
        }
        points = survey_points(step)
        self.assertEqual(len(points), 8)
        self.assertEqual(
            [(p["north_m"], p["east_m"]) for p in points[:4]],
            [(-750, -15), (-710, -15), (-710, -5), (-750, -5)],
        )
        mission = compile_steps([step, hover()], airborne())
        self.assertGreater(mission["distance_m"], 160)
        step["bearing_deg"] = 90
        self.assertEqual(len(survey_points(step)), 8)
        step["polygon"] = [{"north_m": n, "east_m": e} for n, e in ((0, 0), (3, 3), (0, 4), (4, 0))]
        with self.assertRaisesRegex(AssertionError, "self-intersects"):
            survey_points(step)

    def test_30m_survey_preserves_requested_height_over_low_high_and_wooded_ground(self):
        for north, east in ((-730, -20), (590, 290), (20, -880)):
            with self.subTest(north=north, east=east):
                step = {
                    "kind": "survey",
                    "polygon": [
                        {"north_m": north + dn, "east_m": east + de}
                        for dn, de in ((0, 0), (40, 0), (40, 40), (0, 40))
                    ],
                    "spacing_m": 10,
                    "bearing_deg": 0,
                    "height_m": 30,
                    "speed_mps": 10,
                }
                requested = copy.deepcopy(step)
                fleet = airborne(north, east + 5, 30)
                mission = compile_steps([step, hover()], fleet)
                self.assertEqual(step, requested)
                for point in mission["route"]:
                    ground = terrain_height(point["north_m"], point["east_m"])
                    self.assertAlmostEqual(
                        point["altitude_above_home_m"] + HOME_UP_M - ground,
                        30,
                        delta=ALTITUDE_STEP_M / 2 + 0.01,
                    )
                for a, b in zip(mission["route"], mission["route"][1:]):
                    self.assertGreaterEqual(
                        minimum_clearance(a, b, 0, 1, HOME_UP_M, 0),
                        30 - PLANNED_AGL_TOLERANCE_M - 0.01,
                    )

        step["height_m"] = 27
        with self.assertRaisesRegex(AssertionError, "West Lodgepole Woodland"):
            compile_steps([step, hover()], fleet)
        self.assertEqual(step["height_m"], 27)
        step["height_m"] = 30
        step["polygon"][1]["north_m"] = 900
        step["polygon"][2]["north_m"] = 900
        with self.assertRaisesRegex(AssertionError, "outside the world fence"):
            compile_steps([step, hover()], fleet)
        self.assertEqual(step["polygon"][1]["north_m"], 900)

    def test_change_joins_actual_position_not_earlier_preview(self):
        steps = [fly(-650, 20), hover()]
        old = compile_steps(steps, airborne(-780, -2.5))
        fresh = airborne(-745, 5)
        applied = compile_steps(steps, fresh)
        self.assertEqual(applied["route"][0]["north_m"], -745)
        self.assertEqual(applied["route"][0]["east_m"], 5)
        self.assertNotEqual(old["distance_m"], applied["distance_m"])
        self.assertEqual(fresh[0]["north_m"], -745)

    def test_takeoff_only_on_ground_and_explicit_aftermath(self):
        for steps, fleet, message in [
            ([hover()], states(), "takeoff"),
            ([{"kind": "takeoff", "height_m": 30}, hover()], airborne(), "takeoff"),
            ([fly(-700, 0)], airborne(), "aftermath"),
        ]:
            with self.subTest(steps=steps), self.assertRaisesRegex(AssertionError, message):
                compile_steps(steps, fleet)

    def test_rejects_unsafe_fence_height_terrain_obstacle_and_battery(self):
        for step, message in [
            (fly(900, 0), "Outside|outside"),
            (fly(-700, 0, 5), "10–100|clearance"),
            (fly(610, 310, 100), ""),
            (fly(300, -690, 10), "clearance|10–100"),
        ]:
            with self.subTest(step=step), self.assertRaisesRegex(AssertionError, message):
                compile_steps([step, hover()], airborne())
        fleet = airborne()
        fleet[0]["battery_percent"] = 1
        with self.assertRaisesRegex(AssertionError, "battery needs"):
            compile_steps([hover()], fleet)

    def test_battery_budget_has_no_duplicate_return_reserve(self):
        fleet = airborne()
        fleet[0]["battery_percent"] = 80
        mission = compile_steps([hover()], fleet)
        self.assertEqual(mission["battery_required_percent"], VOLTAGE_FLOOR_PERCENT)
        self.assertAlmostEqual(mission["available_hover_s"], 1240.36)
        fleet[0]["battery_percent"] = 45
        with self.assertRaisesRegex(AssertionError, "10% charge floor, voltage requires 45.55%"):
            compile_steps([hover()], fleet)

    def test_return_and_landing_time_are_counted_once(self):
        mission = compile_steps(
            [{"kind": "return_home", "height_m": 40, "speed_mps": 5}], airborne()
        )
        self.assertGreater(mission["duration_s"], 0)
        self.assertAlmostEqual(
            mission["battery_required_percent"], required_percent(mission["duration_s"])
        )
        self.assertAlmostEqual(
            mission["available_hover_s"], available_seconds(100) - mission["duration_s"]
        )

    def test_whole_map_and_every_landmark_at_safe_height(self):
        targets = [(n, e) for n in (-600, 0, 600) for e in (-1000, 0, 1000)]
        targets.extend(
            (
                sum(p["x"] for p in landmark["geometry"]["points"])
                / len(landmark["geometry"]["points"])
                / 100,
                sum(p["y"] for p in landmark["geometry"]["points"])
                / len(landmark["geometry"]["points"])
                / 100,
            )
            for landmark in load()
        )
        for north, east in targets:
            with self.subTest(north=north, east=east):
                mission = compile_steps(
                    [fly(north, east, 45, 15), hover()], airborne(-790, -2.5, 45)
                )
                encoded = encoded_coordinates(north, east)
                self.assertEqual(mission["route"][-1]["north_m"], encoded[0])
                self.assertEqual(mission["route"][-1]["east_m"], encoded[1])

    def test_centimetre_storage_matches_native_float32_conversion(self):
        for altitude, expected in (
            (40.123, 40.119998931884766),
            (99.999, 99.989997863769531),
            (10.01, 10.010000228881836),
        ):
            with self.subTest(altitude=altitude):
                item = mission_item({"north_m": 0, "east_m": 0}, altitude, MAV.MAV_CMD_NAV_WAYPOINT)
                self.assertEqual(mission_readback(item)["z"], expected)
                self.assertEqual(mission_readback(item)["frame"], MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT)

    def test_encoded_anchor_preserves_actual_start_and_rejects_lost_clearance(self):
        fleet = airborne(0, 0)
        fleet[0]["altitude_above_home_m"] = 40.123
        mission = compile_steps([hover()], fleet)
        self.assertEqual(mission["route"][0]["altitude_above_home_m"], 40.123)
        self.assertEqual(mission["route"][1]["altitude_above_home_m"], 40.119998931884766)
        with patch(
            "mission_planner.gcs.validation.terrain_height", return_value=HOME_UP_M + 30.121
        ):
            with self.assertRaisesRegex(AssertionError, "clearance above terrain is 9.999 m"):
                compile_steps([hover()], fleet)

    def test_two_drones_and_conflicting_routes(self):
        fleet = states()
        branches = [
            {
                "drone_id": name,
                "steps": [{"kind": "takeoff", "height_m": 40}, fly(-750, home["east_m"]), hover()],
            }
            for name, home in list(homes().items())[:2]
        ]
        result = compile_plan(
            {"version": 1, "drones": branches}, fleet, {name: readback() for name in NAMES}, load()
        )
        self.assertEqual(set(result["missions"]), {"Drone1", "Drone2"})
        branches[1]["steps"][1]["east_m"] = branches[0]["steps"][1]["east_m"]
        with self.assertRaisesRegex(AssertionError, "conflicts"):
            compile_plan(
                {"version": 2, "drones": branches},
                fleet,
                {name: readback() for name in NAMES},
                load(),
            )

    def test_schema_and_inputs_preserved(self):
        fleet, requested = airborne(), plan([hover()])
        original = copy.deepcopy((fleet, requested))
        compile_plan(requested, fleet, {name: readback() for name in NAMES}, load())
        self.assertEqual((fleet, requested), original)
        requested["drones"][0]["steps"][0]["extra"] = 1
        with self.assertRaises(ValidationError):
            compile_plan(requested, fleet, {name: readback() for name in NAMES}, load())
        self.assertAlmostEqual(
            fleet[0]["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"], HOME_UP_M
        )


if __name__ == "__main__":
    unittest.main()

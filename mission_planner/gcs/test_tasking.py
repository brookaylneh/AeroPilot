"""Tasking schema, defaults, templates and rejections compiled against the fixture fleet."""

import copy
import json
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from shapely import Polygon

from mission_planner.gcs.allocation import APPROVAL_HOVER_S
from mission_planner.gcs.expansion import expandable
from mission_planner.gcs.geodesy import NAMES
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.tasking import (
    CONSTRAINT_KEYWORDS,
    TASK_DEFAULTS,
    TASKING_SCHEMA,
    choose_drones,
    meta_schema,
    plan,
    validate_tasking,
)
from mission_planner.gcs.test_plan_execution import fleet
from mission_planner.gcs.test_planner import states
from mission_planner.gcs.test_validation import readback
from world.cosys.landmarks import load

LANDMARKS = load()
FENCES = {name: readback() for name in NAMES}
SUBJECT = {
    "detector_prompt": "person",
    "threshold": 0.35,
    "look_for": {
        "objective": "Find the missing child",
        "target": "child",
        "attributes": [{"id": "jacket", "text": "red jacket"}],
        "ignore": [],
    },
}
CLICK = {"north_m": -300.0, "east_m": 100.0}


def task(**overrides):
    value = {
        "task": "area_search",
        "subject": copy.deepcopy(SUBJECT),
        "references": {
            "landmark_ids": ["campsite"],
            "points": [],
            "polygon": None,
            "direction_of_travel": {"compass": "south-east"},
            "subject_category": "child",
        },
        "fleet": {"drone_ids": None, "count": None},
        "settings": {key: None for key in [*TASK_DEFAULTS["area_search"], "dwell_seconds"]},
        "aftermath": "return_home",
        "region": {
            "band_index": None,
            "accept_partial": False,
            "radius_m": None,
            "half_angle_deg": None,
        },
        "expansion": {"automatic_proposals": False, "lead_seconds": None},
    }
    for key, override in overrides.items():
        if isinstance(override, dict) and isinstance(value[key], dict):
            value[key].update(override)
        else:
            value[key] = override
    return value


def compiled(tasking, fleet_states=None, clicks=(), version=1):
    return plan(
        tasking,
        version,
        states() if fleet_states is None else fleet_states,
        FENCES,
        LANDMARKS,
        (),
        clicks,
    )


def kinds(receipt):
    return {b["drone_id"]: [s["kind"] for s in b["steps"]] for b in receipt["plan"]["drones"]}


def walk(node, below_root):
    if isinstance(node, dict):
        for keyword in ("oneOf", "allOf", "uniqueItems"):
            assert keyword not in node, keyword
        if not below_root:
            assert "anyOf" not in node, "anyOf at the schema root"
        if "type" in node and node["type"] == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])
        for value in node.values():
            walk(value, True)
    elif isinstance(node, list):
        for value in node:
            walk(value, True)


class Schema(unittest.TestCase):
    def test_both_schema_copies_are_strict_compatible(self):
        for schema in (TASKING_SCHEMA, meta_schema()):
            Draft202012Validator.check_schema(schema)
            self.assertEqual(schema["type"], "object")
            walk(schema, False)
        encoded = json.dumps(meta_schema())
        for keyword in CONSTRAINT_KEYWORDS:
            self.assertNotIn(f'"{keyword}"', encoded)
        self.assertLess(len(encoded), 6000)
        self.assertIn('"maximum"', json.dumps(TASKING_SCHEMA))

    def test_defaults_fill_null_settings_and_inapplicable_settings_are_rejected(self):
        filled = validate_tasking(task(), LANDMARKS, [])
        self.assertEqual(
            filled["settings"], {**TASK_DEFAULTS["area_search"], "dwell_seconds": None}
        )
        explicit = validate_tasking(task(settings={"height_m": 50}), LANDMARKS, [])
        self.assertEqual(explicit["settings"]["height_m"], 50)
        self.assertEqual(explicit["settings"]["spacing_m"], 20)
        transit = task(
            task="transit",
            subject=None,
            references={"landmark_ids": ["snow-survey-hut"], "direction_of_travel": None},
            settings={"spacing_m": 10},
        )
        with self.assertRaises(PlanRejected) as caught:
            validate_tasking(transit, LANDMARKS, [])
        self.assertEqual(caught.exception.rejection["rule"], "schema")
        self.assertIn("spacing_m does not apply to transit", str(caught.exception))

    def test_semantic_rejections_are_structured(self):
        cases = [
            (task(subject=None), "schema", "needs a subject"),
            (task(settings={"zoom": 3}), "schema", "settings.zoom"),
            (task(references={"landmark_ids": ["nowhere"]}), "reference", "Unknown landmark"),
            (
                task(references={"landmark_ids": [], "points": [{"north_m": 0, "east_m": 0}]}),
                "reference",
                "not clicked",
            ),
            (task(fleet={"drone_ids": ["Drone1"], "count": 2}), "schema", "not both"),
            (
                task(task="route_search", references={"direction_of_travel": None}),
                "schema",
                "not a linear feature",
            ),
            (
                task(
                    task="transit",
                    references={"landmark_ids": ["campsite"], "direction_of_travel": None},
                ),
                "schema",
                "without observation",
            ),
            (
                task(task="datum_search", references={"direction_of_travel": None}),
                "schema",
                "region.radius_m",
            ),
            (
                task(references={"direction_of_travel": None}, region={"half_angle_deg": 30.0}),
                "schema",
                "needs a direction_of_travel",
            ),
            (
                task(
                    task="waypoint_route",
                    subject=None,
                    references={"landmark_ids": ["campsite"], "direction_of_travel": None},
                ),
                "schema",
                "two or more waypoints",
            ),
        ]
        for tasking, rule, text in cases:
            with self.subTest(text=text):
                with self.assertRaises(PlanRejected) as caught:
                    validate_tasking(tasking, LANDMARKS, [])
                self.assertEqual(caught.exception.rejection["rule"], rule)
                self.assertIn(text, str(caught.exception))
        with self.assertRaises(PlanRejected) as caught:
            compiled(task(references={"subject_category": "elderly"}))
        self.assertEqual(caught.exception.rejection["rule"], "no_distance_table")

    def test_participants_follow_health_and_lane_order(self):
        current = {state["id"]: state for state in states()}
        current["Drone3"]["link_lost"] = True
        self.assertEqual(choose_drones(task(), current), ["Drone1", "Drone2", "Drone4"])
        self.assertEqual(choose_drones(task(fleet={"count": 2}), current), ["Drone1", "Drone2"])
        self.assertEqual(
            choose_drones(task(fleet={"drone_ids": ["Drone4", "Drone1"]}), current),
            ["Drone1", "Drone4"],
        )
        with self.assertRaises(PlanRejected) as caught:
            choose_drones(task(fleet={"count": 4}), current)
        self.assertEqual(caught.exception.rejection["rule"], "aircraft_state")
        with self.assertRaises(PlanRejected) as caught:
            choose_drones(task(task="transit", fleet={"count": 2}), current)
        self.assertEqual(caught.exception.rejection["rule"], "participants")


class Templates(unittest.TestCase):
    def test_requested_4x_reaches_the_checked_observation_policy(self):
        receipt = compiled(
            task(
                task="datum_search",
                references={"direction_of_travel": None},
                settings={"zoom": 4, "spacing_m": 10},
                region={"radius_m": 150},
            )
        )
        self.assertEqual(receipt["tasking"]["settings"]["zoom"], 4)
        self.assertEqual(receipt["plan"]["search"]["zoom"], 4)
        for branch in receipt["plan"]["drones"]:
            for step in branch["steps"]:
                if step["kind"] == "survey":
                    self.assertLessEqual(step["spacing_m"], step["height_m"] / 3)

    def test_doctrine_band_fits_the_fleet_with_an_approval_hover_and_shrinks_with_battery(self):
        receipt = compiled(task(expansion={"automatic_proposals": True}))
        self.assertEqual(set(receipt["missions"]), set(NAMES))
        self.assertGreaterEqual(receipt["coverage"]["survey_percent"], 99.5)
        self.assertTrue(all(row["headroom_s"] > 0 for row in receipt["fit"].values()))
        self.assertTrue(
            all(row["approval_hover_s"] == APPROVAL_HOVER_S for row in receipt["fit"].values())
        )
        for steps in kinds(receipt).values():
            self.assertEqual(steps[:3], ["takeoff", "fly", "fly"])
            last_survey = len(steps) - 1 - steps[::-1].index("survey")
            self.assertEqual(steps[last_survey + 1], "hover")
            self.assertEqual(steps[-1], "return_home")
        hover = next(s for s in receipt["plan"]["drones"][0]["steps"] if s["kind"] == "hover")
        self.assertEqual(hover["seconds"], APPROVAL_HOVER_S)
        region = receipt["region"]
        self.assertEqual(region["kind"], "doctrine")
        self.assertEqual(region["direction_deg"], 135.0)
        self.assertEqual(region["table"]["n"], 903)
        self.assertEqual(region["bands"][0]["status"], "planned")
        self.assertGreater(len(region["bands"]), 1)
        self.assertEqual(region["bands"][1]["status"], "provisional")
        self.assertIn("n=903", receipt["rationale"][0])
        self.assertIn("hiker table stands in", receipt["rationale"][1])
        self.assertIn("approval hover", receipt["rationale"][-1])
        self.assertIn("1/2 of the sortie", receipt["rationale"][-1])
        self.assertEqual(
            receipt["tasking"]["settings"], {**TASK_DEFAULTS["area_search"], "dwell_seconds": None}
        )
        self.assertEqual({m["drone_id"] for m in receipt["sectors"]}, set(NAMES))
        self.assertTrue(
            all({"entry", "exit", "layer_agl_m", "rows"} <= set(m) for m in receipt["sectors"])
        )
        perimeter = Polygon(
            [(p["north_m"], p["east_m"]) for p in receipt["plan"]["search"]["perimeter"]]
        )
        band_polygon = Polygon([(p["north_m"], p["east_m"]) for p in region["bands"][0]["polygon"]])
        self.assertTrue(perimeter.buffer(0.01).contains(band_polygon))
        self.assertLess(perimeter.exterior.distance(band_polygon.exterior), 2.0)
        self.assertAlmostEqual(
            perimeter.area,
            region["bands"][0]["area_m2"],
            delta=region["bands"][0]["area_m2"] * 0.03,
        )
        json.dumps(receipt, allow_nan=False)
        whole = compiled(task())
        self.assertNotIn("hover", [k for steps in kinds(whole).values() for k in steps])
        self.assertGreater(
            whole["region"]["bands"][0]["area_m2"], 1.5 * region["bands"][0]["area_m2"]
        )
        low = states()
        for state in low:
            state["battery_percent"] = 72
        smaller = compiled(task(), low)
        self.assertLess(
            smaller["region"]["bands"][0]["area_m2"], whole["region"]["bands"][0]["area_m2"]
        )
        self.assertTrue(all(row["headroom_s"] > 0 for row in smaller["fit"].values()))
        self.assertTrue(
            all(m["battery_required_percent"] <= 72 for m in smaller["missions"].values())
        )
        # Half of this endurance buys less search than the round trip costs: band 1 takes the
        # whole sortie, no approval hover is planned and expansion waits for the fleet to return.
        whole_low = compiled(task(expansion={"automatic_proposals": True}), low)
        self.assertFalse(whole_low["region"]["in_flight_expansion"])
        self.assertTrue(region["in_flight_expansion"])
        self.assertNotIn("hover", [k for steps in kinds(whole_low).values() for k in steps])
        self.assertIn("whole sortie", whole_low["rationale"][-1])
        self.assertIn("off the stated direction", whole_low["rationale"][-2])
        self.assertFalse(expandable(whole_low))
        self.assertTrue(expandable(receipt))

    def test_route_search_needs_one_aircraft_per_route(self):
        with self.assertRaises(PlanRejected) as caught:
            compiled(
                task(
                    task="route_search",
                    references={"landmark_ids": ["creek-loop"], "direction_of_travel": None},
                    fleet={"count": 2},
                )
            )
        self.assertEqual(caught.exception.rejection["rule"], "participants")
        self.assertIn("fleet.count 1", str(caught.exception))
        self.assertEqual(caught.exception.rejection["detail"]["aircraft"], ["Drone1", "Drone3"])

    def test_second_band_with_two_aircraft_marks_the_first_band_previous(self):
        receipt = compiled(
            task(
                references={
                    "landmark_ids": ["footbridge"],
                    "direction_of_travel": {"degrees": 10.0},
                },
                fleet={"count": 2},
                region={"band_index": 2},
            )
        )
        self.assertEqual(sorted(receipt["missions"]), ["Drone1", "Drone3"])
        statuses = [b["status"] for b in receipt["region"]["bands"][:3]]
        self.assertEqual(statuses, ["previous", "planned", "provisional"])
        self.assertNotIn("hover", [k for steps in kinds(receipt).values() for k in steps])
        self.assertTrue(all(row["headroom_s"] > 0 for row in receipt["fit"].values()))

    def test_operator_polygon_and_datum_disc_search_whole_and_coverage_is_enforced(self):
        polygon = [
            {"north_m": -650, "east_m": -150},
            {"north_m": -650, "east_m": 150},
            {"north_m": -450, "east_m": 150},
            {"north_m": -450, "east_m": -150},
        ]
        receipt = compiled(
            task(references={"landmark_ids": [], "polygon": polygon, "direction_of_travel": None})
        )
        self.assertEqual(receipt["region"]["kind"], "operator_polygon")
        self.assertAlmostEqual(receipt["coverage"]["survey_percent"], 100, delta=0.5)
        self.assertAlmostEqual(receipt["region"]["bands"][0]["area_m2"], 60000, delta=1)
        outside = [{**p, "north_m": p["north_m"] + 1400} for p in polygon]
        with self.assertRaises(PlanRejected) as caught:
            compiled(
                task(
                    references={"landmark_ids": [], "polygon": outside, "direction_of_travel": None}
                )
            )
        self.assertEqual(caught.exception.rejection["rule"], "fence")
        datum = task(
            task="datum_search",
            references={"landmark_ids": [], "points": [CLICK], "direction_of_travel": None},
            region={"radius_m": 150},
        )
        receipt = compiled(datum, clicks=[CLICK])
        self.assertEqual(receipt["region"]["kind"], "datum_disc")
        self.assertEqual(receipt["region"]["ipp"]["point"], CLICK)
        self.assertGreaterEqual(receipt["coverage"]["survey_percent"], 99.5)
        with patch("mission_planner.gcs.tasking.COVERAGE_PERCENT", 100.5):
            with self.assertRaises(PlanRejected) as caught:
                compiled(datum, clicks=[CLICK])
            self.assertEqual(caught.exception.rejection["rule"], "coverage")
            partial = compiled(
                {**datum, "region": {**datum["region"], "accept_partial": True}}, clicks=[CLICK]
            )
            self.assertTrue(partial["tasking"]["region"]["accept_partial"])

    def test_route_boundary_inspection_transit_and_waypoints_compile(self):
        route = compiled(
            task(
                task="route_search",
                references={"landmark_ids": ["fire-road"], "direction_of_travel": None},
            )
        )
        self.assertEqual(
            kinds(route), {"Drone1": ["takeoff", "fly", "fly", "follow", "fly", "return_home"]}
        )
        self.assertIsNone(route["coverage"])
        self.assertEqual(route["region"]["routes"][0]["landmark_id"], "fire-road")
        self.assertTrue(route["plan"]["drones"][0]["steps"][3]["observe"])
        plain = compiled(
            task(
                task="route_search",
                subject=None,
                references={"landmark_ids": ["creek-loop"], "direction_of_travel": None},
                fleet={"count": 1},
                settings={"height_m": 40},
                aftermath="hover",
            )
        )
        self.assertEqual(kinds(plain), {"Drone1": ["takeoff", "fly", "fly", "follow", "hover"]})
        self.assertNotIn("search", plain["plan"])
        self.assertNotIn("observe", plain["plan"]["drones"][0]["steps"][3])
        two = compiled(
            task(
                task="route_search",
                references={
                    "landmark_ids": ["fire-road", "trailhead-access"],
                    "direction_of_travel": None,
                },
                fleet={"count": 2},
            )
        )
        self.assertEqual(sorted(two["missions"]), ["Drone1", "Drone3"])
        with self.assertRaises(PlanRejected) as caught:
            compiled(
                task(
                    task="route_search",
                    references={
                        "landmark_ids": ["fire-road", "trailhead-access"],
                        "direction_of_travel": None,
                    },
                    fleet={"count": 1},
                )
            )
        self.assertEqual(caught.exception.rejection["rule"], "participants")
        boundary = compiled(
            task(
                task="boundary_check",
                references={"landmark_ids": ["beaver-pond"], "direction_of_travel": None},
            )
        )
        points = boundary["plan"]["drones"][0]["steps"][3]["points"]
        self.assertEqual(points[0], points[-1])
        inspect = compiled(
            task(
                task="point_inspection",
                subject={**SUBJECT, "detector_prompt": "bear"},
                references={"landmark_ids": ["ranger-cabin"], "direction_of_travel": None},
                fleet={"count": 1},
                settings={"dwell_seconds": 30},
            )
        )
        self.assertEqual(
            kinds(inspect)["Drone1"],
            ["takeoff", "fly", "fly", "fly", "circle", "hover", "fly", "return_home"],
        )
        orbit = inspect["plan"]["drones"][0]["steps"][4]
        self.assertTrue(orbit["observe"])
        self.assertEqual(inspect["plan"]["search"]["detector_prompt"], "bear")
        self.assertEqual(inspect["plan"]["drones"][0]["steps"][5]["seconds"], 30)
        look = compiled(
            task(
                task="point_inspection",
                subject=None,
                references={"landmark_ids": ["ranger-cabin"], "direction_of_travel": None},
                fleet={"count": 1},
            )
        )
        self.assertNotIn("search", look["plan"])
        self.assertNotIn("observe", look["plan"]["drones"][0]["steps"][4])
        self.assertEqual(kinds(look)["Drone1"][4:], ["circle", "fly", "return_home"])
        transit = compiled(
            task(
                task="transit",
                subject=None,
                references={"landmark_ids": ["snow-survey-hut"], "direction_of_travel": None},
                fleet={"drone_ids": ["Drone2"]},
                settings={"dwell_seconds": 60},
                aftermath="hover",
            )
        )
        self.assertEqual(
            kinds(transit), {"Drone2": ["takeoff", "fly", "fly", "fly", "hover", "hover"]}
        )
        self.assertEqual(transit["plan"]["drones"][0]["steps"][4]["seconds"], 60)
        self.assertIsNone(transit["missions"]["Drone2"]["duration_s"])
        self.assertTrue(transit["fit"]["Drone2"]["indefinite"])
        self.assertGreater(transit["fit"]["Drone2"]["headroom_s"], 0)
        waypoints = compiled(
            task(
                task="waypoint_route",
                subject=None,
                references={
                    "landmark_ids": [],
                    "points": [CLICK, {"north_m": -100.0, "east_m": -200.0}],
                    "direction_of_travel": None,
                },
                fleet={"count": 1},
            ),
            clicks=[CLICK, {"north_m": -100.0, "east_m": -200.0}],
        )
        self.assertEqual(
            kinds(waypoints)["Drone1"],
            ["takeoff", "fly", "fly", "fly", "fly", "fly", "return_home"],
        )
        self.assertEqual(
            waypoints["sectors"][0]["targets"][1], {"north_m": -100.0, "east_m": -200.0}
        )
        for receipt in (route, boundary, inspect, look, transit, waypoints):
            json.dumps(receipt, allow_nan=False)


class FleetPreview(unittest.IsolatedAsyncioTestCase):
    async def test_preview_task_compiles_against_the_fleet_snapshot_and_fence_version(self):
        aircraft = fleet(states())
        receipt = await aircraft.preview_task(
            task(
                task="transit",
                subject=None,
                references={"landmark_ids": ["campsite"], "direction_of_travel": None},
                fleet={"count": 1},
            ),
            3,
            [],
        )
        self.assertEqual(receipt["plan"]["version"], 3)
        self.assertEqual(receipt["tasking"]["task"], "transit")
        aircraft.fences_verified = False
        with self.assertRaisesRegex(AssertionError, "launch is locked"):
            await aircraft.preview_task(task(), 4, [])


if __name__ == "__main__":
    unittest.main()

"""Real tool contracts reject unsafe and ambiguous requests before execution."""

import copy
import unittest

from mission_planner.agents.tools import TOOLS, validate_arguments
from mission_planner.gcs.test_planner import search_plan
from world.cosys.terrain import EAST_MAX_M, EAST_MIN_M, NORTH_MAX_M


class ToolContracts(unittest.TestCase):
    def test_oversized_fleet_error_identifies_the_array_without_dumping_the_plan(self):
        value = search_plan()
        value["drones"].append(copy.deepcopy(value["drones"][0]))
        with self.assertRaises(AssertionError) as caught:
            validate_arguments("preview_plan", {"plan": value})
        self.assertEqual(
            str(caught.exception), "plan.drones: requires at most 4 items; received 5."
        )

    def test_step_error_names_the_field_for_the_requested_step_kind(self):
        value = search_plan()
        del value["drones"][0]["steps"][0]["spacing_m"]
        with self.assertRaises(AssertionError) as caught:
            validate_arguments("preview_plan", {"plan": value})
        self.assertEqual(
            str(caught.exception),
            "plan.drones[0].steps[0]: Required values missing: spacing m.",
        )
        self.assertNotIn("circle", str(caught.exception))
        del value["drones"][0]["steps"][0]["kind"]
        with self.assertRaisesRegex(
            AssertionError, r"plan.drones\[0\].steps\[0\].kind: Required values missing: kind"
        ):
            validate_arguments("preview_plan", {"plan": value})

    def test_plan_circle_radius_reports_the_step_limit_instead_of_map_focus_limits(self):
        value = {
            "version": 1,
            "drones": [
                {
                    "drone_id": "Drone1",
                    "steps": [
                        {
                            "kind": "circle",
                            "north_m": 0,
                            "east_m": 0,
                            "radius_m": 2,
                            "height_m": 40,
                            "speed_mps": 5,
                            "turns": 1,
                            "clockwise": True,
                        }
                    ],
                }
            ],
        }
        with self.assertRaises(AssertionError) as caught:
            validate_arguments("preview_plan", {"plan": value})
        self.assertEqual(
            str(caught.exception),
            "plan.drones[0].steps[0].radius_m: must be at least 3.5; received 2.",
        )

    def test_registry_has_only_available_subsystems(self):
        names = [tool["name"] for tool in TOOLS]
        self.assertIn("open_feed", names)
        self.assertTrue({"preview_plan", "request_launch", "apply_plan_change"} <= set(names))
        self.assertTrue({"take_off", "fly_to", "fly_route"}.isdisjoint(names))
        self.assertIn("divide_survey", names)

    def test_survey_division_accepts_operator_choices_and_continuation_stays_on_survey_steps(self):
        value = search_plan()
        step = value["drones"][0]["steps"][0]
        arguments = {
            "drone_ids": ["Drone1", "Drone2", "Drone3", "Drone4"],
            **{k: v for k, v in step.items() if k not in ("kind", "observe")},
        }
        validate_arguments("divide_survey", arguments)
        validate_arguments("divide_survey", {**arguments, "drone_ids": ["Drone1"]})
        step["start_at"] = 3
        validate_arguments("preview_plan", {"plan": value})
        step["start_at"] = -1
        with self.assertRaisesRegex(AssertionError, "start_at"):
            validate_arguments("preview_plan", {"plan": value})
        del step["start_at"]
        value["drones"][0]["steps"][-1]["start_at"] = 1
        with self.assertRaisesRegex(AssertionError, "start_at"):
            validate_arguments("preview_plan", {"plan": value})

    def test_feed_requires_one_known_drone(self):
        validate_arguments("open_feed", {"drone_id": "Drone3"})
        with self.assertRaisesRegex(AssertionError, "Required values missing"):
            validate_arguments("open_feed", {})
        with self.assertRaisesRegex(AssertionError, "Unknown drone"):
            validate_arguments("open_feed", {"drone_id": "Drone9"})
        with self.assertRaisesRegex(AssertionError, "Unrecognized arguments"):
            validate_arguments("open_feed", {"drone_id": "Drone1", "start_flight": True})

    def test_inspection_does_not_request_a_flight_heading(self):
        arguments = {"drone_id": "Drone1", "prompt": "building", "threshold": 0.5, "duration_s": 20}
        validate_arguments("start_inspection", arguments)
        validate_arguments("face_heading", {"drone_id": "Drone1", "heading_deg": 90})
        with self.assertRaisesRegex(AssertionError, "Unrecognized arguments"):
            validate_arguments("start_inspection", {**arguments, "heading_deg": 90})

    def test_candidate_release_has_no_flight_destination_or_drone_selector(self):
        arguments = {"review_id": "one-review", "reason": "The crop shows a tree stump"}
        validate_arguments("disregard_and_continue", arguments)
        with self.assertRaisesRegex(AssertionError, "Unrecognized arguments"):
            validate_arguments("disregard_and_continue", {**arguments, "drone_id": "Drone2"})

    def test_follow_selects_exact_evidence_without_replacing_the_search_policy(self):
        arguments = {
            "inspection_id": "inspection",
            "frame_id": "observed-frame",
            "candidate_index": 0,
            "duration_s": 90,
        }
        validate_arguments("follow_candidate", arguments)
        validate_arguments("get_candidate", {"inspection_id": "inspection"})
        validate_arguments("center_candidate", {"inspection_id": "inspection", "zoom": 4})
        with self.assertRaises(AssertionError):
            validate_arguments("center_candidate", {"inspection_id": "inspection", "zoom": 5})
        for change in ({"candidate_index": -1}, {"duration_s": 0}, {"speed_mps": 15}):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                validate_arguments("follow_candidate", {**arguments, **change})

    def test_finding_requires_selected_candidate_and_visual_assessment(self):
        arguments = {
            "inspection_id": "inspection",
            "frame_id": "frame",
            "candidate_index": 0,
            "assessment": "matching",
            "headline": "Person matching reported clothing",
            "summary": "Pink top and light-blue jeans; identity unknown",
        }
        validate_arguments("publish_evidence", arguments)
        for change in (
            {"candidate_index": -1},
            {"assessment": "confirmed"},
            {"headline": "x" * 101},
        ):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                validate_arguments("publish_evidence", {**arguments, **change})
        for key in ("candidate_index", "assessment", "headline"):
            with self.subTest(missing=key), self.assertRaises(AssertionError):
                validate_arguments(
                    "publish_evidence", {k: v for k, v in arguments.items() if k != key}
                )
        validate_arguments(
            "confirm_evidence", {"evidence_id": "finding", "operator_event_id": "message"}
        )

    def test_meta_strict_tools_match_supported_schema_subset(self):
        def check(schema):
            self.assertTrue({"uniqueItems", "allOf", "oneOf"}.isdisjoint(schema))
            if "type" in schema and schema["type"] == "object":
                self.assertEqual(set(schema["required"]), set(schema["properties"]))
                self.assertFalse(schema["additionalProperties"])
            for value in schema.values():
                if isinstance(value, dict):
                    check(value)
                elif isinstance(value, list):
                    for child in value:
                        if isinstance(child, dict):
                            check(child)

        for tool in TOOLS:
            if tool["strict"]:
                with self.subTest(tool=tool["name"]):
                    schema = tool["parameters"]
                    self.assertEqual(schema["type"], "object")
                    self.assertTrue({"anyOf", "enum", "not"}.isdisjoint(schema))
                    check(schema)
        self.assertFalse(next(t for t in TOOLS if t["name"] == "preview_plan")["strict"])

    def test_unknown_drone_is_explicit(self):
        with self.assertRaisesRegex(AssertionError, "Unknown drone: Drone9"):
            validate_arguments("take_off", {"drone_id": "Drone9", "altitude_above_home_m": 30})

    def test_outside_route_is_refused(self):
        with self.assertRaisesRegex(AssertionError, f"fence limit is {NORTH_MAX_M:.1f} m"):
            validate_arguments(
                "fly_route",
                {
                    "drone_id": "Drone1",
                    "speed_mps": 5,
                    "waypoints": [{"north_m": 900, "east_m": 0, "altitude_above_home_m": 30}],
                },
            )

    def test_corridor_corners_are_not_rectangular_expansion(self):
        with self.assertRaisesRegex(AssertionError, "outside the fence"):
            validate_arguments(
                "fly_route",
                {
                    "drone_id": "Drone1",
                    "waypoints": [{"north_m": -820, "east_m": 50, "altitude_above_home_m": 30}],
                    "speed_mps": 5,
                },
            )

    def test_map_fields_are_target_specific(self):
        validate_arguments("show_on_map", {"target": "point", "north_m": 10, "east_m": 20})
        with self.assertRaisesRegex(AssertionError, "Unknown drone"):
            validate_arguments(
                "show_on_map", {"target": "point", "north_m": 10, "east_m": 20, "drone_id": "null"}
            )
        with self.assertRaisesRegex(AssertionError, "requires exactly"):
            validate_arguments("show_on_map", {"target": "area", "north_m": 10, "east_m": 20})

    def test_map_focus_at_rectangular_east_and_west_edges(self):
        for east in (EAST_MIN_M, EAST_MAX_M):
            validate_arguments("show_on_map", {"target": "point", "north_m": 0, "east_m": east})
        validate_arguments(
            "show_on_map",
            {"target": "area", "north_m": 0, "east_m": EAST_MAX_M - 20, "radius_m": 20},
        )
        with self.assertRaisesRegex(AssertionError, "Map area extends outside"):
            validate_arguments(
                "show_on_map",
                {"target": "area", "north_m": 0, "east_m": EAST_MAX_M - 10, "radius_m": 20},
            )

    def test_flight_parameters_are_required_and_bounded(self):
        with self.assertRaisesRegex(AssertionError, "Required values missing"):
            validate_arguments("take_off", {"drone_id": "Drone1"})
        with self.assertRaisesRegex(AssertionError, "use 10–100 m"):
            validate_arguments("take_off", {"drone_id": "Drone1", "altitude_above_home_m": 121})
        with self.assertRaisesRegex(AssertionError, "Unrecognized arguments"):
            validate_arguments("hold", {"drone_id": "Drone1", "force": True})


if __name__ == "__main__":
    unittest.main()

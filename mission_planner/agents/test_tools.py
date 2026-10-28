"""Real tool contracts reject unsafe and ambiguous requests before execution."""

import unittest

from mission_planner.agents.tools import TOOLS, validate_arguments


class ToolContracts(unittest.TestCase):
    def test_registry_has_only_available_subsystems(self):
        self.assertEqual(len(TOOLS), 18)
        self.assertIn("open_feed", [t["name"] for t in TOOLS])

    def test_feed_requires_one_known_drone(self):
        validate_arguments("open_feed", {"drone_id": "Drone3"})
        with self.assertRaisesRegex(AssertionError, "Required values missing"):
            validate_arguments("open_feed", {})
        with self.assertRaisesRegex(AssertionError, "Unknown drone"):
            validate_arguments("open_feed", {"drone_id": "Drone9"})
        with self.assertRaisesRegex(AssertionError, "Unrecognized arguments"):
            validate_arguments("open_feed", {"drone_id": "Drone1", "start_flight": True})

    def test_unknown_drone_is_explicit(self):
        with self.assertRaisesRegex(AssertionError, "Unknown drone: Drone9"):
            validate_arguments("take_off", {"drone_id": "Drone9", "altitude_above_home_m": 30})

    def test_outside_route_is_refused(self):
        with self.assertRaisesRegex(AssertionError, "fence limit is 804.7 m"):
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
                "fly_to",
                {
                    "drone_id": "Drone1",
                    "north_m": -820,
                    "east_m": 50,
                    "altitude_above_home_m": 30,
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

    def test_flight_parameters_are_required_and_bounded(self):
        with self.assertRaisesRegex(AssertionError, "Required values missing"):
            validate_arguments("take_off", {"drone_id": "Drone1"})
        with self.assertRaisesRegex(AssertionError, "use 10–100 m"):
            validate_arguments("take_off", {"drone_id": "Drone1", "altitude_above_home_m": 121})
        with self.assertRaisesRegex(AssertionError, "Unrecognized arguments"):
            validate_arguments("hold", {"drone_id": "Drone1", "force": True})


if __name__ == "__main__":
    unittest.main()

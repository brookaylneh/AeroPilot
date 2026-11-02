"""Actionable flight-plan errors preserve the full safety checks."""

import unittest
from unittest.mock import patch

from mission_planner.gcs.geodesy import NAMES
from mission_planner.gcs.planner import compile_plan
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.test_planner import airborne, compile_steps, fly, hover, plan, states
from mission_planner.gcs.test_validation import readback
from world.cosys.landmarks import load


class PlanDiagnostics(unittest.TestCase):
    def test_external_reservation_has_no_invented_plan_step(self):
        fleet = airborne()
        target = compile_steps([fly(-770, -2.5), hover()], fleet)["route"][-1]
        with self.assertRaises(PlanRejected) as caught:
            compile_plan(
                plan([fly(-770, -2.5), hover()]),
                fleet,
                {name: readback() for name in NAMES},
                load(),
                [("Drone3", [target])],
            )
        self.assertEqual(caught.exception.rejection["conflict"]["step_index"], 0)
        self.assertEqual(caught.exception.rejection["conflict"]["step_kind"], "fly")
        self.assertIsNone(caught.exception.rejection["conflict"]["other_step_index"])
        self.assertIsNone(caught.exception.rejection["conflict"]["other_step_kind"])

    def test_step_ranges_partition_every_motion_segment_including_join_and_land(self):
        home = states()[0]["home"]
        mission = compile_steps(
            [
                fly(-770, -2.5),
                {"kind": "hover", "seconds": 3},
                fly(home["north_m"], home["east_m"]),
                {"kind": "land"},
            ],
            airborne(),
        )
        ranges = mission["step_ranges"]
        self.assertEqual(
            [
                i
                for r in ranges
                for i in range(r["first_route_segment"], r["last_route_segment"] + 1)
            ],
            list(range(len(mission["route"]) - 1)),
        )
        self.assertEqual(ranges[0]["first_route_segment"], 0)
        self.assertEqual(ranges[-1]["last_route_segment"], len(mission["route"]) - 2)

    def test_world_fence_rejects_apron_shortcut_before_terrain_sampling(self):
        with (
            patch("mission_planner.gcs.planner.terrain_leg") as terrain,
            self.assertRaisesRegex(AssertionError, "exits the world fence") as caught,
        ):
            compile_steps([fly(-790, 1000), hover()], airborne(-820, 0))
        terrain.assert_not_called()
        self.assertIn("Transit may leave the search perimeter", str(caught.exception))
        self.assertIn("Drone1 step 1 (fly)", str(caught.exception))
        self.assertEqual(caught.exception.rejection["rule"], "fence")
        self.assertEqual(caught.exception.rejection["step_index"], 0)


if __name__ == "__main__":
    unittest.main()

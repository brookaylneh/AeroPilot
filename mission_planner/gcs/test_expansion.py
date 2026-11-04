"""Next-band proposals and strip reassignment built from a compiled search and live progress."""

import unittest

from pymavlink.dialects.v20 import ardupilotmega as MAV
from shapely import Point, Polygon, union_all

from mission_planner.gcs.expansion import (
    EXPANSION_LEAD_S,
    due,
    expandable,
    expansion_request,
    failed_remainder,
    reassignment,
    remaining_steps,
    work_remaining_s,
)
from mission_planner.gcs.geodesy import NAMES, ORIGIN
from mission_planner.gcs.planner import compile_plan, survey_points
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.tasking import plan
from mission_planner.gcs.test_planner import states
from mission_planner.gcs.test_tasking import FENCES, LANDMARKS, task
from world.cosys.terrain import terrain_height


def search_receipt():
    return plan(
        task(
            settings={"speed_mps": 5, "spacing_m": 20, "zoom": 1},
            expansion={"automatic_proposals": True},
        ),
        1,
        states(),
        FENCES,
        LANDMARKS,
        (),
        [],
    )


def survey_index(branch):
    return next(i for i, step in enumerate(branch["steps"]) if step["kind"] == "survey")


def airborne_mid_survey(receipt, battery=96, late=()):
    """Every participant airborne two rows into its first survey step, or as far as it has;
    drones named in late are on their final row."""
    fleet = states()
    progress = {}
    for branch in receipt["plan"]["drones"]:
        name = branch["drone_id"]
        state = next(s for s in fleet if s["id"] == name)
        index = survey_index(branch)
        step = branch["steps"][index]
        points = survey_points(step)
        start_at = len(points) - 2 if name in late else min(4, len(points) - 1)
        point = points[start_at]
        home_z = state["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"]
        state.update(
            north_m=point["north_m"],
            east_m=point["east_m"],
            altitude_above_home_m=terrain_height(point["north_m"], point["east_m"])
            + step["height_m"]
            - home_z,
            armed=True,
            landed_state=MAV.MAV_LANDED_STATE_IN_AIR,
            battery_percent=battery,
            mode="AUTO",
        )
        ranges = receipt["missions"][name]["step_ranges"]
        remaining = sum(r["duration_s"] for r in ranges if r["step_index"] >= index) * 0.5
        progress[name] = {
            "version": 1,
            "current_step": index,
            "completed_steps": list(range(index)),
            "time_remaining_s": remaining,
            "survey_start_at": start_at,
        }
    return fleet, progress


class Expansion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = search_receipt()

    def test_dense_search_expansion_rejects_conflicting_unfinished_rows(self):
        receipt = plan(
            task(expansion={"automatic_proposals": True}),
            1,
            states(),
            FENCES,
            LANDMARKS,
            (),
            [],
        )
        fleet, progress = airborne_mid_survey(receipt, battery=78, late=NAMES)
        request, searched, continuing = expansion_request(receipt, progress, fleet)
        with self.assertRaises(PlanRejected) as caught:
            plan(
                request,
                2,
                fleet,
                FENCES,
                LANDMARKS,
                (),
                [],
                searched=searched,
                continuing=continuing,
            )
        self.assertEqual(caught.exception.rejection["rule"], "separation")
        self.assertIn("survey", str(caught.exception))

    def test_due_when_any_drone_nears_the_end_of_its_work(self):
        receipt = self.receipt
        self.assertTrue(expandable(receipt))
        fleet, progress = airborne_mid_survey(receipt)
        branch = receipt["plan"]["drones"][0]
        name = branch["drone_id"]
        mission = receipt["missions"][name]
        tail = sum(
            r["duration_s"]
            for r in mission["step_ranges"]
            if r["step_index"]
            > max(i for i, s in enumerate(branch["steps"]) if s["kind"] == "survey")
        )
        self.assertGreater(tail, 0)
        for row in progress.values():
            row["time_remaining_s"] = 10000.0
        progress[name]["time_remaining_s"] = tail + EXPANSION_LEAD_S + 1
        self.assertAlmostEqual(
            work_remaining_s(branch, mission, progress[name]), EXPANSION_LEAD_S + 1
        )
        self.assertFalse(due(receipt, progress, fleet, EXPANSION_LEAD_S))
        progress[name]["time_remaining_s"] = tail + EXPANSION_LEAD_S - 1
        self.assertTrue(due(receipt, progress, fleet, EXPANSION_LEAD_S))
        progress[name]["time_remaining_s"] = None
        self.assertFalse(due(receipt, progress, fleet, EXPANSION_LEAD_S))
        self.assertFalse(due(receipt, progress, states(), EXPANSION_LEAD_S))
        quiet = {
            **receipt,
            "tasking": {
                **receipt["tasking"],
                "expansion": {"automatic_proposals": False, "lead_seconds": None},
            },
        }
        self.assertFalse(expandable(quiet))

    def test_expansion_request_continues_unfinished_rows_outside_the_searched_band(self):
        receipt = self.receipt
        fleet, progress = airborne_mid_survey(receipt, battery=78, late=NAMES)
        tasking, searched, continuing = expansion_request(receipt, progress, fleet)
        self.assertEqual(
            tasking["fleet"],
            {"drone_ids": [b["drone_id"] for b in receipt["plan"]["drones"]], "count": None},
        )
        self.assertEqual(searched, [receipt["region"]["bands"][0]["polygon"]])
        self.assertEqual(set(continuing), set(NAMES))
        for name, steps in continuing.items():
            self.assertEqual(steps[0]["kind"], "survey")
            self.assertEqual(steps[0]["start_at"], progress[name]["survey_start_at"])
        expanded = plan(
            tasking, 2, fleet, FENCES, LANDMARKS, (), [], searched=searched, continuing=continuing
        )
        self.assertEqual(expanded["plan"]["version"], 2)
        first = Polygon(
            [(p["north_m"], p["east_m"]) for p in receipt["region"]["bands"][0]["polygon"]]
        )
        second = Polygon(
            [
                (p["north_m"], p["east_m"])
                for p in next(b for b in expanded["region"]["bands"] if b["status"] == "planned")[
                    "polygon"
                ]
            ]
        )
        self.assertLess(first.intersection(second).area, 0.01 * second.area)
        for branch in expanded["plan"]["drones"]:
            kinds = [s["kind"] for s in branch["steps"]]
            # Unfinished rows first from where the drone is, then a layered join to the new strip.
            self.assertEqual(kinds[:3], ["survey", "fly", "survey"])
            self.assertIn("start_at", branch["steps"][0])
            self.assertEqual(branch["steps"][1]["height_m"], branch["steps"][-2]["height_m"])
            self.assertGreaterEqual(kinds.count("survey"), 2)
        self.assertEqual(
            [b["status"] for b in expanded["region"]["bands"][:2]], ["previous", "planned"]
        )
        self.assertEqual(expanded["region"]["bands"][1]["index"], 2)
        self.assertGreaterEqual(expanded["coverage"]["survey_percent"], 99.5)
        self.assertIn("finish their current rows first", expanded["rationale"][-1])
        self.assertTrue(all(row["headroom_s"] > 0 for row in expanded["fit"].values()))
        perimeter = Polygon(
            [(p["north_m"], p["east_m"]) for p in expanded["plan"]["search"]["perimeter"]]
        )
        self.assertTrue(perimeter.buffer(0.01).contains(union_all([first, second]).convex_hull))

    def test_low_endurance_drone_returns_home_instead_of_taking_a_strip(self):
        receipt = self.receipt
        fleet, progress = airborne_mid_survey(receipt, battery=78, late=NAMES)
        low = next(s for s in fleet if s["id"] == "Drone4")
        low["battery_percent"] = 52
        tasking, searched, continuing = expansion_request(receipt, progress, fleet)
        expanded = plan(
            tasking, 2, fleet, FENCES, LANDMARKS, (), [], searched=searched, continuing=continuing
        )
        kinds = {b["drone_id"]: [s["kind"] for s in b["steps"]] for b in expanded["plan"]["drones"]}
        self.assertEqual(kinds["Drone4"], ["fly", "return_home"])
        self.assertTrue(all(k.count("survey") >= 2 for n, k in kinds.items() if n != "Drone4"))
        self.assertIn("Drone4 return home without a strip", expanded["rationale"][-1])
        self.assertTrue(
            next(m for m in expanded["sectors"] if m["drone_id"] == "Drone4")["returning"]
        )
        for state in fleet:
            state["battery_percent"] = 52
        with self.assertRaises(PlanRejected) as caught:
            plan(
                tasking,
                2,
                fleet,
                FENCES,
                LANDMARKS,
                (),
                [],
                searched=searched,
                continuing=continuing,
            )
        self.assertEqual(caught.exception.rejection["rule"], "endurance")

    def test_reassignment_sends_the_failed_drone_home_and_hands_later_rows_on(self):
        receipt = self.receipt
        failed = "Drone3"
        fleet, progress = airborne_mid_survey(receipt, late=(failed,))
        branch = next(b for b in receipt["plan"]["drones"] if b["drone_id"] == failed)
        steps = remaining_steps(branch, progress[failed])
        start_at = progress[failed]["survey_start_at"]
        self.assertEqual((steps[0]["kind"], steps[0]["start_at"]), ("survey", start_at))
        handed = failed_remainder(branch, progress[failed])
        points = survey_points(branch["steps"][survey_index(branch)])
        self.assertEqual(bool(handed), len(points) > 2 * (start_at // 2 + 1))
        # The row the failed drone stopped on is not handed over; later rows are.
        row = points[2 * (start_at // 2) : 2 * (start_at // 2) + 2]
        for _, remainder in handed:
            for p in row:
                self.assertFalse(remainder.buffer(-0.01).covers(Point(p["north_m"], p["east_m"])))
        request = reassignment(receipt, progress, fleet, failed)
        self.assertEqual(request["version"], 2)
        kinds = {b["drone_id"]: [s["kind"] for s in b["steps"]] for b in request["drones"]}
        self.assertEqual(kinds[failed], ["fly", "return_home"])
        if handed:
            receiver = next(n for n, k in kinds.items() if k.count("survey") >= 2)
            self.assertNotEqual(receiver, failed)
        compiled = compile_plan(request, fleet, FENCES, LANDMARKS)
        self.assertEqual(set(compiled["missions"]), set(NAMES))
        self.assertTrue(
            all(m["battery_required_percent"] <= 96 for m in compiled["missions"].values())
        )


if __name__ == "__main__":
    unittest.main()

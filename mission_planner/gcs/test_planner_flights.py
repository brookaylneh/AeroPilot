"""Complete four-drone searches from the launch pad through checked home landing."""

import unittest

from pymavlink.dialects.v20 import ardupilotmega as MAV

from mission_planner.gcs.geodesy import NAMES, point_in_polygon, to_geodetic
from mission_planner.gcs.planner import compile_plan, divide_survey, survey_points
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.test_planner import fly, rectangle, search_plan, states
from mission_planner.gcs.test_validation import readback
from world.cosys.landmarks import load


def recorded_pad():
    fleet = states()
    for i, state in enumerate(fleet):
        north = -813.8423300437806 if i < 2 else -818.8481657414441
        east = -2.4898236589788727 if i % 2 == 0 else 2.5074195861050925
        home_north = -813.8312306296875 if i < 2 else -818.8370663273507
        home_east = -2.4986216219168496 if i % 2 == 0 else 2.4986216231671157
        latitude, longitude = to_geodetic(home_north, home_east)
        state.update(north_m=north, east_m=east, altitude_above_home_m=0.002)
        state["home"] = {
            "north_m": home_north,
            "east_m": home_east,
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "altitude_msl_m": 2632.77,
        }
    return fleet


def complete_search(fleet, bounds, spacing, bearing):
    value = search_plan()
    value["search"]["perimeter"] = rectangle(*bounds)
    value["search"]["max_lock_seconds"] = 180
    value["drones"] = divide_survey(
        ["Drone3", "Drone1", "Drone2", "Drone4"], rectangle(*bounds), spacing, bearing, 40, 10
    )["drones"]
    for i, branch in enumerate(value["drones"]):
        name = branch["drone_id"]
        state = next(state for state in fleet if state["id"] == name)
        survey = branch["steps"][0]
        entry = survey_points(survey)[0]
        lane, height = -15 + i * 10, 44 + i * 2
        transit = [
            fly(state["north_m"], lane, height, 10),
            fly(-790, lane, height, 15),
            fly(entry["north_m"], entry["east_m"], height, 15),
        ]
        branch["steps"] = [
            {"kind": "takeoff", "height_m": 40},
            *transit,
            survey,
            *reversed(transit),
            {"kind": "return_home", "height_m": height, "speed_mps": 5},
        ]
    return value


class CompleteSearchFlights(unittest.TestCase):
    def test_current_port_can_launch_four_observed_surveys_and_return(self):
        fleet = states()
        value = search_plan()
        value["search"]["perimeter"] = rectangle(-710, -650, -70, 30)
        value["drones"] = divide_survey(
            ["Drone3", "Drone1", "Drone2", "Drone4"], value["search"]["perimeter"], 20, 0, 30, 5
        )["drones"]
        for index, (branch, lane) in enumerate(zip(value["drones"], (-65, -40, -15, 10))):
            name = branch["drone_id"]
            state = next(state for state in fleet if state["id"] == name)
            survey = branch["steps"][0]
            entry = survey_points(survey)[0]
            height = 40 + index * 2
            transit = [
                fly(state["north_m"], lane, height, 5),
                fly(entry["north_m"], entry["east_m"], height, 5),
            ]
            branch["steps"] = [
                {"kind": "takeoff", "height_m": 30},
                *transit,
                survey,
                *reversed(transit),
                {"kind": "return_home", "height_m": height, "speed_mps": 5},
            ]
        receipt = compile_plan(value, fleet, {name: readback() for name in NAMES}, load())
        self.assertEqual(receipt["plan"], value)
        self.assertEqual(receipt["coverage"]["survey_percent"], 100)
        self.assertEqual(set(receipt["missions"]), set(NAMES))
        for mission in receipt["missions"].values():
            self.assertEqual(mission["items"][-1]["command"], MAV.MAV_CMD_NAV_LAND)

    def test_four_grounded_drones_search_and_return_across_the_map(self):
        fleet = recorded_pad()
        fences, landmarks = {name: readback() for name in NAMES}, load()
        for bounds, spacing, zoom in (
            ((-400, 500, -100, 800), 45, 1),
            ((-750, -650, -100, 100), 20, 2),
            ((500, 700, 200, 400), 20, 2),
            ((-100, 100, -600, -400), 20, 2),
        ):
            for bearing in (0, 90):
                with self.subTest(bounds=bounds, bearing=bearing):
                    value = complete_search(fleet, bounds, spacing, bearing)
                    value["search"]["zoom"] = zoom
                    receipt = compile_plan(value, fleet, fences, landmarks)
                    self.assertEqual(receipt["plan"], value)
                    self.assertEqual(receipt["coverage"]["survey_percent"], 100)
                    self.assertEqual(set(receipt["missions"]), set(NAMES))
                    for branch in value["drones"]:
                        mission = receipt["missions"][branch["drone_id"]]
                        state = next(s for s in fleet if s["id"] == branch["drone_id"])
                        self.assertEqual(mission["items"][1]["command"], MAV.MAV_CMD_NAV_TAKEOFF)
                        self.assertEqual(mission["items"][-1]["command"], MAV.MAV_CMD_NAV_LAND)
                        self.assertAlmostEqual(
                            mission["route"][-1]["north_m"], state["home"]["north_m"]
                        )
                        self.assertAlmostEqual(
                            mission["route"][-1]["east_m"], state["home"]["east_m"]
                        )
                        self.assertLessEqual(mission["battery_required_percent"], 100)
                        self.assertGreater(mission["duration_s"], 0)
                        perimeter = value["search"]["perimeter"]
                        self.assertFalse(point_in_polygon(mission["route"][1], perimeter))
                        self.assertFalse(point_in_polygon(mission["route"][-2], perimeter))
                        survey = branch["steps"][4]
                        for point in survey_points(survey):
                            self.assertTrue(point_in_polygon(point, survey["polygon"]))

    def test_recorded_crossed_departure_rejects_despite_separate_destinations(self):
        fleet = recorded_pad()
        value = {
            "version": 1,
            "drones": [
                {
                    "drone_id": name,
                    "steps": [
                        {"kind": "takeoff", "height_m": 40},
                        fly(-790, lane, 40, 10),
                        {"kind": "hover", "seconds": None},
                    ],
                }
                for name, lane in (("Drone2", -5), ("Drone3", 5))
            ],
        }
        with self.assertRaisesRegex(PlanRejected, "conflicts with Drone3") as caught:
            compile_plan(value, fleet, {name: readback() for name in NAMES}, load())
        conflict = caught.exception.rejection["conflict"]
        self.assertEqual((conflict["drone_id"], conflict["other_drone_id"]), ("Drone2", "Drone3"))
        self.assertEqual((conflict["step_index"], conflict["other_step_index"]), (1, 1))
        self.assertEqual((conflict["step_kind"], conflict["other_step_kind"]), ("fly", "fly"))
        self.assertLess(conflict["distance_m"], conflict["minimum_m"])
        self.assertEqual(conflict["minimum_m"], 0.1)


if __name__ == "__main__":
    unittest.main()

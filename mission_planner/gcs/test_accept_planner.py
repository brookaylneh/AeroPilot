"""Live operator fixtures compile offline and telemetry measurements preserve failures."""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mission_planner.gcs.accept_planner import itinerary, measure, run
from mission_planner.gcs.geodesy import NAMES, ORIGIN, fence_vertices
from mission_planner.gcs.planner import compile_plan
from mission_planner.gcs.test_planner import airborne, states
from mission_planner.gcs.test_validation import readback
from world.cosys.landmarks import load
from world.cosys.terrain import terrain_height


class LivePlannerAcceptance(unittest.TestCase):
    def test_operator_itinerary_compiles_with_changes_partway_along_legs(self):
        fleet = states()
        receipts = []
        for plan in itinerary():
            receipt = compile_plan(plan, fleet, {name: readback() for name in NAMES}, load())
            receipts.append(receipt)
            for name, mission in receipt["missions"].items():
                state = next(v for v in fleet if v["id"] == name)
                endpoint = mission["route"][-1]
                if plan["version"] in (2, 6) and name == (
                    "Drone1" if plan["version"] == 2 else "Drone2"
                ):
                    endpoint = {
                        key: (state[key] + endpoint[key]) / 2 for key in ("north_m", "east_m")
                    }
                    endpoint["altitude_above_home_m"] = (
                        terrain_height(endpoint["north_m"], endpoint["east_m"])
                        + 40
                        - (state["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"])
                    )
                state.update(endpoint, armed=True, landed_state=2)
        self.assertEqual(len(receipts), 7)
        self.assertEqual(set(receipts[4]["missions"]), {"Drone1", "Drone2"})
        self.assertTrue(
            all(
                mission["items"][-1]["command"] == 21
                for mission in receipts[-1]["missions"].values()
            )
        )

    def test_telemetry_reducer_measures_clearance_and_swap_time(self):
        fleet = airborne(-700, 0)
        for vehicle in fleet:
            vehicle.update(
                mode="STABILIZE",
                mission=[{"seq": 1, "command": 17}],
                mission_current=1,
                fence=fence_vertices(),
            )
        fleet[0]["mode"] = "BRAKE"
        second = copy.deepcopy(fleet)
        second[0].update(mode="AUTO", altitude_above_home_m=fleet[0]["altitude_above_home_m"] - 5)
        samples = [
            {
                "at_ms": at,
                "phase": "change",
                "applying_version": 1,
                "plan_version": 1,
                "vehicles": value,
            }
            for at, value in ((1000, fleet), (2000, second))
        ]
        receipt = {1: {"missions": {"Drone1": {"route": [fleet[0], second[0]]}}}}
        changes = {
            1: {"drones": ["Drone1"], "submitted_at_ms": 500, "uploads_ms": {"Drone1": 1500}}
        }
        value = measure(samples, receipt, changes)
        self.assertAlmostEqual(value["minimum_cruise_clearance_m"], 35)
        self.assertAlmostEqual(value["minimum_separation_m"], 5)
        self.assertEqual(value["swaps"][1]["Drone1"]["hover_s"], 1)
        self.assertEqual(value["swaps"][1]["Drone1"]["change_to_upload_s"], 1)
        self.assertEqual(value["maximum_route_deviation_m"]["Drone1"], 0)
        self.assertEqual(value["fence_violations"], [])

    def test_unavailable_world_is_recorded_without_connecting(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "new"
            runtime = {
                "SNR_HOST": "127.0.0.1",
                "SNR_MISSION_PORT": "8765",
                "SNR_SITL_MODEL": "airsim-copter",
                "SNR_DRONE_DATA_DIR": str(Path(temporary) / "missing"),
            }
            with (
                patch("mission_planner.gcs.accept_planner.load_env"),
                patch("mission_planner.gcs.accept_planner.dotenv_values", return_value=runtime),
                patch("mission_planner.gcs.accept_planner.connect") as connection,
            ):
                with self.assertRaises(FileNotFoundError):
                    asyncio.run(run("saved-case", output))
            result = json.loads((output / "result.json").read_text())
            self.assertFalse(result["passed"])
            self.assertIn("FileNotFoundError", result["failure"])
            connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()

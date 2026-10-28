"""Re-look preserves AUTO state and excludes paused routes from completion only."""

import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.gcs.geodesy import ORIGIN
from mission_planner.search.execution import finish
from mission_planner.search.relook import relook, resume_search
from world.cosys.terrain import terrain_height


class Relook(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {
            "id": "Drone1",
            "north_m": -650.0,
            "east_m": 0.0,
            "altitude_above_home_m": 30.0,
            "armed": True,
            "landed_state": 2,
            "mode": "AUTO",
            "mission_current": 2,
            "mission_total": 4,
            "link_lost": False,
            "ekf_healthy": True,
            "ground_speed_mps": 0.0,
            "climb_rate_mps": 0.0,
            "home": {"altitude_msl_m": ORIGIN["altitude_msl_m"] + terrain_height(-650, 0)},
        }
        self.mission_items = [{"seq": seq, "command": 16, "z": 30.0} for seq in range(4)]

        async def execute(name, arguments):
            if name == "hold":
                self.state["mode"] = "BRAKE"
            elif name == "fly_to":
                self.state.update(
                    {key: arguments[key] for key in ("north_m", "east_m", "altitude_above_home_m")}
                )
                self.state["mode"] = "GUIDED"
            elif name == "resume":
                self.state["mode"] = "AUTO"
            else:
                raise AssertionError("Unexpected flight command: " + name)
            return copy.deepcopy(self.state)

        self.fleet = SimpleNamespace(
            execute=AsyncMock(side_effect=execute),
            _validate_route=Mock(),
            _check_tasks=Mock(),
            vehicles={"Drone1": {"mission": self.mission_items}},
        )
        mission = SimpleNamespace(fleet=self.fleet, vehicles=lambda: [copy.deepcopy(self.state)])
        cluster = {
            "status": "pending",
            "localization": {"ground": {"north_m": -640.0, "east_m": 5.0}},
        }
        self.search = SimpleNamespace(
            status="running",
            paused={},
            changed=Mock(),
            mission=mission,
            plan={"routes": {"Drone1": {"reserve_seconds": 300}}},
            commander=SimpleNamespace(
                source="live", ledger=SimpleNamespace(cluster=Mock(return_value=cluster))
            ),
        )

    async def look(self, altitude=30):
        with patch("mission_planner.search.relook.asyncio.sleep", new_callable=AsyncMock):
            return await relook(self.search, "detection", "Drone1", altitude, 1)

    async def test_guided_look_and_resume_preserve_auto_mission_and_current(self):
        original = copy.deepcopy(self.mission_items)
        current = self.state["mission_current"]
        returned = await self.look()
        saved = self.search.paused["Drone1"]
        self.assertEqual(saved["status"], "awaiting_resume")
        self.assertEqual(saved["mission"], original)
        self.assertEqual(saved["mission_current"], current)
        self.assertEqual(returned["perception_credit"], "calibrated live frames only")
        self.fleet._validate_route.assert_called_once()
        result = await resume_search(self.search, "Drone1")
        self.assertEqual(result["mode"], "AUTO")
        self.assertEqual(result["mission_current"], current)
        self.assertEqual(self.mission_items, original)
        self.assertFalse(self.search.paused)
        self.assertNotIn("fly_route", [call.args[0] for call in self.fleet.execute.call_args_list])

    async def test_lower_look_descends_only_at_cluster_and_is_visual_confirmation(self):
        result = await self.look(20)
        route = self.search.paused["Drone1"]["route"]
        self.assertEqual(route[-1]["north_m"], route[-2]["north_m"])
        self.assertEqual(route[-1]["east_m"], route[-2]["east_m"])
        self.assertEqual(
            route[-2]["altitude_above_home_m"] - route[-1]["altitude_above_home_m"], 10
        )
        self.assertEqual(result["perception_credit"], "operator visual confirmation only")

    async def test_changed_mission_current_blocks_auto_resume(self):
        await self.look()
        self.state["mission_current"] += 1
        self.fleet.execute.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "Search resume failed"):
            await resume_search(self.search, "Drone1")
        self.assertNotIn("resume", [call.args[0] for call in self.fleet.execute.call_args_list])
        self.assertEqual(self.search.paused["Drone1"]["status"], "failed")

    async def test_rejected_return_leg_marks_failed_for_explicit_operator_recovery(self):
        await self.look()
        self.fleet.execute.side_effect = RuntimeError("Return route rejected")
        with self.assertRaisesRegex(RuntimeError, "Search resume failed: Return route rejected"):
            await resume_search(self.search, "Drone1")
        self.assertEqual(self.search.paused["Drone1"]["status"], "failed")

    async def test_stopping_auto_snapshots_advanced_current_and_stop_position(self):
        original_execute = self.fleet.execute.side_effect

        async def advance_while_braking(name, arguments):
            if name == "hold" and self.state["mode"] == "AUTO":
                self.state["mission_current"] += 1
                self.state["north_m"] += 1
            return await original_execute(name, arguments)

        self.fleet.execute.side_effect = advance_while_braking
        await self.look()
        saved = self.search.paused["Drone1"]
        self.assertEqual(saved["mission_current"], 3)
        self.assertEqual(saved["return_point"]["north_m"], -649)
        await resume_search(self.search, "Drone1")
        self.assertEqual(self.state["mission_current"], 3)

    async def test_same_horizontal_position_supports_hold_and_vertical_look(self):
        cluster = self.search.commander.ledger.cluster.return_value
        cluster["localization"]["ground"] = {"north_m": -650.0, "east_m": 0.0}
        await self.look(20)
        route = self.search.paused["Drone1"]["route"]
        self.assertEqual(len(route), 2)
        self.assertEqual([p["north_m"] for p in route], [-650.0, -650.0])
        self.assertEqual([p["altitude_above_home_m"] for p in route], [30.0, 20.0])

    async def test_vertical_only_leg_reports_actual_rounded_clearance_error(self):
        cluster = self.search.commander.ledger.cluster.return_value
        cluster["localization"]["ground"] = {"north_m": -650.0, "east_m": 0.0}
        self.state["home"]["altitude_msl_m"] += 0.07
        await self.look()
        self.assertAlmostEqual(self.search.paused["Drone1"]["planned_agl_error_m"], 0.07, places=8)

    async def test_relook_failure_does_not_replace_auto_mission(self):
        original = copy.deepcopy(self.mission_items)
        self.fleet.execute.side_effect = RuntimeError("Flight command rejected")
        with self.assertRaisesRegex(RuntimeError, "Flight command rejected"):
            await self.look()
        self.assertEqual(self.mission_items, original)
        self.assertIn("Drone1", self.search.paused)
        self.assertEqual(self.search.paused["Drone1"]["status"], "failed")


class PausedMonitor(unittest.IsolatedAsyncioTestCase):
    async def test_paused_route_still_checks_telemetry_health(self):
        fleet = SimpleNamespace(execute=AsyncMock(), _check_tasks=Mock())
        mission = SimpleNamespace(
            fleet=fleet, vehicles=lambda: [{"id": "Drone1", "link_lost": True, "ekf_healthy": True}]
        )
        search = SimpleNamespace(
            plan={"routes": {"Drone1": {}}}, paused={"Drone1": {"kind": "relook"}}, mission=mission
        )
        with self.assertRaisesRegex(AssertionError, "telemetry unhealthy"):
            await finish(search, time.monotonic())
        fleet.execute.assert_not_awaited()

    async def test_paused_route_at_last_waypoint_does_not_return_until_resumed(self):
        states = {
            name: {
                "id": name,
                "north_m": float(index * 20),
                "east_m": 0.0,
                "altitude_above_home_m": 30.0,
                "mission_current": 2,
                "mission_total": 3,
                "link_lost": False,
                "ekf_healthy": True,
                "armed": True,
                "landed_state": 2,
            }
            for index, name in enumerate(("Drone1", "Drone2"))
        }

        async def execute(name, arguments):
            self.assertEqual(name, "return_home")
            states[arguments["drone_id"]].update(armed=False, landed_state=1)

        fleet = SimpleNamespace(execute=AsyncMock(side_effect=execute), _check_tasks=Mock())
        mission = SimpleNamespace(
            fleet=fleet, vehicles=lambda: list(states.values()), event=Mock(), active_flight=set()
        )
        search = SimpleNamespace(
            plan={
                "routes": {
                    name: {"waypoints": [copy.deepcopy(state)]} for name, state in states.items()
                }
            },
            paused={"Drone1": {"kind": "relook"}},
            mission=mission,
            changed=Mock(),
            status="running",
            coverage_summary=lambda: {"message": "Recorded frames"},
        )

        async def tick(seconds):
            if "Drone1" in search.paused:
                self.assertEqual(
                    fleet.execute.call_args_list[0].args, ("return_home", {"drone_id": "Drone2"})
                )
                self.assertEqual(fleet.execute.call_count, 1)
                del search.paused["Drone1"]

        with patch("mission_planner.search.execution.asyncio.sleep", side_effect=tick):
            await finish(search, time.monotonic())
        self.assertEqual(search.status, "complete")
        self.assertEqual(
            [call.args[1]["drone_id"] for call in fleet.execute.call_args_list],
            ["Drone2", "Drone1"],
        )

    async def test_monitor_skips_in_progress_command_and_claims_automatic_return(self):
        state = {
            "id": "Drone1",
            "north_m": 0.0,
            "east_m": 0.0,
            "altitude_above_home_m": 30.0,
            "mission_current": 2,
            "mission_total": 3,
            "link_lost": False,
            "ekf_healthy": True,
            "armed": True,
            "landed_state": 2,
        }
        mission = SimpleNamespace(active_flight={"Drone1"}, vehicles=lambda: [state], event=Mock())

        async def execute(name, arguments):
            self.assertEqual(mission.active_flight, {"Drone1"})
            state.update(armed=False, landed_state=1)

        mission.fleet = SimpleNamespace(execute=AsyncMock(side_effect=execute), _check_tasks=Mock())
        search = SimpleNamespace(
            plan={"routes": {"Drone1": {"waypoints": [copy.deepcopy(state)]}}},
            paused={},
            mission=mission,
            changed=Mock(),
            status="running",
            coverage_summary=lambda: {"message": "Recorded frames"},
        )

        async def tick(seconds):
            if not mission.fleet.execute.call_count:
                mission.fleet.execute.assert_not_awaited()
                mission.active_flight.clear()

        with patch("mission_planner.search.execution.asyncio.sleep", side_effect=tick):
            await finish(search, time.monotonic())
        mission.fleet.execute.assert_awaited_once_with("return_home", {"drone_id": "Drone1"})
        self.assertFalse(mission.active_flight)


if __name__ == "__main__":
    unittest.main()

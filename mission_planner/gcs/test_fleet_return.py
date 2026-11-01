"""Remaining reservations and explicit coordinated returns, without live commands."""

import asyncio
import copy
import unittest
from unittest.mock import AsyncMock

from mission_planner.gcs.fleet import remaining_route
from mission_planner.gcs.geodesy import NAMES, rtl_route
from mission_planner.gcs.test_plan_execution import mission
from mission_planner.gcs.test_planner import states
from mission_planner.gcs.test_validation import HOME_HEIGHTS
from mission_planner.gcs.validation import RouteConflict, validate_separation


def returning_fleet():
    fleet = states()
    positions = ((259.9, 192.5), (-316.067, 372.497), (467.3, -77.5), (207.537, 687.473))
    for state, (north, east) in zip(fleet, positions):
        state.update(
            north_m=north,
            east_m=east,
            altitude_above_home_m=40,
            armed=True,
            landed_state=2,
            mode="BRAKE",
        )
    return fleet


class RemainingReservations(unittest.TestCase):
    def test_auto_discards_completed_legs_using_mission_sequence(self):
        state = {
            **returning_fleet()[0],
            "mode": "AUTO",
            "mission_current": 3,
            "mission": [
                {"seq": seq, "north_m": seq * 10, "east_m": 0, "altitude_above_home_m": 40}
                for seq in range(1, 5)
            ],
        }
        route = remaining_route(state, [])
        self.assertEqual(route, [state, *state["mission"][2:]])

    def test_rtl_keeps_future_climb_and_removes_it_during_descent(self):
        start = {**returning_fleet()[1], "mode": "RTL"}
        route = rtl_route(start)
        climbing = {**start, "altitude_above_home_m": 60}
        self.assertEqual(remaining_route(climbing, route), [climbing, *route])
        descending = {**start, **start["home"], "altitude_above_home_m": 30, "landed_state": 4}
        self.assertEqual(remaining_route(descending, route), [descending, route[-1]])
        landed = {**descending, "armed": False, "landed_state": 1}
        self.assertEqual(remaining_route(landed, route), [landed])

    def test_near_home_drift_cannot_be_mistaken_for_final_descent(self):
        start = {
            **returning_fleet()[0],
            "north_m": 1,
            "east_m": 0,
            "mode": "RTL",
        }
        start["home"] = {**start["home"], "north_m": 0, "east_m": 0}
        route = rtl_route(start)
        drifting = {**start, "north_m": 0.4, "altitude_above_home_m": 45}
        remaining = remaining_route(drifting, route)
        climb_point = {"north_m": 1, "east_m": 0, "altitude_above_home_m": 60}
        with self.assertRaises(RouteConflict):
            validate_separation("Drone4", [climb_point], [("Drone1", remaining)], HOME_HEIGHTS)
        self.assertEqual(remaining, [drifting, *route])

    def test_recorded_crossing_clears_before_the_other_drone_lands(self):
        fleet = returning_fleet()
        two, four = fleet[1], fleet[3]
        route = rtl_route(copy.deepcopy(two))
        with self.assertRaises(RouteConflict):
            validate_separation("Drone4", rtl_route(four), [("Drone2", route)], HOME_HEIGHTS)
        two.update(
            mode="RTL",
            north_m=two["home"]["north_m"],
            east_m=two["home"]["east_m"],
            altitude_above_home_m=70,
        )
        with self.assertRaises(RouteConflict):
            validate_separation(
                "Drone4", rtl_route(four), [("Drone2", remaining_route(two, route))], HOME_HEIGHTS
            )
        two["landed_state"] = 4
        remaining = remaining_route(two, route)
        validate_separation("Drone4", rtl_route(four), [("Drone2", remaining)], HOME_HEIGHTS)
        self.assertEqual(len(remaining), 2)
        self.assertTrue(two["armed"])


class CoordinatedReturn(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.current = returning_fleet()
        self.owner = mission(self.current)
        for state in self.current:
            state["mode"] = "BRAKE"
            self.owner.fleet.vehicles[state["id"]]["mode"] = "BRAKE"

    async def asyncTearDown(self):
        await self.owner.cancel_returns(NAMES, "Test complete")
        if self.owner.tasks:
            await asyncio.gather(*self.owner.tasks, return_exceptions=True)

    async def wait_for(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait(), 2)

    async def queue(self):
        result = await self.owner.execute(
            "return_fleet", {"drone_ids": list(NAMES)}, "agent", "return"
        )
        self.assertEqual(len(result["returns"]), 4)
        await self.wait_for(lambda: self.owner.return_rows["Drone4"]["waiting_for"] == "Drone2")
        self.assertEqual(
            [self.owner.return_rows[n]["status"] for n in NAMES],
            ["returning", "returning", "returning", "waiting"],
        )

    def clear_crossing(self):
        two = self.current[1]
        two.update(
            north_m=two["home"]["north_m"],
            east_m=two["home"]["east_m"],
            altitude_above_home_m=70,
            landed_state=4,
        )

    def rtl_calls(self, name):
        return [
            call for call in self.owner.fleet._mode.await_args_list if call.args == (name, "RTL")
        ]

    async def test_duplicate_drone_ids_fail_before_any_flight_or_inspection_action(self):
        with self.assertRaisesRegex(AssertionError, "drone_ids must contain distinct known drones"):
            await self.owner.execute(
                "return_fleet", {"drone_ids": ["Drone1", "Drone1"]}, "agent", "duplicate"
            )
        self.owner.fleet._mode.assert_not_awaited()
        self.owner.inspections.invalidate.assert_not_called()
        self.assertFalse(self.owner.return_rows)
        self.assertFalse(self.owner.return_tasks)

    async def test_group_landing_reports_once_without_claiming_the_other_drone_is_tracking(self):
        names = ["Drone1", "Drone3", "Drone4"]
        await self.owner.return_fleet(names, "agent")
        tasks = list(self.owner.return_tasks.values())
        for state in self.current:
            if state["id"] in names:
                state.update(armed=False, landed_state=1, **state["home"])
        await asyncio.gather(*tasks)
        messages = [
            call.args[1] for call in self.owner.event.call_args_list if call.args[0] == "agent"
        ]
        self.assertEqual(
            messages,
            ["Drone1, Drone3, Drone4 landed and disarmed. Still airborne: Drone2."],
        )
        self.assertFalse(self.owner.return_tasks)

    async def test_three_return_one_waits_then_starts_once_without_another_model_turn(self):
        await self.queue()
        self.assertFalse(self.rtl_calls("Drone4"))
        self.assertTrue(
            any(
                "holding until Drone2 clears" in call.args[1]
                for call in self.owner.event.call_args_list
            )
        )
        self.clear_crossing()
        await self.wait_for(lambda: self.owner.return_rows["Drone4"]["status"] == "returning")
        self.assertTrue(self.current[1]["armed"])
        for name in NAMES:
            self.assertEqual(len(self.rtl_calls(name)), 1)
        for state in self.current:
            state.update(armed=False, landed_state=1)
        await self.wait_for(lambda: not self.owner.return_tasks)
        self.assertTrue(all(row["status"] == "landed" for row in self.owner.return_rows.values()))

    async def test_override_cancels_waiting_return_without_a_late_rtl(self):
        await self.queue()
        await self.owner.execute("hold", {"drone_id": "Drone4"}, "operator", "hold")
        self.clear_crossing()
        await asyncio.sleep(0.3)
        self.assertEqual(self.owner.return_rows["Drone4"]["status"], "stopped")
        self.assertEqual(self.current[3]["mode"], "BRAKE")
        self.assertFalse(self.rtl_calls("Drone4"))

    async def test_plan_swap_cancels_waiting_return_without_a_late_rtl(self):
        await self.queue()
        self.owner.fleet_event(
            {"kind": "plan_swap", "stage": "holding", "version": 2, "drone_ids": ["Drone4"]}
        )
        self.clear_crossing()
        await asyncio.sleep(0.3)
        self.assertEqual(self.owner.return_rows["Drone4"]["status"], "stopped")
        self.assertNotIn("Drone4", self.owner.return_tasks)
        self.assertFalse(self.rtl_calls("Drone4"))

    async def test_plan_application_revokes_returns_before_braking(self):
        await self.queue()
        braking = asyncio.Event()

        async def mode(name, mode):
            self.assertEqual((name, mode), ("Drone1", "BRAKE"))
            self.assertEqual(self.owner.return_rows[name]["status"], "stopped")
            braking.set()
            await asyncio.Future()

        self.owner.fleet._mode.side_effect = mode
        self.owner.fleet.preview_plan = AsyncMock(return_value={"missions": {"Drone1": {}}})
        applying = asyncio.create_task(self.owner.fleet.apply_plan({"version": 2}))
        try:
            await asyncio.wait_for(braking.wait(), 1)
            await asyncio.sleep(0)
            self.assertNotIn("Drone1", self.owner.return_tasks)
            self.assertFalse(self.owner.return_rows["Drone1"]["pending"])
            self.assertEqual(self.owner.return_rows["Drone4"]["status"], "waiting")
        finally:
            applying.cancel()
            await asyncio.gather(applying, return_exceptions=True)

    async def test_override_cancels_return_during_inspection_cleanup(self):
        entered = asyncio.Event()

        async def cleanup(name):
            if name == "Drone1":
                entered.set()
                await asyncio.Future()

        self.owner.inspections.flight_stopped.side_effect = cleanup
        operation = asyncio.create_task(
            self.owner.invoke("return_fleet", {"drone_ids": list(NAMES)}, "agent", "return")
        )
        await asyncio.wait_for(entered.wait(), 1)
        await self.owner.execute("hold", {"drone_id": "Drone4"}, "operator", "hold")
        result = await asyncio.wait_for(operation, 1)
        self.assertFalse(result["ok"])
        self.assertIn("override cancelled", result["error"])
        self.assertIsNone(self.owner.plan_task)
        self.assertFalse(self.owner.return_tasks)
        self.assertFalse(any(self.rtl_calls(name) for name in NAMES))

    async def test_static_blocker_fails_instead_of_waiting_forever(self):
        four = self.current[3]
        route = rtl_route(four)
        self.current[1].update(
            north_m=(route[1]["north_m"] + route[2]["north_m"]) / 2,
            east_m=(route[1]["east_m"] + route[2]["east_m"]) / 2,
            altitude_above_home_m=80,
        )
        await self.owner.return_fleet(["Drone4"], "agent")
        await self.wait_for(lambda: self.owner.return_rows["Drone4"]["status"] == "failed")
        self.assertIn("conflicts with Drone2", self.owner.return_rows["Drone4"]["error"])
        self.assertFalse(self.rtl_calls("Drone4"))

    async def test_static_blocker_is_not_hidden_by_an_earlier_returning_drone(self):
        route = rtl_route(self.current[3])
        self.current[0].update(
            north_m=route[1]["north_m"],
            east_m=route[1]["east_m"],
            altitude_above_home_m=80,
            mode="RTL",
        )
        self.owner.fleet.planned_routes["Drone1"] = [copy.deepcopy(self.current[0]), route[2]]
        self.current[1].update(
            north_m=(route[1]["north_m"] + route[2]["north_m"]) / 2,
            east_m=(route[1]["east_m"] + route[2]["east_m"]) / 2,
            altitude_above_home_m=80,
        )
        with self.assertRaisesRegex(RouteConflict, "conflicts with Drone2"):
            await self.owner.fleet.return_step("Drone4")
        self.assertFalse(self.rtl_calls("Drone4"))

    async def test_command_failure_stops_pending_returns_without_retry(self):
        original = self.owner.fleet._mode.side_effect

        async def mode(name, mode):
            if name == "Drone2" and mode == "RTL":
                raise TimeoutError("Drone2 RTL acknowledgement timed out")
            await original(name, mode)

        self.owner.fleet._mode.side_effect = mode
        await self.owner.return_fleet(list(NAMES), "agent")
        await self.wait_for(lambda: self.owner.return_rows["Drone2"]["status"] == "failed")
        await self.wait_for(
            lambda: all(
                self.owner.return_rows[n]["status"] == "stopped" for n in ("Drone3", "Drone4")
            )
        )
        self.assertEqual(len(self.rtl_calls("Drone2")), 1)
        self.assertFalse(self.rtl_calls("Drone3"))
        self.assertFalse(self.rtl_calls("Drone4"))

    async def test_failed_return_wakes_wonder_once_even_if_landing_precedes_review(self):
        self.owner.fleet.return_step = AsyncMock(side_effect=TimeoutError("RTL timed out"))
        await self.owner.return_fleet(["Drone4"], "agent")
        await self.wait_for(lambda: self.owner.return_rows["Drone4"]["status"] == "failed")
        self.owner.agent = AsyncMock()
        self.owner.agent_busy = True
        receipt = {"missions": {"Drone4": {}}}
        self.owner.plan_active = receipt
        for state in self.current:
            state.update(armed=False, landed_state=1)
        watcher = asyncio.create_task(self.owner.watch_plan(receipt))
        await asyncio.sleep(0)
        self.assertFalse(watcher.done())
        self.owner.agent.assert_not_awaited()
        self.owner.agent_busy = False
        await asyncio.wait_for(watcher, 1)
        await asyncio.sleep(0)
        self.owner.agent.assert_awaited_once()
        wake = self.owner.agent.await_args.kwargs["wake"]
        self.assertEqual(wake["reason"], "return")
        self.assertEqual(wake["returns"][0]["error"], "RTL timed out")
        self.assertFalse(self.owner.return_rows["Drone4"]["pending"])
        await self.owner.watch_plan(receipt)
        self.owner.agent.assert_awaited_once()

    async def test_existing_rtl_descent_is_not_restarted(self):
        state = self.current[0]
        self.owner.fleet.planned_routes["Drone1"] = rtl_route(copy.deepcopy(state))
        state.update(
            mode="RTL",
            north_m=state["home"]["north_m"],
            east_m=state["home"]["east_m"],
            altitude_above_home_m=20,
        )
        await self.owner.return_fleet(["Drone1"], "agent")
        await asyncio.sleep(0.01)
        self.assertEqual(self.owner.return_rows["Drone1"]["status"], "returning")
        self.owner.fleet._mode.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

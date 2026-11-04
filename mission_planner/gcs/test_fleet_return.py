"""Remaining reservations and explicit coordinated returns, without live commands."""

import asyncio
import copy
import unittest
from unittest.mock import AsyncMock

from mission_planner.gcs.fleet import remaining_route
from mission_planner.gcs.geodesy import NAMES, rtl_route
from mission_planner.gcs.planner import mission_point, mission_readback
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.returning import approach
from mission_planner.gcs.test_plan_execution import mission
from mission_planner.gcs.test_planner import states
from mission_planner.gcs.test_validation import HOME_HEIGHTS
from mission_planner.gcs.validation import validate_separation


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
        with self.assertRaises(PlanRejected):
            validate_separation("Drone4", [climb_point], [("Drone1", remaining)], HOME_HEIGHTS)
        self.assertEqual(remaining, [drifting, *route])

    def test_recorded_crossing_clears_before_the_other_drone_lands(self):
        fleet = returning_fleet()
        two, four = fleet[1], fleet[3]
        route = rtl_route(copy.deepcopy(two))
        with self.assertRaises(PlanRejected):
            validate_separation("Drone4", rtl_route(four), [("Drone2", route)], HOME_HEIGHTS)
        two.update(
            mode="RTL",
            north_m=two["home"]["north_m"],
            east_m=two["home"]["east_m"],
            altitude_above_home_m=70,
        )
        with self.assertRaises(PlanRejected):
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
            state["climb_rate_mps"] = 0
            self.owner.fleet.vehicles[state["id"]]["mode"] = "BRAKE"
        self.uploads = {}

        async def upload(name, items, kind):
            self.uploads[name] = copy.deepcopy(items)

        async def download(name, kind):
            items = self.uploads[name]
            state = next(s for s in self.current if s["id"] == name)
            state["mission_total"] = len(items)
            state["mission"] = [
                {"seq": i, "command": item["command"], **mission_point(item)}
                for i, item in enumerate(items)
                if i > 0
            ]
            state["mission_current"] = 1
            return [mission_readback(item, home=i == 0) for i, item in enumerate(items)]

        self.owner.fleet._upload.side_effect = upload
        self.owner.fleet._download.side_effect = download

    async def asyncTearDown(self):
        await self.owner.cancel_returns(NAMES, "Test complete")
        if self.owner.tasks:
            await asyncio.gather(*self.owner.tasks, return_exceptions=True)

    async def wait_for(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait(), 2)

    async def queue(self, names=NAMES):
        result = await self.owner.execute(
            "return_fleet", {"drone_ids": list(names)}, "agent", "return"
        )
        self.assertEqual(len(result["returns"]), len(names))
        await self.wait_for(
            lambda: all(self.owner.return_rows[name]["status"] == "climbing" for name in names)
        )
        self.assertTrue(all(s["mode"] == "AUTO" for s in self.current if s["id"] in names))

    def arrive(self, index):
        state = self.current[index]
        state.update(
            **mission_point(self.uploads[state["id"]][-1]),
            mission_current=2,
            ground_speed_mps=0,
            climb_rate_mps=0,
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

    async def test_all_approaches_start_then_land_without_another_model_turn(
        self,
    ):
        await self.queue()
        routes = [(name, self.owner.fleet.planned_routes[name]) for name in NAMES]
        for name, route in routes:
            validate_separation(name, route, routes, HOME_HEIGHTS)
        for index in range(4):
            self.arrive(index)
        await self.wait_for(
            lambda: all(self.owner.return_rows[name]["status"] == "returning" for name in NAMES)
        )
        for name in NAMES:
            self.assertEqual(len(self.rtl_calls(name)), 1)
        for state in self.current:
            state.update(armed=False, landed_state=1)
        await self.wait_for(lambda: not self.owner.return_tasks)
        self.assertTrue(all(row["status"] == "landed" for row in self.owner.return_rows.values()))

    async def test_recorded_locked_drone_departs_with_the_first_return_request(self):
        for state in self.current:
            if state["id"] not in ("Drone1", "Drone3"):
                state.update(**state["home"], altitude_above_home_m=0, armed=False, landed_state=1)
        self.current[0].update(north_m=-4.972537, east_m=-101.475711, altitude_above_home_m=26.065)
        self.current[2].update(north_m=13.863168, east_m=-127.658450, altitude_above_home_m=26.024)
        await self.queue(("Drone1", "Drone3"))
        self.owner.inspections.invalidate.assert_any_call("Coordinated fleet return", "Drone3")
        self.owner.inspections.flight_stopped.assert_any_await("Drone3")
        self.assertIsNone(self.owner.return_rows["Drone3"]["waiting_for"])
        self.assertFalse(self.rtl_calls("Drone1"))
        self.assertFalse(self.rtl_calls("Drone3"))

    async def test_repeat_group_and_individual_return_preserve_tasks_and_mission(self):
        await self.queue()
        tasks = dict(self.owner.return_tasks)
        modes = list(self.owner.fleet._mode.await_args_list)
        uploads = self.owner.fleet._upload.await_count
        repeated = await self.owner.return_fleet(list(NAMES), "agent")
        single = await self.owner.execute("return_home", {"drone_id": "Drone3"}, "agent", "repeat")
        self.assertEqual(single["return"]["status"], "climbing")
        self.assertEqual(single["mode"], "AUTO")
        self.assertTrue(all(r["status"] == "climbing" for r in repeated["returns"]))
        self.assertEqual(self.owner.return_tasks, tasks)
        self.assertEqual(self.owner.fleet._mode.await_args_list, modes)
        self.assertEqual(self.owner.fleet._upload.await_count, uploads)

    async def test_bad_readback_prevents_every_departure_without_retry(self):
        self.owner.fleet._download.side_effect = None
        self.owner.fleet._download.return_value = []
        with self.assertRaisesRegex(AssertionError, "mission readback count mismatch"):
            await self.owner.return_fleet(list(NAMES), "agent")
        self.assertEqual(self.owner.fleet._download.await_count, 1)
        self.assertFalse(self.owner.return_tasks)
        self.assertFalse(
            any(call.args[1] == "AUTO" for call in self.owner.fleet._mode.await_args_list)
        )

    async def test_replaced_approach_cannot_issue_a_late_landing(self):
        await self.queue()
        self.owner.fleet.control_versions["Drone3"] += 1
        self.arrive(2)
        await self.wait_for(lambda: self.owner.return_rows["Drone3"]["status"] == "failed")
        self.assertIn("superseded", self.owner.return_rows["Drone3"]["error"])
        self.assertFalse(self.rtl_calls("Drone3"))

    async def test_arrival_requires_position_altitude_and_settled_velocity(self):
        await self.queue()
        self.arrive(2)
        state = self.current[2]
        state["ground_speed_mps"] = 4
        await asyncio.sleep(0.25)
        self.assertEqual(self.owner.return_rows["Drone3"]["status"], "transiting")
        self.assertFalse(self.rtl_calls("Drone3"))
        state["ground_speed_mps"] = 0
        state["altitude_above_home_m"] -= 3
        await asyncio.sleep(0.25)
        self.assertFalse(self.rtl_calls("Drone3"))
        self.arrive(2)
        await self.wait_for(lambda: self.owner.return_rows["Drone3"]["status"] == "returning")

    async def test_only_final_descent_waits_for_another_approach_to_clear(self):
        await self.queue()
        self.arrive(2)
        one, three = self.current[0], self.current[2]
        one.update(
            north_m=three["home"]["north_m"] - 20,
            east_m=three["home"]["east_m"],
            altitude_above_home_m=80,
            mission_current=2,
        )
        await self.wait_for(lambda: self.owner.return_rows["Drone3"]["waiting_for"] == "Drone1")
        self.assertLess(abs(three["north_m"] - three["home"]["north_m"]), 0.5)
        self.arrive(0)
        await self.wait_for(lambda: self.owner.return_rows["Drone3"]["status"] == "returning")

    async def test_override_cancels_approach_without_a_late_rtl(self):
        await self.queue()
        await self.owner.execute("hold", {"drone_id": "Drone4"}, "operator", "hold")
        self.arrive(3)
        await asyncio.sleep(0.3)
        self.assertEqual(self.owner.return_rows["Drone4"]["status"], "stopped")
        self.assertEqual(self.current[3]["mode"], "BRAKE")
        self.assertFalse(self.rtl_calls("Drone4"))

    async def test_plan_swap_cancels_approach_without_a_late_rtl(self):
        await self.queue()
        self.owner.fleet_event(
            {"kind": "plan_swap", "stage": "holding", "version": 2, "drone_ids": ["Drone4"]}
        )
        self.arrive(3)
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
            self.assertEqual(self.owner.return_rows["Drone4"]["status"], "climbing")
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
        route = approach(four, max(s["home"]["altitude_msl_m"] for s in self.current))["route"]
        self.current[1].update(
            north_m=(route[1]["north_m"] + route[2]["north_m"]) / 2,
            east_m=(route[1]["east_m"] + route[2]["east_m"]) / 2,
            altitude_above_home_m=route[-1]["altitude_above_home_m"],
        )
        with self.assertRaisesRegex(PlanRejected, "conflicts with Drone2"):
            await self.owner.return_fleet(["Drone4"], "agent")
        self.owner.fleet._upload.assert_not_awaited()
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
        with self.assertRaisesRegex(PlanRejected, "conflicts with Drone2"):
            await self.owner.fleet.return_step("Drone4")
        self.assertFalse(self.rtl_calls("Drone4"))

    async def test_command_failure_stops_pending_returns_without_retry(self):
        original = self.owner.fleet._mode.side_effect

        async def mode(name, mode):
            if name == "Drone2" and mode == "AUTO":
                raise TimeoutError("Drone2 AUTO acknowledgement timed out")
            await original(name, mode)

        self.owner.fleet._mode.side_effect = mode
        await self.owner.return_fleet(list(NAMES), "agent")
        await self.wait_for(lambda: self.owner.return_rows["Drone2"]["status"] == "failed")
        await self.wait_for(
            lambda: all(
                self.owner.return_rows[n]["status"] == "stopped" for n in ("Drone3", "Drone4")
            )
        )
        self.assertEqual(
            sum(call.args == ("Drone2", "AUTO") for call in self.owner.fleet._mode.await_args_list),
            1,
        )
        self.assertFalse(self.rtl_calls("Drone3"))
        self.assertFalse(self.rtl_calls("Drone4"))

    async def test_failed_return_wakes_wonder_once_even_if_landing_precedes_review(self):
        self.owner.fleet.return_step = AsyncMock(side_effect=TimeoutError("RTL timed out"))
        await self.owner.return_fleet(["Drone4"], "agent")
        await self.wait_for(lambda: self.owner.return_rows["Drone4"]["status"] == "climbing")
        self.arrive(3)
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

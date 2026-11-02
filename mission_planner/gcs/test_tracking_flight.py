"""Candidate flight control preserves search progress and rejects stale authority."""

import asyncio
import copy
import math
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from mission_planner.gcs.fleet import MAV, Fleet
from mission_planner.gcs.geodesy import ORIGIN, to_geodetic, to_local
from mission_planner.gcs.planner import mission_home, mission_item, mission_readback
from mission_planner.gcs.test_validation import readback


def item(north, east, altitude, command=MAV.MAV_CMD_NAV_WAYPOINT):
    latitude, longitude = to_geodetic(north, east)
    return {
        "command": command,
        "x": round(latitude * 1e7),
        "y": round(longitude * 1e7),
        "z": altitude,
    }


class TrackingFlight(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {
            "id": "Drone1",
            "link_lost": False,
            "received_at_ms": time.time() * 1000,
            "ekf_healthy": True,
            "gps_fix": 3,
            "armed": True,
            "camera_available": True,
            "battery_percent": 90,
            "landed_state": MAV.MAV_LANDED_STATE_IN_AIR,
            "mode": "AUTO",
            "ground_speed_mps": 0,
            "climb_rate_mps": 0,
            "heading_deg": 0,
            "north_m": -600,
            "east_m": 0,
            "altitude_above_home_m": 70,
            "home": {"altitude_msl_m": ORIGIN["altitude_msl_m"] + 11.45},
            "mission_current": 3,
        }
        mission = [
            item(-814, 0, 0),
            item(-814, 0, 70, MAV.MAV_CMD_NAV_TAKEOFF),
            {"command": MAV.MAV_CMD_DO_CHANGE_SPEED, "param2": 4},
            item(-550, 0, 70),
            item(-500, 0, 70),
            item(-814, 0, 70),
            item(-814, 0, 0, MAV.MAV_CMD_NAV_LAND),
        ]
        self.mav = Mock()
        self.mav.set_position_target_global_int_send.side_effect = self.move
        self.vehicle = {
            "mission": mission,
            "fence": readback(),
            "sysid": 1,
            "connection": SimpleNamespace(mav=self.mav),
        }
        self.fleet = Fleet("unused", Mock(), [])
        self.fleet.fences_verified = True
        self.fleet.vehicles = {"Drone1": self.vehicle}

        def snapshot():
            state = copy.deepcopy(self.state)
            state["mission"] = []
            for seq, point in enumerate(self.vehicle["mission"]):
                if point["command"] == MAV.MAV_CMD_DO_CHANGE_SPEED:
                    continue
                north, east = to_local(point["x"] / 1e7, point["y"] / 1e7)
                state["mission"].append(
                    {
                        "seq": seq,
                        "north_m": north,
                        "east_m": east,
                        "altitude_above_home_m": point["z"],
                    }
                )
            return [state]

        self.fleet.snapshot = snapshot
        self.fleet.planned_routes = {
            "Drone1": [
                {"north_m": -814, "east_m": 0, "altitude_above_home_m": 0},
                {"north_m": -550, "east_m": 0, "altitude_above_home_m": 70},
            ]
        }
        self.fleet._command = AsyncMock()

        async def expect(name, kind, predicate, send):
            send()
            value = (
                SimpleNamespace(param_id="MIS_RESTART", param_value=0)
                if kind == "PARAM_VALUE"
                else SimpleNamespace(seq=self.state["mission_current"])
            )
            self.assertTrue(predicate(value))
            return value

        self.fleet._expect = AsyncMock(side_effect=expect)

        async def mode(name, mode):
            self.state["mode"] = mode

        async def until(name, predicate, timeout):
            self.assertTrue(predicate())

        self.fleet._mode = AsyncMock(side_effect=mode)
        self.fleet._until = AsyncMock(side_effect=until)

    def move(self, *args):
        north, east = to_local(args[5] / 1e7, args[6] / 1e7)
        self.state.update(north_m=north, east_m=east, altitude_above_home_m=args[7])

    async def test_lock_checkpoint_and_negative_resume_preserve_partial_survey(self):
        original = copy.deepcopy(self.vehicle["mission"])
        saved = await self.fleet.tracking_start("Drone1", (3, 4))
        self.assertEqual(saved["sequence"], 3)
        self.assertEqual(saved["point"]["north_m"], -600)
        self.assertEqual(saved["speed_mps"], 4)
        self.assertEqual(self.state["mode"], "BRAKE")
        await self.fleet.tracking_follow("Drone1", saved, 2)
        target = {"north_m": -590, "east_m": 2, "altitude_above_home_m": 70}
        await self.fleet.tracking_target("Drone1", target, 90)
        args = self.mav.set_position_target_global_int_send.call_args.args
        self.assertEqual(args[4], 2552)
        self.assertAlmostEqual(args[14], math.pi / 2)
        await self.fleet.tracking_resume("Drone1", saved, [saved["point"], target], 2)
        self.assertEqual(self.state["mode"], "AUTO")
        self.assertEqual(self.state["mission_current"], 3)
        self.assertLess(abs(self.state["north_m"] + 600), 0.02)
        self.assertEqual(self.vehicle["mission"], original)
        self.assertFalse(
            any(
                call.args[1] == MAV.MAV_CMD_DO_SET_MISSION_CURRENT
                for call in self.fleet._command.await_args_list
            )
        )
        self.assertEqual(self.fleet._command.await_args_list[-1].args[2][1], 4)

    async def test_stop_must_be_measured_and_search_item_still_current(self):
        self.state["ground_speed_mps"] = 4

        async def until(name, predicate, timeout):
            self.assertFalse(self.fleet.command_lock.locked())
            self.assertFalse(predicate())
            self.state.update(ground_speed_mps=0, climb_rate_mps=0.3)
            self.assertFalse(predicate())
            self.state.update(climb_rate_mps=0, mission_current=4, north_m=-548)
            self.assertTrue(predicate())

        self.fleet._until = AsyncMock(side_effect=until)
        saved = await self.fleet.tracking_start("Drone1", (3, 4))
        self.assertEqual(saved["sequence"], 4)
        self.assertEqual(saved["point"]["north_m"], -548)

    async def test_explicit_acquisition_from_hold_preserves_verified_survey(self):
        self.state["mode"] = "BRAKE"
        saved = await self.fleet.tracking_start("Drone1", (3, 4))
        self.assertEqual(saved["sequence"], 3)
        self.assertEqual(saved["mission"], self.vehicle["mission"])
        self.fleet._mode.assert_not_awaited()
        self.fleet._expect.assert_awaited_once()
        await self.fleet.tracking_follow("Drone1", saved, 2)
        self.assertEqual(self.state["mode"], "GUIDED")

    async def test_held_return_item_cannot_acquire_search_following(self):
        self.state.update(mode="BRAKE", mission_current=5)
        with self.assertRaisesRegex(AssertionError, "outside its observation step"):
            await self.fleet.tracking_start("Drone1", (3, 4))
        self.fleet._mode.assert_not_awaited()

    async def test_target_expiring_in_command_queue_is_never_sent(self):
        self.state["mode"] = "GUIDED"
        target = {"north_m": -590, "east_m": 2, "altitude_above_home_m": 70}
        captured = time.time() * 1000 - 1950
        await self.fleet.command_lock.acquire()
        request = asyncio.create_task(self.fleet.tracking_target("Drone1", target, 90, captured))
        await asyncio.sleep(0.1)
        self.fleet.command_lock.release()
        with self.assertRaisesRegex(AssertionError, "too old at flight-command dispatch"):
            await request
        self.mav.set_position_target_global_int_send.assert_not_called()

    async def test_operator_hold_prevents_late_resume(self):
        saved = await self.fleet.tracking_start("Drone1", (3, 4))
        await self.fleet.execute("hold", {"drone_id": "Drone1"})
        self.fleet._mode.reset_mock()
        with self.assertRaisesRegex(AssertionError, "superseded"):
            await self.fleet.tracking_resume("Drone1", saved, [], 2)
        self.fleet._mode.assert_not_awaited()

    async def test_review_can_resume_owned_brake_without_a_visual_lock(self):
        saved = await self.fleet.tracking_start("Drone1", (3, 4))
        self.assertEqual(self.state["mode"], "BRAKE")
        self.fleet._command.assert_not_awaited()
        await self.fleet.tracking_resume("Drone1", saved, [], 2)
        self.assertEqual(self.state["mode"], "AUTO")
        self.assertEqual(self.state["mission_current"], saved["sequence"])

    async def test_automatic_failure_hold_checks_authority_after_acquiring_command_lock(self):
        for mode, armed in (("RTL", True), ("LAND", True), ("BRAKE", True), ("AUTO", False)):
            with self.subTest(mode=mode, armed=armed):
                self.state.update(mode="AUTO", armed=True)
                await self.fleet.command_lock.acquire()
                hold = asyncio.create_task(
                    self.fleet.execute("hold", {"drone_id": "Drone1"}, expected_mode="AUTO")
                )
                await asyncio.sleep(0)
                self.assertFalse(hold.done())
                self.state.update(mode=mode, armed=armed)
                self.fleet.command_lock.release()
                with self.assertRaisesRegex(AssertionError, "automatic hold was superseded"):
                    await hold
                self.fleet._mode.assert_not_awaited()

    async def test_automatic_hold_accepts_only_its_current_airborne_mode(self):
        for mode in ("AUTO", "GUIDED", "BRAKE"):
            with self.subTest(mode=mode):
                self.state.update(mode=mode, armed=True)
                await self.fleet.execute("hold", {"drone_id": "Drone1"}, expected_mode=mode)
                self.assertEqual(self.state["mode"], "BRAKE")
        self.assertEqual(self.fleet._mode.await_count, 3)

    async def test_observation_failure_hold_rechecks_mission_and_waypoint_after_queueing(self):
        sent = [
            mission_home({"north_m": -814, "east_m": 0, **self.state["home"]}),
            mission_item({"north_m": -550, "east_m": 0}, 70, MAV.MAV_CMD_NAV_WAYPOINT),
            mission_item({"north_m": -500, "east_m": 0}, 70, MAV.MAV_CMD_NAV_LOITER_TIME, 10),
            mission_item({"north_m": -814, "east_m": 0}, 70, MAV.MAV_CMD_NAV_WAYPOINT),
        ]
        for change, message in (
            ("completed", "observation waypoint is no longer current"),
            ("loiter", "observation waypoint is no longer current"),
            ("replaced", "mission was replaced"),
            ("superseded", "authority was superseded"),
            ("current", None),
        ):
            with self.subTest(change=change):
                self.state.update(mode="AUTO", mission_current=1)
                self.vehicle["mission"] = [
                    {**mission_readback(point, home=index == 0), "seq": index}
                    for index, point in enumerate(sent)
                ]
                self.fleet._mode.reset_mock()
                await self.fleet.command_lock.acquire()
                hold = asyncio.create_task(
                    self.fleet.execute(
                        "hold",
                        {"drone_id": "Drone1"},
                        expected_mode="AUTO",
                        expected_step=(sent, 1, 2),
                    )
                )
                await asyncio.sleep(0)
                self.assertFalse(hold.done())
                if change == "completed":
                    self.state["mission_current"] = 3
                elif change == "loiter":
                    self.state["mission_current"] = 2
                elif change == "replaced":
                    self.vehicle["mission"][1]["z"] += 1
                elif change == "superseded":
                    self.fleet.control_versions["Drone1"] += 1
                self.fleet.command_lock.release()
                if message is None:
                    await hold
                    self.fleet._mode.assert_awaited_once_with("Drone1", "BRAKE")
                else:
                    with self.assertRaisesRegex(AssertionError, message):
                        await hold
                    self.fleet._mode.assert_not_awaited()
                    self.assertEqual(self.state["mode"], "AUTO")

    async def test_operator_hold_remains_available_during_rtl(self):
        self.state["mode"] = "RTL"
        await self.fleet.execute("hold", {"drone_id": "Drone1"})
        self.fleet._mode.assert_awaited_once_with("Drone1", "BRAKE")

    async def test_replaced_mission_prevents_late_resume(self):
        saved = await self.fleet.tracking_start("Drone1", (3, 4))
        self.vehicle["mission"][3]["z"] += 1
        with self.assertRaisesRegex(AssertionError, "replaced"):
            await self.fleet.tracking_resume("Drone1", saved, [], 2)
        self.mav.set_position_target_global_int_send.assert_not_called()

    async def test_position_mode_is_initialized_before_setting_follow_speed(self):
        saved = await self.fleet.tracking_start("Drone1", (3, 4))

        async def speed(name, command, parameters):
            self.assertEqual(command, MAV.MAV_CMD_DO_CHANGE_SPEED)
            self.assertEqual(parameters[1], 2)
            self.mav.set_position_target_global_int_send.assert_called_once()
            args = self.mav.set_position_target_global_int_send.call_args.args
            self.assertAlmostEqual(args[7], saved["point"]["altitude_above_home_m"])
            self.assertEqual(args[14], 0)

        self.fleet._command.side_effect = speed
        await self.fleet.tracking_follow("Drone1", saved, 2)
        self.fleet._command.assert_awaited_once()

    async def test_unsafe_follow_target_is_rejected_before_sending(self):
        saved = await self.fleet.tracking_start("Drone1", (3, 4))
        await self.fleet.tracking_follow("Drone1", saved, 2)
        self.mav.set_position_target_global_int_send.reset_mock()
        for target in (
            {"north_m": 5000, "east_m": 0, "altitude_above_home_m": 70},
            {"north_m": -500, "east_m": 0, "altitude_above_home_m": 9},
        ):
            with self.subTest(target=target), self.assertRaises(AssertionError):
                await self.fleet.tracking_target("Drone1", target, 0)
        self.mav.set_position_target_global_int_send.assert_not_called()

    async def test_plan_finishing_during_braking_never_starts_following(self):
        async def until(name, predicate, timeout):
            self.state["mission_current"] = 5
            self.assertTrue(predicate())

        self.fleet._until = AsyncMock(side_effect=until)
        with self.assertRaisesRegex(AssertionError, "observation step changed"):
            await self.fleet.tracking_start("Drone1", (3, 4))
        self.assertEqual(self.state["mode"], "BRAKE")
        self.fleet._command.assert_not_awaited()

    async def test_non_waypoint_command_is_rejected_before_candidate_brake(self):
        for command in (
            MAV.MAV_CMD_NAV_LOITER_TIME,
            MAV.MAV_CMD_NAV_LOITER_UNLIM,
            MAV.MAV_CMD_DO_CHANGE_SPEED,
        ):
            with self.subTest(command=command):
                self.vehicle["mission"][3]["command"] = command
                with self.assertRaisesRegex(AssertionError, "unfinished observed waypoint"):
                    await self.fleet.tracking_start("Drone1", (3, 4))
                self.assertEqual(self.state["mode"], "AUTO")
        self.fleet._mode.assert_not_awaited()
        self.fleet._expect.assert_not_awaited()
        self.fleet._command.assert_not_awaited()

    async def test_stale_telemetry_prevents_any_tracking_command(self):
        self.state["received_at_ms"] -= 3000
        with self.assertRaisesRegex(AssertionError, "stale"):
            await self.fleet.tracking_start("Drone1", (3, 4))
        self.fleet._mode.assert_not_awaited()

    async def test_mission_restart_setting_is_read_and_rejected_without_changing_it(self):
        self.fleet._expect = AsyncMock(return_value=SimpleNamespace(param_value=1))
        with self.assertRaisesRegex(AssertionError, "MIS_RESTART must be 0"):
            await self.fleet.tracking_start("Drone1", (3, 4))
        self.fleet._mode.assert_not_awaited()
        self.mav.param_set_send.assert_not_called()

    async def test_low_battery_prevents_following_before_voltage_threshold(self):
        self.state["battery_percent"] = 45
        with self.assertRaisesRegex(
            AssertionError, "10% charge floor and 45.55% voltage threshold"
        ):
            await self.fleet.tracking_start("Drone1", (3, 4))
        self.fleet._mode.assert_not_awaited()

    async def test_candidate_has_no_duplicate_five_minute_reserve(self):
        self.state["battery_percent"] = 50
        self.assertEqual(self.fleet._tracking_state("Drone1")["battery_percent"], 50)

    async def test_parameter_read_cannot_override_a_new_failsafe_mode(self):
        async def expect(*args):
            self.state["mode"] = "RTL"
            return SimpleNamespace(param_value=0)

        self.fleet._expect = AsyncMock(side_effect=expect)
        with self.assertRaisesRegex(AssertionError, "superseded before candidate pause"):
            await self.fleet.tracking_start("Drone1", (3, 4))
        self.fleet._mode.assert_not_awaited()

    async def test_parameter_read_cannot_pause_after_observation_step_ends(self):
        async def expect(*args):
            self.state["mission_current"] = 5
            return SimpleNamespace(param_value=0)

        self.fleet._expect = AsyncMock(side_effect=expect)
        with self.assertRaisesRegex(
            AssertionError, "observation step changed before candidate pause"
        ):
            await self.fleet.tracking_start("Drone1", (3, 4))
        self.fleet._mode.assert_not_awaited()
        self.assertEqual(self.state["mode"], "AUTO")

    async def test_parameter_read_cannot_pause_on_an_unobserved_command(self):
        self.vehicle["mission"][4]["command"] = MAV.MAV_CMD_NAV_LOITER_TIME

        async def expect(*args):
            self.state["mission_current"] = 4
            return SimpleNamespace(param_value=0)

        self.fleet._expect = AsyncMock(side_effect=expect)
        with self.assertRaisesRegex(AssertionError, "unfinished observed waypoint"):
            await self.fleet.tracking_start("Drone1", (3, 4))
        self.fleet._mode.assert_not_awaited()
        self.assertEqual(self.state["mode"], "AUTO")

    async def test_resume_accepts_forward_progress_but_rejects_rewinding(self):
        for sequence in (1, 4):
            with self.subTest(sequence=sequence):
                self.setUp()
                saved = await self.fleet.tracking_start("Drone1", (3, 4))

                async def expect(name, kind, predicate, send):
                    return (
                        SimpleNamespace(param_value=0)
                        if kind == "PARAM_VALUE"
                        else SimpleNamespace(seq=sequence)
                    )

                self.fleet._expect = AsyncMock(side_effect=expect)
                if sequence == 1:
                    with self.assertRaisesRegex(AssertionError, "rewound"):
                        await self.fleet.tracking_resume("Drone1", saved, [], 2)
                else:
                    await self.fleet.tracking_resume("Drone1", saved, [], 2)


if __name__ == "__main__":
    unittest.main()

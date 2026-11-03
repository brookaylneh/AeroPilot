"""Launch approval and failure boundaries around actual plan compilation."""

import asyncio
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from mission_planner.environment import ROOT
from mission_planner.gcs.fleet import MAV, Fleet, with_kept
from mission_planner.gcs.geodesy import MIN_SEPARATION_M, NAMES
from mission_planner.gcs.planner import mission_readback
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.service import Mission
from mission_planner.gcs.tasking import SETTINGS_SCHEMA
from mission_planner.gcs.test_planner import airborne, fly, hover, plan, search_plan, states
from mission_planner.gcs.test_validation import HOME_HEIGHTS, readback
from mission_planner.gcs.validation import validate_separation
from world.cosys.landmarks import load

TRIGGER = {"source": "agent", "event_id": "operator-1", "text": "Go to the new destination"}


def fleet(current):
    value = Fleet("unused", Mock(), load())
    value.fences_verified = True
    value.snapshot = lambda: copy.deepcopy(current)
    value.vehicles = {
        state["id"]: {
            "fence": readback(),
            "mission": [],
            "mode": "AUTO" if state["armed"] else "GUIDED",
            "camera_available": state["armed"],
            "messages": {"HEARTBEAT": SimpleNamespace(base_mode=128 if state["armed"] else 0)},
            "times": {"HOME_POSITION": 0.0, "GLOBAL_POSITION_INT": 0.0},
        }
        for state in current
    }
    for state in current:
        state.update(mode=value.vehicles[state["id"]]["mode"], mission_total=0, mission=[])
    value.planned_routes = {state["id"]: [copy.deepcopy(state)] for state in current}
    value._upload = AsyncMock()
    value._download = AsyncMock()

    async def command(name, command, params):
        if command == MAV.MAV_CMD_COMPONENT_ARM_DISARM:
            value.vehicles[name]["messages"]["HEARTBEAT"].base_mode = 128
            next(state for state in current if state["id"] == name)["armed"] = True
        elif command == MAV.MAV_CMD_REQUEST_MESSAGE:
            kind = {242: "HOME_POSITION", 33: "GLOBAL_POSITION_INT"}[params[0]]
            value.vehicles[name]["times"][kind] += 1
        elif command == MAV.MAV_CMD_MISSION_START:
            value.vehicles[name]["mode"] = "AUTO"
            next(state for state in current if state["id"] == name)["mode"] = "AUTO"

    value._command = AsyncMock(side_effect=command)
    value._until = AsyncMock()

    async def mode(name, mode):
        next(s for s in current if s["id"] == name)["mode"] = mode
        value.vehicles[name]["mode"] = mode

    value._mode = AsyncMock(side_effect=mode)
    return value


def transit_task(drone_ids=("Drone1",)):
    return {
        "task": "transit",
        "subject": None,
        "references": {
            "landmark_ids": ["ranger-cabin"],
            "points": [],
            "polygon": None,
            "direction_of_travel": None,
            "subject_category": "unknown",
        },
        "fleet": {"drone_ids": list(drone_ids), "count": None},
        "settings": {key: None for key in SETTINGS_SCHEMA["properties"]},
        "aftermath": "hover",
        "region": {
            "band_index": None,
            "accept_partial": False,
            "radius_m": None,
            "half_angle_deg": None,
        },
        "expansion": {"automatic_proposals": False, "lead_seconds": None},
    }


def mission(current):
    owner = object.__new__(Mission)
    owner.fence_change = False
    owner.case = None
    owner.fleet = fleet(current)
    owner.fleet_ready, owner.fleet_error = True, None
    owner.search = SimpleNamespace(status="unplanned", paused={})
    owner.events = [
        {"id": "operator-1", "kind": "operator", "sequence": 1, "text": "Go to the new destination"}
    ]
    owner.event = Mock()
    owner.autopilot_alerts = {}
    owner.fleet.on_event = owner.fleet_event
    owner.active_flight = set()
    owner.approvals = {}
    owner.plan_preview = owner.plan_active = owner.plan_previous = owner.plan_task = None
    owner.plan_trigger = None
    owner.holds = set()
    owner.proposal = None
    owner.step_clock = {}
    owner.agent_task = None
    owner.agent_lock = asyncio.Lock()
    owner.agent_wait = owner.wait_task = None
    owner.inspections = Mock(
        snapshot=Mock(return_value=[]),
        pending_events=Mock(return_value=[]),
        close=AsyncMock(),
        flight_stopped=AsyncMock(),
        start_search=AsyncMock(),
    )
    owner.agent_busy = False
    owner.conversation = []
    owner.tasks = set()
    owner.operator_tasks = set()
    owner.return_rows = {}
    owner.return_tasks = {}
    return owner


async def pending_approval(owner):
    async def ready():
        while not owner.approvals:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(ready(), 2)


class PlanApplication(unittest.IsolatedAsyncioTestCase):
    async def test_survey_progress_preserves_absolute_pattern_index_across_changes(self):
        current = airborne(-745, -55, 40)
        owner = mission(current)
        request = search_plan()
        request["drones"] = request["drones"][:1]
        owner.plan_active = await owner.fleet.preview_plan(request)
        first = owner.plan_active["missions"]["Drone1"]["step_ranges"][0]
        current[0]["mission_current"] = first["survey_seqs"][1] + 1
        progress = owner.planner_state()["progress"]["Drone1"]
        self.assertEqual(progress["survey_start_at"], 2)
        self.assertEqual(progress["completed_steps"], [])
        request["version"] = 2
        request["drones"][0]["steps"][0].update(start_at=progress["survey_start_at"], speed_mps=10)
        owner.plan_active = await owner.fleet.preview_plan(request)
        updated = owner.plan_active["missions"]["Drone1"]["step_ranges"][0]
        current[0]["mission_current"] = updated["survey_seqs"][0] + 1
        self.assertEqual(owner.planner_state()["progress"]["Drone1"]["survey_start_at"], 3)
        owner.fleet._command.assert_not_awaited()

    async def test_plan_task_assigns_the_next_version_and_keeps_tasking_extras(self):
        current = states()
        owner = mission(current)
        result = await owner.execute("plan_task", {"tasking": transit_task()}, "agent", "t1")
        self.assertEqual(result["plan"]["version"], 1)
        self.assertEqual(result["tasking"]["task"], "transit")
        self.assertTrue({"region", "sectors", "fit", "rationale"} <= set(result))
        self.assertNotIn("items", result["missions"]["Drone1"])
        self.assertEqual(owner.plan_trigger, TRIGGER)
        self.assertIs(owner.plan_preview["plan"], owner.plan_preview["plan"])
        owner.plan_active = owner.plan_preview
        again = await owner.execute("plan_task", {"tasking": transit_task()}, "agent", "t2")
        self.assertEqual(again["plan"]["version"], 2)
        owner.fleet._command.assert_not_awaited()
        current[0].update(armed=True, landed_state=MAV.MAV_LANDED_STATE_IN_AIR)
        current[0]["altitude_above_home_m"] = 40
        owner.vehicles = lambda: current
        # Drone1 flies on; a tasking that names only Drone2 keeps Drone1's plan beside it.
        third = await owner.execute(
            "plan_task", {"tasking": transit_task(drone_ids=["Drone2"])}, "agent", "t3"
        )
        self.assertEqual(third["plan"]["version"], 2)
        self.assertEqual(list(owner.plan_preview["missions"]), ["Drone2"])
        kept = owner.kept_flights(owner.plan_preview)
        self.assertEqual([k["drone_id"] for k in kept], ["Drone1"])
        self.assertEqual(kept[0]["branch"], owner.plan_active["plan"]["drones"][0])
        self.assertIsNone(kept[0]["search"])

    async def test_expansion_proposal_needs_approval_and_a_decline_keeps_the_compiled_hover(self):
        from mission_planner.gcs.test_expansion import airborne_mid_survey, search_receipt

        receipt = search_receipt()
        fleet_states, progress = airborne_mid_survey(receipt, battery=78, late=NAMES)
        owner = mission(fleet_states)
        owner.plan_active = receipt
        owner.planner_state = Mock(
            return_value={
                "preview": None,
                "active": receipt,
                "previous": None,
                "progress": progress,
            }
        )
        proposal = await owner.propose_expansion(receipt)
        self.assertEqual(proposal["kind"], "expansion")
        self.assertEqual((proposal["version"], proposal["band_index"]), (2, 2))
        self.assertNotIn("items", proposal["receipt"]["missions"]["Drone1"])
        self.assertIs(owner.plan_preview["tasking"], proposal["receipt"]["tasking"])
        self.assertEqual(owner.proposal, {"kind": "expansion", "version": 2, "band_index": 2})
        with self.assertRaisesRegex(AssertionError, "another version"):
            await owner.execute("apply_proposal", {"version": 3}, "agent", "wrong")
        applying = asyncio.create_task(
            owner.execute("apply_proposal", {"version": 2}, "agent", "apply")
        )
        await pending_approval(owner)
        card = next(iter(owner.approvals.values()))["card"]
        self.assertEqual((card["name"], card["arguments"]), ("apply_proposal", {"version": 2}))
        text = owner.event.call_args_list[-1].args[1]
        self.assertEqual(text, "Plan 2 · search expansion to band 2 awaiting operator approval")
        next(iter(owner.approvals.values()))["future"].set_result(False)
        with self.assertRaisesRegex(AssertionError, "Operator declined"):
            await applying
        self.assertIsNone(owner.proposal)
        owner.fleet._mode.assert_not_awaited()
        owner.fleet._upload.assert_not_awaited()
        with self.assertRaisesRegex(AssertionError, "No search expansion proposal"):
            await owner.execute("apply_proposal", {"version": 2}, "agent", "gone")
        owner.fleet.upload_mission = AsyncMock()
        await owner.propose_expansion(receipt)
        applying = asyncio.create_task(
            owner.execute("apply_proposal", {"version": 2}, "agent", "apply-again")
        )
        await pending_approval(owner)
        next(iter(owner.approvals.values()))["future"].set_result(True)
        applied = await applying
        self.assertEqual(applied["receipt"]["plan"]["version"], 2)
        self.assertEqual(owner.plan_active["plan"]["version"], 2)
        self.assertEqual(owner.plan_active["tasking"]["task"], "area_search")
        self.assertEqual(owner.plan_active["region"]["bands"][0]["status"], "previous")
        self.assertIsNone(owner.proposal)

    async def test_watcher_proposes_once_when_due_and_hands_failed_rows_on(self):
        from mission_planner.gcs.test_expansion import airborne_mid_survey, search_receipt

        receipt = search_receipt()
        fleet_states, progress = airborne_mid_survey(receipt, battery=78, late=NAMES)
        for row in progress.values():
            row["time_remaining_s"] = 1.0
        owner = mission(fleet_states)
        owner.plan_active = receipt
        owner.planner_state = Mock(
            return_value={
                "preview": None,
                "active": receipt,
                "previous": None,
                "progress": progress,
            }
        )
        wakes = []

        async def review(text, *, wake):
            wakes.append(wake)
            owner.plan_active = None

        owner.agent = AsyncMock(side_effect=review)
        await asyncio.wait_for(owner.watch_plan(receipt), 30)
        self.assertEqual([w["reason"] for w in wakes], ["proposal"])
        self.assertEqual(wakes[0]["proposal"]["band_index"], 2)
        self.assertEqual(owner.plan_preview["plan"]["version"], 2)
        owner.plan_active, owner.plan_preview, owner.proposal = receipt, None, None
        failed_row = {
            "id": "inspection-3",
            "drone_id": "Drone3",
            "status": "failed",
            "rescue": {"pending": True, "phase": "failed"},
            "latest": None,
        }
        owner.inspections.pending_events.return_value = [failed_row]
        await asyncio.wait_for(owner.watch_plan(receipt), 30)
        self.assertEqual(wakes[1]["reason"], "rescue")
        self.assertEqual(wakes[1]["proposal"]["kind"], "reassignment")
        self.assertEqual(wakes[1]["proposal"]["failed"], "Drone3")
        kinds = {
            b["drone_id"]: [s["kind"] for s in b["steps"]]
            for b in owner.plan_preview["plan"]["drones"]
        }
        self.assertEqual(kinds["Drone3"], ["fly", "return_home"])
        self.assertEqual(owner.proposal["kind"], "reassignment")
        owner.fleet.upload_mission = AsyncMock()
        owner.inspections.pending_events.return_value = []
        applied = await owner.execute("apply_plan_change", {"version": 2}, "agent", "reassign")
        self.assertEqual(applied["receipt"]["plan"]["version"], 2)
        self.assertFalse(owner.approvals)
        self.assertIsNone(owner.proposal)

    async def test_battery_reset_rewrites_the_simulator_voltage_and_resets_consumption(self):
        current = states()
        value = fleet(current)
        value._parameter = AsyncMock()
        for state in current:
            value.vehicles[state["id"]]["messages"]["SYS_STATUS"] = SimpleNamespace(
                battery_remaining=100
            )
        await value.reset_batteries()
        for name in NAMES:
            self.assertEqual(
                [c.args for c in value._parameter.await_args_list if c.args[0] == name],
                [(name, "SIM_BATT_VOLTAGE", 12.61), (name, "SIM_BATT_VOLTAGE", 12.6)],
            )
            self.assertIn(
                (name, MAV.MAV_CMD_BATTERY_RESET, [1, 100, 0, 0, 0, 0, 0]),
                [c.args for c in value._command.await_args_list],
            )
        self.assertEqual(value.on_event.call_args.args[0]["kind"], "battery_reset")
        current[0].update(armed=True, landed_state=MAV.MAV_LANDED_STATE_IN_AIR)
        with self.assertRaisesRegex(AssertionError, "landed and disarmed"):
            await value.reset_batteries()
        self.assertEqual(value._parameter.await_count, 8)
        owner = mission(states())
        owner.clearing = False
        owner.chat_ready = lambda: False
        owner.fleet.reset_batteries = AsyncMock()
        with self.assertRaisesRegex(AssertionError, "landed and disarmed at its home pad"):
            await owner.handle({"type": "reset_batteries"}, None)
        owner.chat_ready = lambda: True
        await owner.handle({"type": "reset_batteries"}, None)
        owner.fleet.reset_batteries.assert_awaited_once()

    async def test_plan_task_rejection_carries_the_assigned_version_and_clicked_points(self):
        owner = mission(states())
        owner.plan_active = {"plan": {"version": 4, "drones": []}, "missions": {}}
        bad = transit_task()
        bad["references"]["landmark_ids"] = ["nowhere"]
        result = await owner.invoke("plan_task", {"tasking": bad}, "agent", "bad")
        self.assertFalse(result["ok"])
        self.assertEqual(result["rejection"]["rule"], "reference")
        self.assertEqual(result["rejection"]["version"], 5)
        self.assertIsNone(owner.plan_preview)
        clicked = transit_task()
        clicked["references"].update(landmark_ids=[], points=[{"north_m": -600, "east_m": -200}])
        with self.assertRaises(PlanRejected) as caught:
            await owner.execute("plan_task", {"tasking": clicked}, "agent", "unclicked")
        self.assertEqual(caught.exception.rejection["rule"], "reference")
        owner.events.append(
            {
                "id": "operator-2",
                "kind": "operator",
                "sequence": 2,
                "text": "Use the map point at -600.00 m north, -200.00 m east.",
            }
        )
        self.assertEqual(owner.operator_points(), [{"north_m": -600.0, "east_m": -200.0}])
        result = await owner.execute("plan_task", {"tasking": clicked}, "agent", "clicked")
        self.assertEqual(result["sectors"][0]["targets"], [{"north_m": -600.0, "east_m": -200.0}])
        owner.events.append({"id": "reset", "kind": "chat_reset", "sequence": 3, "text": ""})
        self.assertEqual(owner.operator_points(), [])

    async def test_all_launches_arm_before_fresh_references_and_auto_without_rewriting_upload(self):
        current = states()
        value = fleet(current)
        request = {
            "version": 1,
            "drones": [
                {"drone_id": name, "steps": [{"kind": "takeoff", "height_m": 30}, hover()]}
                for name in ("Drone1", "Drone2")
            ],
        }
        expected = await value.preview_plan(request)
        value.upload_mission = AsyncMock()
        send = value._command.side_effect

        async def command(name, command, params):
            await send(name, command, params)
            if command == MAV.MAV_CMD_COMPONENT_ARM_DISARM:
                state = next(state for state in current if state["id"] == name)
                state["home"]["altitude_msl_m"] -= 0.01
                state["altitude_above_home_m"] += 0.01

        value._command.side_effect = command
        applied = await value.apply_plan(request)
        self.assertEqual(applied["receipt"], expected)
        for call in value.upload_mission.await_args_list:
            self.assertEqual(call.args[1], expected["missions"][call.args[0]]["items"])
        calls = value._command.await_args_list
        arms = [
            i for i, call in enumerate(calls) if call.args[1] == MAV.MAV_CMD_COMPONENT_ARM_DISARM
        ]
        requests = [
            i for i, call in enumerate(calls) if call.args[1] == MAV.MAV_CMD_REQUEST_MESSAGE
        ]
        starts = [i for i, call in enumerate(calls) if call.args[1] == MAV.MAV_CMD_MISSION_START]
        self.assertEqual((len(arms), len(requests), len(starts)), (2, 4, 2))
        self.assertLess(max(arms), min(requests))
        self.assertLess(max(requests), min(starts))
        self.assertEqual(value.upload_mission.await_count, 2)
        self.assertEqual(
            [state["home"] for state in applied["states"]], [state["home"] for state in current]
        )

    async def test_home_change_during_arm_rejects_new_separation_conflict_before_auto(self):
        current = states()
        value = fleet(current)
        request = plan([{"kind": "takeoff", "height_m": 30}, hover()])
        receipt = await value.preview_plan(request)
        top = receipt["missions"]["Drone1"]["route"][1]["altitude_above_home_m"]
        current[1].update(
            north_m=current[0]["north_m"],
            east_m=current[0]["east_m"],
            altitude_above_home_m=top + MIN_SEPARATION_M,
            armed=True,
            landed_state=2,
            mode="BRAKE",
        )
        value.upload_mission = AsyncMock()
        send = value._command.side_effect

        async def command(name, command, params):
            await send(name, command, params)
            if command == MAV.MAV_CMD_COMPONENT_ARM_DISARM:
                current[0]["home"]["altitude_msl_m"] += 0.01
                current[0]["altitude_above_home_m"] -= 0.01

        value._command.side_effect = command
        with self.assertRaisesRegex(
            RuntimeError, "Pre-AUTO.*no mission started:.*measured 3.990000"
        ):
            await value.apply_plan(request)
        self.assertNotIn(
            MAV.MAV_CMD_MISSION_START, [call.args[1] for call in value._command.await_args_list]
        )
        self.assertEqual(value.upload_mission.await_count, 1)
        self.assertTrue(current[0]["armed"])
        self.assertEqual(current[0]["mode"], "GUIDED")

    async def test_home_change_during_arm_rechecks_uploaded_terrain_clearance(self):
        current = states()
        value = fleet(current)
        request = plan([{"kind": "takeoff", "height_m": 10.25}, hover()])
        value.upload_mission = AsyncMock()
        send = value._command.side_effect

        async def command(name, command, params):
            await send(name, command, params)
            if command == MAV.MAV_CMD_COMPONENT_ARM_DISARM:
                current[0]["home"]["altitude_msl_m"] -= 0.5
                current[0]["altitude_above_home_m"] += 0.5

        value._command.side_effect = command
        with self.assertRaisesRegex(
            RuntimeError, "Pre-AUTO.*no mission started:.*clearance above terrain"
        ):
            await value.apply_plan(request)
        self.assertNotIn(
            MAV.MAV_CMD_MISSION_START, [call.args[1] for call in value._command.await_args_list]
        )

    async def test_post_arm_home_and_position_must_be_fresh_without_retry(self):
        for missing in (242, 33):
            with self.subTest(message_id=missing):
                current = states()
                value = fleet(current)
                value.upload_mission = AsyncMock()
                send = value._command.side_effect

                async def command(name, command, params):
                    if command != MAV.MAV_CMD_REQUEST_MESSAGE or params[0] != missing:
                        await send(name, command, params)

                async def until(name, predicate, timeout):
                    if not predicate():
                        raise TimeoutError(f"{name}: fresh telemetry timed out")

                value._command.side_effect = command
                value._until.side_effect = until
                with self.assertRaisesRegex(
                    RuntimeError, "Pre-AUTO.*no mission started:.*timed out"
                ):
                    await value.apply_plan(plan([{"kind": "takeoff", "height_m": 30}, hover()]))
                commands = value._command.await_args_list
                self.assertNotIn(MAV.MAV_CMD_MISSION_START, [call.args[1] for call in commands])
                self.assertEqual(
                    sum(
                        call.args[1] == MAV.MAV_CMD_REQUEST_MESSAGE and call.args[2][0] == missing
                        for call in commands
                    ),
                    1,
                )

    async def test_failed_preview_clears_old_launch_authority_without_changing_active_flight(self):
        for invalid in ({"plan": {"version": 2}}, {"plan": plan([fly(900, 0), hover()])}):
            with self.subTest(invalid=invalid):
                owner = mission(airborne())
                await owner.execute(
                    "preview_plan", {"plan": plan([fly(-730, -2.5), hover()])}, "agent", "p"
                )
                active = owner.plan_preview
                owner.plan_active = active
                if "drones" in invalid["plan"]:
                    invalid["plan"]["version"] = 2
                result = await owner.invoke("preview_plan", invalid, "agent", "failed")
                self.assertFalse(result["ok"])
                self.assertIsNone(owner.plan_preview)
                self.assertIs(owner.plan_active, active)
                self.assertEqual(owner.plan_trigger, TRIGGER)
                with self.assertRaisesRegex(AssertionError, "Preview this plan version"):
                    await owner.execute("request_launch", {"version": 1}, "agent", "stale")
                owner.inspections.close.assert_not_awaited()
                owner.fleet._mode.assert_not_awaited()
                owner.fleet._upload.assert_not_awaited()

    async def test_conflict_geometry_reaches_wonder_and_the_event_log(self):
        owner = mission(airborne())
        point = {"north_m": -750, "east_m": -2.5, "altitude_above_home_m": 40}
        with self.assertRaises(PlanRejected) as caught:
            validate_separation("Drone1", [point], [("Drone3", [point])], HOME_HEIGHTS)
        owner.fleet.preview_plan = AsyncMock(side_effect=caught.exception)
        result = await owner.invoke(
            "preview_plan", {"plan": plan([fly(-730, -2.5), hover()])}, "agent", "p"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["rejection"]["conflict"], caught.exception.rejection["conflict"])
        self.assertEqual(result["rejection"]["rule"], "separation")
        self.assertEqual(result["rejection"]["version"], 1)
        self.assertIn("measured 0.000000 m", result["error"])
        logged = owner.event.call_args_list[-1]
        self.assertEqual(logged.args[0], "plan_version")
        self.assertEqual(logged.kwargs["result"]["rejection"], result["rejection"])
        self.assertEqual(logged.kwargs["result"]["trigger"], TRIGGER)
        owner.fleet._upload.assert_not_awaited()

    async def test_fresh_plan_rejection_keeps_the_existing_inspection_active(self):
        owner = mission(airborne())
        owner.plan_preview = {"plan": {"version": 2}, "missions": {"Drone1": {}}}
        owner.fleet.apply_plan = AsyncMock(side_effect=AssertionError("New leg exits fence"))
        with self.assertRaisesRegex(AssertionError, "New leg exits fence"):
            await owner.execute_plan("apply_plan_change", {"version": 2}, "agent", "change")
        owner.inspections.close.assert_not_awaited()
        owner.inspections.invalidate.assert_not_called()

    async def test_tracking_ownership_blocks_competing_yaw_and_raw_flight(self):
        owner = mission(airborne())
        owner.inspections.snapshot.return_value = [
            {"drone_id": "Drone1", "rescue": {"phase": "tracking"}}
        ]
        with self.assertRaisesRegex(AssertionError, "tracking owns"):
            await owner.execute(
                "face_heading", {"drone_id": "Drone1", "heading_deg": 180}, "agent", "yaw"
            )
        with self.assertRaisesRegex(AssertionError, "tracking owns"):
            await owner.execute(
                "fly_route",
                {
                    "drone_id": "Drone1",
                    "waypoints": [{"north_m": 0, "east_m": 0, "altitude_above_home_m": 30}],
                    "speed_mps": 5,
                },
                "operator",
                "fly",
            )

    async def test_relock_candidate_dispatches_to_the_held_session(self):
        owner = mission(airborne())
        arguments = {"inspection_id": "inspection", "frame_id": "hold-frame", "candidate_index": 1}
        outcome = {"inspection_id": "inspection", "frame": {"frame_id": "locked-frame"}}
        owner.inspections.relock_candidate = AsyncMock(return_value=outcome)
        result = await owner.execute("relock_candidate", arguments, "agent", "relock")
        self.assertEqual(result, outcome)
        owner.inspections.relock_candidate.assert_awaited_once_with(**arguments)
        owner.fleet._mode.assert_not_awaited()
        owner.plan_task = object()
        with self.assertRaisesRegex(AssertionError, "already in progress"):
            await owner.execute("relock_candidate", arguments, "agent", "relock-busy")

    async def test_inspection_dispatch_does_not_point_or_hold_the_drone(self):
        owner = mission(airborne())
        arguments = {"drone_id": "Drone1", "prompt": "person", "threshold": 0.3, "duration_s": 30}
        observation = {"id": "inspection", "drone_id": "Drone1", "status": "running"}
        owner.inspections.start = AsyncMock(return_value=observation)
        owner.fleet.face_heading = AsyncMock()
        owner.fleet.execute = AsyncMock()
        result = await owner.execute("start_inspection", arguments, "agent", "inspect")
        self.assertEqual(result, observation)
        owner.inspections.start.assert_awaited_once_with(**arguments)
        owner.fleet.face_heading.assert_not_awaited()
        owner.fleet.execute.assert_not_awaited()
        owner.fleet._mode.assert_not_awaited()
        owner.fleet._command.assert_not_awaited()
        self.assertFalse(owner.approvals)
        self.assertFalse(owner.active_flight)
        self.assertIsNone(owner.plan_task)

    async def test_independent_heading_tool_reports_measured_heading(self):
        current = airborne()
        owner = mission(current)
        current[0]["heading_deg"] = 271.4

        async def point(drone_id, heading_deg):
            self.assertEqual((drone_id, heading_deg), ("Drone1", 270))
            self.assertEqual(owner.active_flight, {"Drone1"})
            self.assertIs(owner.plan_task, asyncio.current_task())
            return current[0]

        owner.fleet.face_heading = AsyncMock(side_effect=point)
        result = await owner.execute(
            "face_heading", {"drone_id": "Drone1", "heading_deg": 270}, "agent", "point"
        )
        self.assertEqual(result["heading_deg"], 271.4)
        self.assertEqual(result["acknowledged_command"], "face_heading")
        self.assertEqual(result["mode"], "AUTO")
        owner.inspections.start.assert_not_called()
        self.assertFalse(owner.active_flight)
        self.assertIsNone(owner.plan_task)

    async def test_operator_hold_cancels_an_operator_heading_action(self):
        owner = mission(airborne())
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def point(**arguments):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        owner.fleet.face_heading = AsyncMock(side_effect=point)
        pointing = asyncio.create_task(
            owner.invoke(
                "face_heading", {"drone_id": "Drone1", "heading_deg": 270}, "operator", "point"
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertIsNone(owner.agent_task)
            self.assertIsNotNone(owner.plan_task)
            self.assertEqual(owner.active_flight, {"Drone1"})
            await asyncio.wait_for(
                owner.execute("hold", {"drone_id": "Drone1"}, "operator", "hold"), 2
            )
            result = await asyncio.wait_for(pointing, 2)
            self.assertTrue(cancelled.is_set())
            self.assertFalse(result["ok"])
            self.assertIn("override cancelled", result["error"])
            self.assertEqual(owner.fleet.vehicles["Drone1"]["mode"], "BRAKE")
            self.assertFalse(owner.active_flight)
            self.assertIsNone(owner.plan_task)
        finally:
            if not pointing.done():
                pointing.cancel()
                await asyncio.gather(pointing, return_exceptions=True)

    async def test_fault_watcher_waits_for_operator_override_cleanup_and_command(self):
        owner = mission(airborne())
        owner.plan_active = receipt = {"missions": {"Drone1": {}}}
        stopped, release, reviewed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def flight_stopped(name):
            stopped.set()
            await release.wait()

        async def review(text, *, wake):
            owner.fleet.execute.assert_awaited_once_with("return_home", {"drone_id": "Drone1"})
            self.assertFalse(owner.operator_tasks)
            self.assertTrue(
                any(
                    call.args == ("tool_result", "return_home") and call.kwargs["result"]["ok"]
                    for call in owner.event.call_args_list
                )
            )
            reviewed.set()
            owner.plan_active = None

        owner.inspections.flight_stopped.side_effect = flight_stopped
        owner.fleet.execute = AsyncMock(return_value=owner.vehicles()[0])
        owner.agent = AsyncMock(side_effect=review)
        override = asyncio.create_task(
            owner.invoke("return_home", {"drone_id": "Drone1"}, "operator", "return")
        )
        await asyncio.wait_for(stopped.wait(), 1)
        owner.fleet_event(
            {
                "kind": "statustext",
                "drone_id": "Drone1",
                "severity": 0,
                "text": "Potential Thrust Loss (3)",
                "at_ms": 1000,
            }
        )
        watcher = asyncio.create_task(owner.watch_plan(receipt))
        try:
            await asyncio.sleep(0.15)
            owner.agent.assert_not_awaited()
        finally:
            release.set()
            await override
            await asyncio.wait_for(reviewed.wait(), 1)
            await asyncio.wait_for(watcher, 1)
            await asyncio.gather(*owner.tasks)

    async def test_operator_hold_discards_late_model_commands(self):
        owner = mission(airborne())
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        request = plan([fly(-730, -2.5), hover()])
        response = {
            "id": "stale-response",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
            "output": [
                {
                    "type": "function_call",
                    "name": name,
                    "call_id": name,
                    "arguments": json.dumps(arguments),
                }
                for name, arguments in (
                    ("preview_plan", {"plan": request}),
                    ("apply_plan_change", {"version": 1}),
                )
            ],
        }

        def respond(*args, trace_path, **kwargs):
            entered.set()
            assert release.wait(5), "Blocked test response was not released"
            trace_path.with_suffix(".request.json").write_text("{}")
            finished.set()
            return response

        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as directory:
            owner.directory = Path(directory)
            owner.model = SimpleNamespace(respond=respond)
            from world.cosys.landmarks import load

            owner.context = Mock(return_value={"inspections": []})
            owner.landmarks = load()[:1]
            owner.effort = "high"
            owner.id = "plan-test"
            owner.invoke = AsyncMock()
            owner.conversation = [{"role": "user", "content": "Previous request"}]
            owner.agent_busy = True
            conversation = owner.agent_task = owner.launch(owner.agent("Go elsewhere"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await asyncio.wait_for(
                    owner.execute("hold", {"drone_id": "Drone1"}, "operator", "hold"), 2
                )
                self.assertFalse(release.is_set())
                self.assertTrue(conversation.cancelled())
                self.assertIsNone(owner.agent_task)
                self.assertFalse(owner.agent_busy)
                self.assertEqual(owner.fleet.vehicles["Drone1"]["mode"], "BRAKE")
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 2))
            await asyncio.sleep(0)
            owner.invoke.assert_not_called()
            self.assertEqual(owner.conversation, [])

    async def test_override_cancels_conversation_before_it_starts(self):
        owner = mission(airborne())
        owner.agent_busy = True
        conversation = owner.agent_task = owner.launch(owner.agent("Go elsewhere"))
        await owner.execute("hold", {"drone_id": "Drone1"}, "operator", "hold")
        self.assertTrue(conversation.cancelled())
        self.assertIsNone(owner.agent_task)
        self.assertFalse(owner.agent_busy)

    async def test_agent_hold_does_not_cancel_its_own_conversation(self):
        owner = mission(airborne())
        owner.agent_task = asyncio.current_task()
        owner.conversation = [{"role": "user", "content": "Previous request"}]
        await asyncio.wait_for(owner.execute("hold", {"drone_id": "Drone1"}, "agent", "hold"), 2)
        self.assertEqual(owner.fleet.vehicles["Drone1"]["mode"], "BRAKE")
        self.assertEqual(owner.conversation, [{"role": "user", "content": "Previous request"}])

    async def test_conversation_cancel_preserves_failed_plan_event(self):
        owner = mission(airborne())
        await owner.execute(
            "preview_plan", {"plan": plan([fly(-730, -2.5), hover()])}, "agent", "p"
        )
        entered = asyncio.Event()

        async def blocked_upload(*args):
            entered.set()
            await asyncio.Event().wait()

        owner.fleet._upload.side_effect = blocked_upload
        owner.agent_busy = True
        conversation = owner.agent_task = owner.launch(
            owner.invoke("apply_plan_change", {"version": 1}, "agent", "a")
        )
        await asyncio.wait_for(entered.wait(), 2)
        await owner.execute("hold", {"drone_id": "Drone1"}, "operator", "hold")
        self.assertTrue(conversation.cancelled())
        failures = [
            call.kwargs["result"]
            for call in owner.event.call_args_list
            if call.args[0] == "plan_version" and call.args[1] == "apply_plan_change"
        ]
        self.assertEqual(len(failures), 1)
        self.assertFalse(failures[0]["ok"])
        self.assertIn("override cancelled", failures[0]["error"])
        owner.fleet._command.assert_not_called()
        self.assertFalse(owner.active_flight)

    async def test_rejected_change_does_not_brake_or_upload(self):
        value = fleet(airborne())
        with self.assertRaisesRegex(AssertionError, "outside the world fence"):
            await value.apply_plan(plan([fly(900, 0), hover()]))
        value._mode.assert_not_called()
        value._upload.assert_not_called()

    async def test_change_joins_apply_position_and_stopped_position(self):
        current = airborne()
        value = fleet(current)
        request = plan([fly(-730, -2.5), hover()])
        preview = await value.preview_plan(request)
        current[0]["north_m"] = -770

        async def brake(name, mode):
            self.assertEqual(mode, "BRAKE")
            current[0]["north_m"] = -765

        value._mode.side_effect = brake
        value.upload_mission = AsyncMock()
        applied = await value.apply_plan(request)
        self.assertEqual(preview["missions"]["Drone1"]["route"][0]["north_m"], -780)
        self.assertEqual(applied["receipt"]["missions"]["Drone1"]["route"][0]["north_m"], -765)
        self.assertEqual(applied["states"][0]["north_m"], -765)
        self.assertEqual(value._command.await_args_list[-1].args[1], MAV.MAV_CMD_MISSION_START)
        self.assertEqual(value._command.await_args_list[-1].args[2], [0] * 7)

    async def test_upload_or_readback_failure_stays_braked(self):
        for failure in ("upload", "readback"):
            with self.subTest(failure=failure):
                value = fleet(airborne())
                if failure == "upload":
                    value._upload.side_effect = TimeoutError("mission upload timed out")
                    error = TimeoutError
                else:
                    value._download.return_value = []
                    error = AssertionError
                with self.assertRaises(error):
                    await value.apply_plan(plan([fly(-730, -2.5), hover()]))
                self.assertEqual(value.vehicles["Drone1"]["mode"], "BRAKE")
                self.assertTrue(value.vehicles["Drone1"]["camera_available"])
                value._command.assert_not_called()
                self.assertEqual(value._upload.await_count, 1)

    async def test_launch_waits_for_one_approval_and_decline_never_flies(self):
        owner = mission(states())
        request = plan([{"kind": "takeoff", "height_m": 40}, hover()])
        await owner.execute("preview_plan", {"plan": request}, "agent", "preview")
        owner.fleet.apply_plan = AsyncMock()
        task = asyncio.create_task(
            owner.invoke("request_launch", {"version": 1}, "agent", "launch")
        )
        await pending_approval(owner)
        self.assertEqual(len(owner.approvals), 1)
        owner.fleet.apply_plan.assert_not_called()
        next(iter(owner.approvals.values()))["future"].set_result(False)
        result = await task
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Operator declined")
        self.assertFalse(owner.approvals)
        owner.fleet.apply_plan.assert_not_called()

    async def test_launch_beside_a_flying_drone_keeps_it_flying(self):
        current = airborne()
        owner = mission(current)
        first = plan([fly(-600, 0), hover()])
        owner.plan_active = await owner.fleet.preview_plan(first)
        owner.fleet.planned_routes["Drone1"] = owner.plan_active["missions"]["Drone1"]["route"]
        before = copy.deepcopy(owner.plan_active["missions"]["Drone1"])
        owner.fleet.upload_mission = AsyncMock()
        request = plan(
            [{"kind": "takeoff", "height_m": 60}, fly(-700, 150, 60), hover()],
            drone="Drone2",
            version=2,
        )
        await owner.execute("preview_plan", {"plan": request}, "operator", "preview")
        task = asyncio.create_task(
            owner.invoke("request_launch", {"version": 2}, "operator", "launch")
        )
        await pending_approval(owner)
        # Drone1 is not a participant: no hold before approval, no brake, no upload.
        self.assertEqual(owner.fleet.vehicles["Drone1"]["mode"], "AUTO")
        next(iter(owner.approvals.values()))["future"].set_result(True)
        result = await task
        self.assertTrue(result["ok"], result)
        active = owner.plan_active
        self.assertEqual(active["plan"]["version"], 2)
        self.assertEqual(sorted(active["missions"]), ["Drone1", "Drone2"])
        self.assertEqual(active["plan"]["drones"][0]["drone_id"], "Drone2")
        self.assertEqual(active["plan"]["drones"][1], first["drones"][0])
        self.assertEqual(active["missions"]["Drone1"], before)
        self.assertNotIn("search", active["plan"])
        self.assertEqual(owner.fleet.upload_mission.await_count, 1)
        self.assertEqual(owner.fleet.upload_mission.await_args.args[0], "Drone2")
        self.assertNotIn(("Drone1", "BRAKE"), [c.args for c in owner.fleet._mode.await_args_list])
        self.assertEqual(owner.fleet.planned_routes["Drone1"], before["route"])
        owner.inspections.close.assert_not_awaited()
        owner.inspections.start_search.assert_not_awaited()
        owner.inspections.carry.assert_called_once_with(["Drone1"], 2)
        self.assertEqual(result["state"]["receipt"]["missions"]["Drone1"], before)

    def test_kept_flights_inherit_their_search_when_the_plan_has_none(self):
        receipt = {
            "plan": {"version": 2, "drones": [{"drone_id": "Drone2", "steps": []}]},
            "missions": {"Drone2": {"items": []}},
        }
        kept = [
            {
                "drone_id": "Drone1",
                "branch": {"drone_id": "Drone1", "steps": [{"kind": "survey", "observe": True}]},
                "mission": {"items": [1]},
                "search": {"perimeter": []},
            }
        ]
        merged = with_kept(receipt, kept)
        self.assertEqual([b["drone_id"] for b in merged["plan"]["drones"]], ["Drone2", "Drone1"])
        self.assertEqual(merged["plan"]["search"], {"perimeter": []})
        self.assertEqual(merged["missions"]["Drone1"], {"items": [1]})
        self.assertIs(with_kept(receipt, []), receipt)
        own = {**receipt, "plan": {**receipt["plan"], "search": {"perimeter": [1]}}}
        self.assertEqual(with_kept(own, kept)["plan"]["search"], {"perimeter": [1]})

    async def test_added_drone_hovers_existing_drone_before_approval(self):
        owner = mission(airborne())
        request = plan([hover()])
        request["drones"].append(
            {"drone_id": "Drone2", "steps": [{"kind": "takeoff", "height_m": 60}, hover()]}
        )
        await owner.execute("preview_plan", {"plan": request}, "agent", "preview")
        task = asyncio.create_task(
            owner.invoke("request_launch", {"version": 1}, "agent", "launch")
        )
        await pending_approval(owner)
        self.assertEqual(owner.fleet.vehicles["Drone1"]["mode"], "BRAKE")
        self.assertEqual(len(owner.approvals), 1)
        await owner.execute("stop", {"drone_id": "Drone1"}, "operator", "stop")
        result = await task
        self.assertFalse(result["ok"])
        self.assertIn("override cancelled", result["error"])
        self.assertFalse(owner.approvals)
        self.assertIsNone(owner.plan_preview)
        owner.fleet._upload.assert_not_called()

    async def test_stop_during_upload_prevents_later_auto_or_arm(self):
        owner = mission(airborne())
        await owner.execute(
            "preview_plan", {"plan": plan([fly(-730, -2.5), hover()])}, "agent", "p"
        )
        entered = asyncio.Event()

        async def blocked_upload(*args):
            entered.set()
            await asyncio.Event().wait()

        owner.fleet._upload.side_effect = blocked_upload
        applying = asyncio.create_task(
            owner.invoke("apply_plan_change", {"version": 1}, "agent", "a")
        )
        await asyncio.wait_for(entered.wait(), 2)
        await owner.execute("stop", {"drone_id": "Drone1"}, "operator", "stop")
        self.assertFalse((await applying)["ok"])
        owner.fleet._command.assert_not_called()
        self.assertEqual(owner.fleet.vehicles["Drone1"]["mode"], "BRAKE")
        self.assertFalse(owner.active_flight)

    async def test_readback_checks_speed_parameters_and_location_frames(self):
        value = fleet(airborne())
        items = (await value.preview_plan(plan([fly(-730, -2.5), hover()])))["missions"]["Drone1"][
            "items"
        ]
        received = [mission_readback(item, home=index == 0) for index, item in enumerate(items)]
        value._download.return_value = received
        await value.upload_mission("Drone1", items)
        next(i for i in received if i["command"] == MAV.MAV_CMD_DO_CHANGE_SPEED)["param2"] += 1
        with self.assertRaisesRegex(AssertionError, "param2 mismatch"):
            await value.upload_mission("Drone1", items)

    async def test_partial_start_failure_reports_installed_plan_without_rollback(self):
        current = airborne()
        current[1].update(
            north_m=-750, east_m=20, altitude_above_home_m=65, armed=True, landed_state=2
        )
        owner = mission(current)
        owner.watch_plan = AsyncMock()
        request = plan([fly(-730, -2.5), hover()])
        request["drones"].append(
            {"drone_id": "Drone2", "steps": [fly(-700, 20, height=60), hover()]}
        )
        await owner.execute("preview_plan", {"plan": request}, "agent", "p")
        owner.fleet.upload_mission = AsyncMock()

        async def command(name, command, params):
            if command == MAV.MAV_CMD_MISSION_START:
                if name == "Drone2":
                    raise RuntimeError("Drone2 AUTO rejected")
                current[0]["mode"] = "AUTO"

        owner.fleet._command.side_effect = command
        result = await owner.invoke("apply_plan_change", {"version": 1}, "agent", "a")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Drone2 AUTO rejected")
        self.assertEqual(owner.plan_active["plan"], request)
        self.assertIsNone(owner.plan_preview)
        self.assertEqual([s["mode"] for s in current[:2]], ["AUTO", "BRAKE"])
        self.assertEqual(owner.fleet.upload_mission.await_count, 2)
        await asyncio.gather(*owner.tasks)
        owner.watch_plan.assert_awaited_once_with(owner.plan_active)

    async def test_successful_general_plan_starts_fault_watcher(self):
        owner = mission(airborne())
        owner.watch_plan = AsyncMock()
        owner.fleet.upload_mission = AsyncMock()
        request = plan([fly(-730, -2.5), hover()])
        await owner.execute("preview_plan", {"plan": request}, "agent", "p")
        await owner.execute("apply_plan_change", {"version": 1}, "agent", "a")
        await asyncio.gather(*owner.tasks)
        owner.watch_plan.assert_awaited_once_with(owner.plan_active)
        self.assertIsNone(owner.plan_task)

    async def test_early_landing_does_not_complete_unflown_steps(self):
        current = airborne()
        owner = mission(current)
        owner.plan_active = await owner.fleet.preview_plan(plan([fly(-730, -2.5), hover()]))
        first = owner.plan_active["missions"]["Drone1"]["step_ranges"][0]
        current[0].update(
            armed=False, landed_state=1, mission_current=first["last_seq"], mode="LAND"
        )
        progress = owner.planner_state()["progress"]["Drone1"]
        self.assertEqual(progress["completed_steps"], [])
        self.assertIsNone(progress["current_step"])
        self.assertIsNone(progress["time_remaining_s"])

    async def test_uploaded_plan_is_inactive_until_launch(self):
        owner = mission(states())
        owner.plan_active = await owner.fleet.preview_plan(
            plan([{"kind": "takeoff", "height_m": 40}, hover()])
        )
        progress = owner.planner_state()["progress"]["Drone1"]
        self.assertEqual(progress["completed_steps"], [])
        self.assertIsNone(progress["current_step"])
        self.assertIsNone(progress["time_remaining_s"])


if __name__ == "__main__":
    unittest.main()

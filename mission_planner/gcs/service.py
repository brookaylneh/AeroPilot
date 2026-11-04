"""Persistent mission owner: MAVLink, operator/agent tools, approvals and WebSocket."""

import asyncio
import copy
import json
import math
import os
import re
import time
import traceback
import uuid
from itertools import count
from pathlib import Path

import httpx
from pymavlink.dialects.v20 import ardupilotmega as MAV

from mission_planner.agents.map_context import compact_map, ground_height, ground_profile
from mission_planner.agents.model_process import WonderProcess, WonderRequestError
from mission_planner.agents.models import EFFORTS
from mission_planner.agents.tools import (
    AGENT_INSTRUCTIONS,
    CATEGORIES,
    INSPECTION_TOOLS,
    TOOLS,
    validate_arguments,
)
from mission_planner.gcs.allocation import APPROVAL_HOVER_S
from mission_planner.gcs.battery import available_seconds
from mission_planner.gcs.expansion import EXPANSION_LEAD_S, due, expansion_request, reassignment
from mission_planner.gcs.fences import BOUNDARY_CLEARANCE_M, validate_fences
from mission_planner.gcs.fleet import Fleet
from mission_planner.gcs.geodesy import (
    BASE_RADIUS_M,
    MAX_ALTITUDE_M,
    MAX_SPEED_MPS,
    MIN_ALTITUDE_M,
    MIN_SEPARATION_M,
    MIN_SPEED_MPS,
    NAMES,
    ORIGIN,
)
from mission_planner.gcs.inspection import Inspections
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.validation import CLEARANCE_M
from mission_planner.gcs.video_diagnostics import record as record_video_diagnostic
from mission_planner.gcs.waiting import ready, validate_conditions
from world.cosys import client as sim
from world.cosys.landmarks import directions, find, load
from world.cosys.terrain import BOUNDS, terrain_height

MESSAGE_FIELDS = {
    "video_diagnostic": {"type", "sample"},
    "launch": {"type", "case_id"},
    "connect": {"type", "case_id"},
    "start": {"type"},
    "end_mission": {"type"},
    "chat": {"type", "text"},
    "new_chat": {"type"},
    "tool": {"type", "name", "arguments"},
    "approval": {"type", "id", "approved"},
    "export_plan": {"type", "drone_id"},
    "reset_batteries": {"type"},
    "set_effort": {"type", "effort"},
    "set_fences": {"type", "fences"},
}


PLAN_TOOLS = ("plan_task", "preview_plan", "request_launch", "apply_plan_change", "apply_proposal")
TASKING_EXTRAS = ("tasking", "region", "sectors", "fit", "rationale")
CONTEXT_PREFIX = "Current mission context (live system state, not an operator message): "
# The browser sends an operator's map click as this chat line.
MAP_POINT = re.compile(r"Use the map point at (-?\d+\.\d+) m north, (-?\d+\.\d+) m east\.")


def requested_version(name, arguments):
    """The plan version a plan tool asked for, when the request carried one."""
    holder = arguments
    if name == "plan_task":
        holder = None
    if name == "preview_plan":
        holder = arguments["plan"] if isinstance(arguments, dict) and "plan" in arguments else None
    if isinstance(holder, dict) and "version" in holder and type(holder["version"]) is int:
        return holder["version"]
    return None


def encode(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def operator_error(error):
    if isinstance(error, WonderRequestError):
        if error.kind == "http":
            return (
                "Wonder rejected the request (HTTP 400): " + error.detail
                if error.status_code == 400
                else f"Wonder is unavailable (HTTP {error.status_code})"
            ) + ". No command was issued from this Wonder request."
        assert error.kind == "timeout"
        return (
            "Wonder did not respond before the request timed out. "
            "No command was issued from this Wonder request."
        )
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code == 400:
            return (
                "Wonder rejected the request (HTTP 400): "
                + error.response.text
                + ". No command was issued from this Wonder request."
            )
        return (
            f"Wonder is unavailable (HTTP {error.response.status_code}). "
            "No command was issued from this Wonder request."
        )
    if isinstance(error, httpx.TimeoutException):
        return (
            "Wonder did not respond before the request timed out. "
            "No command was issued from this Wonder request."
        )
    return str(error) or f"{type(error).__name__}: no error message was provided."


def compact_vehicle(vehicle):
    return {
        "drone_id": vehicle["id"],
        **{
            key: vehicle[key]
            for key in (
                "mode",
                "armed",
                "landed_state",
                "north_m",
                "east_m",
                "altitude_above_home_m",
                "mission_current",
                "mission_total",
            )
        },
    }


def planning_vehicle(vehicle):
    return {
        **compact_vehicle(vehicle),
        "ground_speed_mps": vehicle["ground_speed_mps"],
        "heading_deg": vehicle["heading_deg"],
        "climb_rate_mps": vehicle["climb_rate_mps"],
        "battery_percent": vehicle["battery_percent"],
        "usable_flight_s": None
        if vehicle["battery_percent"] is None
        else available_seconds(vehicle["battery_percent"]),
        "home": vehicle["home"],
        "height_above_ground_m": vehicle["home"]["altitude_msl_m"]
        - ORIGIN["altitude_msl_m"]
        + vehicle["altitude_above_home_m"]
        - terrain_height(vehicle["north_m"], vehicle["east_m"]),
    }


def planning_receipt(receipt):
    """The receipt for Wonder: no mission items, routes, windows or polygon vertex lists."""
    if receipt is None:
        return None
    compact = {key: value for key, value in receipt.items() if key != "missions"}
    if "region" in compact and "bands" in compact["region"]:
        compact["region"] = {
            **compact["region"],
            "bands": [
                {key: value for key, value in band.items() if key != "polygon"}
                for band in compact["region"]["bands"]
            ],
        }
    if "sectors" in compact:
        compact["sectors"] = [
            {key: value for key, value in sector.items() if key != "polygons"}
            for sector in compact["sectors"]
        ]
    return {
        **compact,
        "missions": {
            name: {
                **{
                    key: [{k: v for k, v in step.items() if k != "survey_seqs"} for step in value]
                    if key == "step_ranges"
                    else value
                    for key, value in mission.items()
                    if key not in ("items", "route", "windows")
                },
                "route_points": len(mission["route"]),
            }
            for name, mission in receipt["missions"].items()
        },
    }


def planning_context(planner):
    return {
        "active": planning_receipt(planner["active"]),
        "preview": planning_receipt(planner["preview"]),
        "previous_version": None
        if planner["previous"] is None
        else planner["previous"]["plan"]["version"],
        "progress": planner["progress"],
    }


def planning_inspection(row, landmarks):
    def located(observation):
        if "ground" not in observation or observation["ground"] is None:
            return observation
        point = observation["ground"]
        return {
            **observation,
            "landmark_location": directions(landmarks, point["north_m"], point["east_m"])["text"],
        }

    result = dict(row)
    if row["latest"] is not None:
        result["latest"] = {
            **row["latest"],
            "detections": [located(item) for item in row["latest"]["detections"]],
        }
    if "rescue" in row:
        rescue = result["rescue"] = dict(row["rescue"])
        rescue["unavailable_tools"] = (
            ["follow_candidate"] if rescue["tracking_error"] is not None else ["set_camera_view"]
        )
        if rescue["track"] is not None:
            rescue["track"] = located(rescue["track"])
        for key in ("live_frame", "held_frame"):
            if key in rescue and rescue[key] is not None:
                rescue[key] = {
                    **rescue[key],
                    "detections": [located(item) for item in rescue[key]["detections"]],
                }
    return result


def planning_instructions(instructions, landmarks):
    """The static request prefix: the brief and the public map, identical on every turn."""
    return instructions + "\nPublic map: " + encode(compact_map(landmarks))


def live_context(context, landmarks):
    """The live mission state travels as the newest input item, after the cacheable prefix."""
    rows = [planning_inspection(row, landmarks) for row in context["inspections"]]
    text = CONTEXT_PREFIX + encode({**context, "inspections": rows})
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def planning_result(name, result):
    if not result["ok"]:
        return result
    state = result["state"]
    if name in ("preview_plan", "plan_task"):
        state = planning_receipt(state)
    elif name in ("request_launch", "apply_plan_change", "apply_proposal"):
        state = {
            **state,
            "receipt": planning_receipt(state["receipt"]),
            "states": [planning_vehicle(vehicle) for vehicle in state["states"]],
        }
    elif name == "get_fleet":
        state = {**state, "planner": planning_context(state["planner"])}
    elif name == "get_drone":
        state = {key: value for key, value in state.items() if key != "waypoints"}
    elif name == "find_place" and state["matches"]:
        places = compact_map(state["matches"])
        state = {**state, "matches": places["places"], "coordinates": places["coordinates"]}
    return {**result, "state": state}


def wake_message(wake):
    """Wake payloads stay in the transcript; vehicles keep only their compact state."""
    if "results" not in wake:
        return wake
    rows = [
        {**row, "vehicle": compact_vehicle(row["vehicle"])} if "vehicle" in row else row
        for row in wake["results"]
    ]
    return {**wake, "results": rows}


def retire_images(history):
    for item in history:
        if "role" in item and item["role"] == "user" and isinstance(item["content"], list):
            item["content"] = [part for part in item["content"] if part["type"] != "input_image"]


def evidence_message(evidence, history):
    supplied = {
        part["sha256"]
        for item in history
        if "content" in item and isinstance(item["content"], list)
        for part in item["content"]
        if part["type"] == "input_image" and "sha256" in part
    }
    assert len(evidence["image_paths"]) == len(evidence["image_order"])
    content, order, previous = [], [], []
    for path, photo in zip(evidence["image_paths"], evidence["image_order"]):
        if photo["sha256"] in supplied:
            previous.append(photo)
            continue
        supplied.add(photo["sha256"])
        order.append(photo)
        content.append({"type": "input_image", "image_path": path, "sha256": photo["sha256"]})
    return {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": "Sensor evidence. Each image is the object's single original 4x 4K "
                "photograph. Its capture timestamp is recorded; it is not a current tracking "
                "frame. SAM boxes and masks belong to the separate main camera. New images "
                "follow image_order; previously_supplied photographs are already in this "
                "conversation. "
                + encode(
                    {
                        "metadata": evidence["metadata"],
                        "image_order": order,
                        "previously_supplied": previous,
                    }
                ),
            },
            *content,
        ],
    }


def flight_constraints(vehicles):
    return {
        "origin_altitude_msl_m": ORIGIN["altitude_msl_m"],
        "altitude_above_home_m": [MIN_ALTITUDE_M, MAX_ALTITUDE_M],
        "speed_mps": [MIN_SPEED_MPS, MAX_SPEED_MPS],
        "minimum_clearance_m": CLEARANCE_M,
        "boundary_clearance_m": BOUNDARY_CLEARANCE_M,
        "minimum_separation_m": MIN_SEPARATION_M,
        "separation": "Observed steps, hovers and takeoff/landing columns reserve space independent "
        "of timing; unobserved transit before a drone's first observed step is separated in time",
        "approval_hover_s": APPROVAL_HOVER_S,
        "fences": {vehicle["id"]: vehicle["fence"] for vehicle in vehicles},
    }


def read_case(case_id):
    assert str(uuid.UUID(case_id)) == case_id.lower(), "Case id must be a UUID"
    directory = Path(os.environ["SNR_SCENARIO_DATA_DIR"])
    value = json.loads((directory / "briefings" / f"{case_id}.json").read_text())
    assert set(value) == {"schemaVersion", "id", "name", "report", "world"}
    assert value["schemaVersion"] == 2 and value["id"].lower() == case_id.lower()
    assert isinstance(value["name"], str) and 1 <= len(value["name"]) <= 80
    assert set(value["report"]) == {"brief", "portrait"}
    assert all(isinstance(v, str) for v in value["report"].values())
    assert all(len(value["report"][k]) <= 4000 for k in ("brief",))
    assert len(value["report"]["portrait"]) <= 45000
    world = value["world"]
    assert set(world) == {"environmentId", "assetVersion", "coordinateSystem", "searchBounds"}
    assert world["coordinateSystem"] == "UE_LH_Z_UP_CENTIMETERS"
    assert world["searchBounds"], "Rescue case has no search bounds"
    for bounds in world["searchBounds"]:
        assert set(bounds) == {"id", "minX", "maxX", "minY", "maxY"}
        assert (bounds["minX"], bounds["maxX"], bounds["minY"], bounds["maxY"]) == (
            BOUNDS["min"]["x"],
            BOUNDS["max"]["x"],
            BOUNDS["min"]["y"],
            BOUNDS["max"]["y"],
        ), "Rescue case search bounds differ from the configured mission fence"
    return value


class Mission:
    def __init__(self, case):
        assert os.environ["SNR_SITL_MODEL"] == "airsim-copter", (
            "Mission service requires the Unreal-coupled fleet"
        )
        self.fleet_ready = False
        self.fleet_closed = False
        self.fleet_error = None
        self.model = WonderProcess()
        self.lifecycle_lock = asyncio.Lock()
        self.clearing = False
        # The operator's effort choice outlives each run, including the one a chat starts.
        self.effort = "high"
        self.initialize_run(case, active=case is not None)
        self.fleet = Fleet(self.directory / "sitl", self.fleet_event, self.landmarks)

    def initialize_run(self, case, *, active):
        assert type(active) is bool and (active or case is None)
        self.active = active
        self.case = case
        self.id = str(uuid.uuid4())
        self.directory = Path(os.environ["SNR_MISSION_DATA_DIR"]) / self.id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log = (self.directory / "events.jsonl").open("x")
        self.events = []
        self.autopilot_alerts = {}
        self.event_sequence = 0
        self.started = time.monotonic()
        self.approvals = {}
        self.active_flight = set()
        self.tasks = set()
        self.operator_tasks = set()
        self.return_rows = {}
        self.return_tasks = {}
        self.agent_busy = False
        self.agent_task = None
        self.agent_lock = asyncio.Lock()
        self.map_focus = None
        self.landmarks = load()
        self.admission_error = None
        self.feed_focus = None
        self.plan_preview = None
        self.plan_active = None
        self.plan_previous = None
        self.plan_trigger = None
        self.step_clock = {}
        # Aircraft braked by an operator override or a pending launch approval; a wait on their
        # steps keeps waiting instead of reporting a stopped flight.
        self.holds = set()
        self.proposal = None
        self.conversation = []
        self.plan_task = None
        self.fence_change = False
        self.agent_wait = None
        self.wait_task = None
        self.inspections = Inspections(self)

    async def begin(self, case_id):
        assert not self.clearing, "A mission is already running or being cleared"
        async with self.lifecycle_lock:
            assert not self.clearing, "A mission is already running or being cleared"
            if case_id is None and self.active:
                assert self.admission_error is None, "Mission admission failed: " + str(
                    self.admission_error
                )
                return None
            assert not self.active, "A mission is already running"
            case = None if case_id is None else read_case(case_id)
            assert self.fleet_ready and self.fleet_error is None, "Fleet is not ready"
            assert all(not v["armed"] and v["landed_state"] == 1 for v in self.vehicles()), (
                "Fleet must be landed"
            )
            self.log.close()
            self.initialize_run(case, active=True)
            await self.enter_world()
            self.event(
                "mode",
                "General mission opened"
                if case is None
                else "Rescue mission launched: " + case["name"],
                result=None if case is None else {"case_id": case["id"], "case_name": case["name"]},
            )
            return case_id

    async def enter_world(self):
        try:
            await asyncio.to_thread(sim.enter_mission)
        except Exception as error:
            self.admission_error = operator_error(error)
            self.event("error", "Mission admission failed: " + self.admission_error)
            raise RuntimeError("Mission admission failed: " + self.admission_error) from error

    def grounded(self):
        vehicles = self.vehicles()
        return (
            self.fleet_ready
            and self.fleet_error is None
            and not self.clearing
            and not self.active_flight
            and self.plan_task is None
            and len(vehicles) == len(NAMES)
            and all(
                not v["armed"]
                and v["landed_state"] == 1
                and not v["link_lost"]
                and v["ekf_healthy"]
                for v in vehicles
            )
        )

    async def end_mission(self):
        assert self.active, "No mission is running"
        airborne = [v["id"] for v in self.vehicles() if v["armed"]]
        self.event(
            "mode",
            "Operator ended the mission"
            + (": returning " + ", ".join(airborne) if airborne else ""),
            source="operator",
        )
        await self.interrupt_agent("operator ending the mission")
        if airborne:
            await self.return_fleet(airborne, "operator")
            returns = [self.return_tasks[name] for name in airborne if name in self.return_tasks]
            if returns:
                await asyncio.wait(returns)
        await self.clear()

    async def clear(self):
        async with self.lifecycle_lock:
            assert not self.clearing, "Mission cancellation has failed; restore the service"
            assert self.active, "No mission is running"
            assert self.grounded(), (
                "Ending the mission requires a healthy fleet with all drones landed and disarmed"
            )
            self.clearing = True
            self.cancel_wait("Mission ended")
            self.inspections.invalidate("Mission ended")
            pending = list(self.tasks)
            for task in pending:
                task.cancel()
            if pending:
                results = await asyncio.gather(*pending, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        raise RuntimeError(
                            f"Mission task failed during cancellation: {result}"
                        ) from result
            assert all(not v["armed"] and v["landed_state"] == 1 for v in self.vehicles()), (
                "Fleet left the ground during mission cancellation"
            )
            await self.inspections.close()
            await self.fleet.clear_missions()
            await asyncio.to_thread(sim.reset_session)
            self.event("mode", "Mission ended; world reset to character selection")
            self.log.close()
            self.initialize_run(None, active=False)
            self.clearing = False

    def event(
        self, kind, text, *, name=None, arguments=None, result=None, call_id=None, source=None
    ):
        self.event_sequence += 1
        item = {
            "sequence": self.event_sequence,
            "id": str(uuid.uuid4()),
            "timestamp_ms": time.time() * 1000,
            "kind": kind,
            "text": text,
            "name": name,
            "arguments": arguments,
            "result": result,
            "call_id": call_id,
            "source": source,
        }
        self.log.write(encode(item) + "\n")
        self.log.flush()
        self.events.append(item)
        return item

    def fleet_event(self, item):
        kind = item["kind"]
        if kind == "statustext":
            text = item["drone_id"] + ": " + item["text"]
            assert type(item["severity"]) is int and 0 <= item["severity"] <= MAV.MAV_SEVERITY_DEBUG
            if item["severity"] <= MAV.MAV_SEVERITY_ERROR:
                key = (item["drone_id"], item["severity"], item["text"])
                if key not in self.autopilot_alerts:
                    self.autopilot_alerts[key] = {**item, "pending": True}
        elif kind == "mode":
            text = item["drone_id"] + " mode " + item["mode"]
        elif kind == "autopilot_started":
            text = item["drone_id"] + " autopilot started"
        elif kind == "firmware_verified":
            text = item["drone_id"] + " firmware verified"
        elif kind == "safety_parameters_verified":
            text = item["drone_id"] + " safety parameters verified"
        elif kind == "rtl_envelope_verified":
            text = f"{item['drone_id']} RTL terrain envelope: {item['clearance_m']:.3f} m clearance"
        elif kind == "fences":
            text = f"Flight fences v{item['fences']['version']} · {item['stage']}"
        elif kind == "sitl_ports_verified":
            text = "SITL listener ports verified"
        elif kind == "battery_reset":
            text = "Simulator batteries reset to 100 % on " + ", ".join(item["drone_ids"])
        elif kind == "home_comparison":
            text = f"{item['drone_id']} home position error {item['home_error_m']:.3f} m"
        elif kind == "plan_swap":
            if item["stage"] == "holding":
                self.cancel_wait("Flight plan changed")
                self.proposal = None
                for name in item["drone_ids"]:
                    self.inspections.invalidate("Flight plan changed", name)
                    if name in self.return_tasks:
                        self.cancel_return(name, "Flight plan changed")
                    elif name in self.return_rows:
                        del self.return_rows[name]
            if item["stage"] == "uploaded":
                self.cancel_wait("Flight plan changed")
                for name in item["drone_ids"]:
                    self.inspections.invalidate("Flight plan changed", name)
                self.plan_previous = self.plan_active
                self.plan_active = item["receipt"]
                kept = [
                    branch["drone_id"]
                    for branch in self.plan_active["plan"]["drones"]
                    if branch["drone_id"] not in item["drone_ids"]
                ]
                if kept:
                    self.inspections.carry(kept, self.plan_active["plan"]["version"])
                if (
                    self.plan_preview is not None
                    and self.plan_preview["plan"] == self.plan_active["plan"]
                ):
                    # The fleet recompiles the plan; the tasking receipt's extras describe it.
                    for key in TASKING_EXTRAS:
                        if key in self.plan_preview:
                            self.plan_active[key] = self.plan_preview[key]
                self.plan_preview = None
                self.step_clock.clear()
                self.holds.difference_update(
                    branch["drone_id"] for branch in self.plan_active["plan"]["drones"]
                )
                launching = {
                    branch["drone_id"]
                    for branch in self.plan_active["plan"]["drones"]
                    if branch["drone_id"] in item["drone_ids"]
                    and branch["steps"][0]["kind"] == "takeoff"
                }
                self.inspections.reset_lock(launching)
                self.autopilot_alerts = {
                    key: alert
                    for key, alert in self.autopilot_alerts.items()
                    if alert["drone_id"] not in launching
                }
            self.event("plan_version", f"Plan {item['version']} · {item['stage']}", result=item)
            return
        else:
            assert kind == "parameter", f"Unknown fleet event: {kind}"
            text = f"{item['drone_id']} {item['name']} verified: {item['value']}"
        self.event("statustext" if kind == "statustext" else "mode", text, result=item)

    def launch(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)

        def completed(finished):
            self.tasks.remove(finished)
            if finished is self.agent_task and not self.agent_lock.locked():
                self.agent_task = None
                self.agent_busy = False
            if not finished.cancelled() and finished.exception() is not None:
                error = finished.exception()
                self.event(
                    "error",
                    operator_error(error),
                    result={
                        "exception_type": type(error).__name__,
                        "traceback": "".join(traceback.format_exception(error)),
                    },
                )

        task.add_done_callback(completed)
        return task

    async def start_fleet(self):
        task = asyncio.create_task(self.fleet.start())
        try:
            await asyncio.wait([task])
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait([task])
        error = task.exception()
        if error is not None:
            self.fleet_error = operator_error(error)
            self.event("error", "Fleet startup failed: " + self.fleet_error)
            await self.fleet.close()
            self.fleet_closed = True
            return
        if self.active:
            await self.enter_world()
        self.fleet_ready = True
        self.event("mode", "Four autopilots connected; fences and missions read back.")

    def vehicles(self):
        return self.fleet.snapshot() if self.fleet_ready else []

    def summary(self, vehicles):
        alerts = [
            row["drone_id"] + " autopilot: " + row["text"] for row in self.autopilot_alerts.values()
        ]
        if self.admission_error is not None:
            alerts.append("Mission admission failed: " + self.admission_error)
        if self.fleet.fence_error is not None:
            alerts.append(self.fleet.fence_error)
        if self.fleet_error is not None:
            alerts.append("Fleet startup failed: " + self.fleet_error)
        elif not self.fleet_ready:
            alerts.append("Starting autopilots; awaiting verified telemetry and fence read-back.")
        for vehicle in vehicles:
            if vehicle["link_lost"]:
                alerts.append(vehicle["id"] + " telemetry link lost")
            elif not vehicle["ekf_healthy"]:
                alerts.append(vehicle["id"] + " EKF is not healthy")
        return {
            "id": self.id,
            "case_id": None if self.case is None else self.case["id"],
            "active": self.active,
            "case_name": "General mission" if self.case is None else self.case["name"],
            "briefing": None if self.case is None else self.case["report"],
            "elapsed_s": time.monotonic() - self.started,
            "alerts": alerts,
        }

    def state(self):
        vehicles = self.vehicles()
        return {
            "type": "state",
            "mission": self.summary(vehicles),
            "vehicles": vehicles,
            "fences": {
                **self.fleet.fences,
                "verified": self.fleet.fences_verified,
                "error": self.fleet.fence_error,
                "editable": self.fences_editable(vehicles),
            },
            "agent_busy": self.agent_busy,
            "agent_wait": self.agent_wait,
            "chat_ready": self.chat_ready(vehicles),
            "effort": self.effort,
            "inspections": self.inspections.snapshot(),
            "returns": [dict(row) for row in self.return_rows.values()],
            "approvals": [item["card"] for item in self.approvals.values()],
            "map_focus": self.map_focus,
            "planner": {"progress": self.planner_state(vehicles)["progress"]},
        }

    def plans(self):
        """Plan receipts, sent to a browser only when one of them changes."""
        return {
            "type": "plans",
            "preview": self.plan_preview,
            "active": self.plan_active,
            "previous": self.plan_previous,
        }

    def fences_editable(self, vehicles):
        return (
            self.fleet_ready
            and not self.clearing
            and not self.fence_change
            and not self.fleet.command_lock.locked()
            and (not self.active_flight or bool(self.approvals))
            and len(vehicles) == len(NAMES)
            and all(
                not v["armed"]
                and v["landed_state"] == 1
                and not v["link_lost"]
                and v["ekf_healthy"]
                and v["gps_fix"] >= 3
                for v in vehicles
            )
        )

    async def set_fences(self, requested):
        async with self.lifecycle_lock:
            assert self.fences_editable(self.vehicles()), (
                "Save flight fences when all four drones are healthy, landed and disarmed"
            )
            assert requested["version"] == self.fleet.fences["version"], (
                "Flight fences changed in another session; reopen the editor"
            )
            validate_fences(requested, {v["id"]: v["home"] for v in self.vehicles()})
            self.fence_change = True
            try:
                self.cancel_wait("Flight fences changed")
                await self.interrupt_agent("flight fence edit")
                for item in self.approvals.values():
                    if not item["future"].done():
                        item["future"].set_exception(
                            RuntimeError("Flight fences changed; preview again")
                        )
                if self.operator_tasks:
                    await asyncio.wait(list(self.operator_tasks))
                self.plan_preview = self.plan_active = self.plan_previous = None
                self.step_clock.clear()
                await self.inspections.close()
                await self.fleet.clear_missions()
                self.event(
                    "mode", "Operator saved flight fences", source="operator", result=requested
                )
                return await self.fleet.set_fences(requested)
            finally:
                self.fence_change = False

    def chat_ready(self, vehicles):
        return (
            len(vehicles) == len(NAMES)
            and all(
                not v["armed"]
                and v["landed_state"] == 1
                and not v["link_lost"]
                and math.dist(
                    (v["north_m"], v["east_m"]), (v["home"]["north_m"], v["home"]["east_m"])
                )
                <= BASE_RADIUS_M
                for v in vehicles
            )
            and not self.active_flight
            and self.plan_task is None
        )

    def planner_state(self, vehicles):
        progress = {}
        if self.plan_active is not None:
            for vehicle in vehicles:
                name = vehicle["id"]
                if name not in self.plan_active["missions"]:
                    continue
                ranges = self.plan_active["missions"][name]["step_ranges"]
                sequence = vehicle["mission_current"]
                current = next(
                    (
                        step
                        for step in ranges
                        if sequence is not None and sequence <= step["last_seq"]
                    ),
                    None,
                )
                landed = not vehicle["armed"] and vehicle["landed_state"] == 1
                completed = [
                    s["step_index"]
                    for s in ranges
                    if sequence is not None
                    and (
                        sequence > s["last_seq"]
                        or (
                            landed
                            and sequence == s["last_seq"]
                            and s is ranges[-1]
                            and self.plan_active["missions"][name]["items"][-1]["command"] == 21
                        )
                    )
                ]
                if landed:
                    current = None
                index = None if current is None else current["step_index"]
                key = (self.plan_active["plan"]["version"], index, vehicle["mode"])
                if name not in self.step_clock or self.step_clock[name][0] != key:
                    self.step_clock[name] = (key, time.monotonic())
                remaining = 0.0 if len(completed) == len(ranges) else None
                if current is not None:
                    future = [step for step in ranges if step["step_index"] >= index]
                    if vehicle["mode"] != "AUTO" or any(s["duration_s"] is None for s in future):
                        remaining = None
                    else:
                        remaining = max(
                            0,
                            sum(s["duration_s"] for s in future)
                            - (time.monotonic() - self.step_clock[name][1]),
                        )
                progress[name] = {
                    "version": self.plan_active["plan"]["version"],
                    "current_step": index,
                    "completed_steps": completed,
                    "time_remaining_s": remaining,
                }
                if current is not None and "survey_seqs" in current:
                    progress[name]["survey_start_at"] = current["survey_start_at"] + sum(
                        sequence > target for target in current["survey_seqs"]
                    )
        return {
            "preview": self.plan_preview,
            "active": self.plan_active,
            "previous": self.plan_previous,
            "progress": progress,
        }

    async def execute_plan(self, name, arguments, source, call_id):
        assert not self.fence_change, "Flight fences are being saved; wait for verification"
        assert self.fleet_ready, "MAVLink fleet is unavailable"
        assert self.plan_task is None, "A plan operation is already in progress"
        if name in ("preview_plan", "plan_task"):
            assert not any(
                card["card"]["name"] in ("request_launch", "apply_proposal")
                for card in self.approvals.values()
            ), "Resolve the pending approval before replacing its preview"
            self.plan_preview = None
            self.proposal = None
            operator = next((e for e in reversed(self.events) if e["kind"] == "operator"), None)
            if source == "agent":
                assert operator is not None, "Wonder planning requires a preceding operator message"
                self.plan_trigger = {
                    "source": source,
                    "event_id": operator["id"],
                    "text": operator["text"],
                }
            else:
                self.plan_trigger = {"source": source, "event_id": call_id, "text": None}
        validate_arguments(name, arguments)
        if name == "plan_task":
            version = 1 if self.plan_active is None else self.plan_active["plan"]["version"] + 1
            self.plan_task = asyncio.current_task()
            try:
                receipt = await self.fleet.preview_task(
                    arguments["tasking"], version, self.operator_points()
                )
            except PlanRejected as error:
                error.rejection["version"] = version
                raise
            finally:
                if self.plan_task is asyncio.current_task():
                    self.plan_task = None
            self.plan_preview = receipt
            return {
                **receipt,
                "missions": {
                    drone: {key: value for key, value in mission.items() if key != "items"}
                    for drone, mission in receipt["missions"].items()
                },
            }
        if name == "preview_plan":
            plan = arguments["plan"]
            if (
                self.plan_active is not None
                and plan["version"] <= self.plan_active["plan"]["version"]
            ):
                raise PlanRejected(
                    "version",
                    f"Plan version must exceed the active version: requested {plan['version']}, "
                    f"active {self.plan_active['plan']['version']}. "
                    "Landing or starting a new chat does not reset the plan version.",
                    detail={
                        "requested": plan["version"],
                        "active": self.plan_active["plan"]["version"],
                    },
                )
            self.plan_task = asyncio.current_task()
            try:
                receipt = await self.fleet.preview_plan(plan)
            finally:
                if self.plan_task is asyncio.current_task():
                    self.plan_task = None
            self.plan_preview = receipt
            return {
                **receipt,
                "missions": {
                    drone: {key: value for key, value in mission.items() if key != "items"}
                    for drone, mission in receipt["missions"].items()
                },
            }
        if name == "apply_proposal":
            assert self.proposal is not None and self.proposal["kind"] == "expansion", (
                "No search expansion proposal is pending"
            )
            assert self.proposal["version"] == arguments["version"], (
                "The proposal has another version"
            )
        receipt = self.plan_preview
        assert receipt is not None and arguments["version"] == receipt["plan"]["version"], (
            "Preview this plan version successfully before applying it"
        )
        aircraft = set(receipt["missions"])
        assert aircraft.isdisjoint(self.active_flight), "Aircraft already has an action in progress"
        self.active_flight.update(aircraft)
        self.plan_task = asyncio.current_task()
        active_before = self.plan_active
        try:
            states = [v for v in self.vehicles() if v["id"] in aircraft]
            launches = any(not v["armed"] for v in states)
            if name == "apply_proposal":
                assert not launches, "A search expansion keeps every participant airborne"
                band = self.proposal["band_index"]
                try:
                    await self.approve(
                        name,
                        arguments,
                        source,
                        call_id,
                        f"Plan {arguments['version']} · search expansion to band {band} awaiting "
                        "operator approval",
                    )
                finally:
                    self.proposal = None
            else:
                assert name == ("request_launch" if launches else "apply_plan_change"), (
                    "Use request_launch for a plan that launches a drone; otherwise use "
                    "apply_plan_change"
                )
            if launches:
                await self.fleet.preview_plan(receipt["plan"])
                await self.cancel_returns(aircraft, "Flight launch awaiting approval")
                if any(vehicle["armed"] for vehicle in states):
                    await self.inspections.close()
                for vehicle in states:
                    if vehicle["armed"]:
                        await self.fleet.execute("hold", {"drone_id": vehicle["id"]})
                        self.holds.add(vehicle["id"])
                await self.approve(
                    name,
                    arguments,
                    source,
                    call_id,
                    f"Plan {arguments['version']} · launch awaiting operator approval",
                )
            assert self.plan_preview is receipt, (
                "Plan changed or was overridden while approval was pending"
            )
            kept = self.kept_flights(receipt)
            applied = await self.fleet.apply_plan(receipt["plan"], kept)
            if kept and not any(v["armed"] for v in states):
                # Launching beside flying drones: their inspections continue under the new
                # version; only the new branches start searching.
                if "search" in receipt["plan"]:
                    active = self.plan_active
                    branches = [b for b in active["plan"]["drones"] if b["drone_id"] in aircraft]
                    await self.inspections.start_search(
                        {**active, "plan": {**active["plan"], "drones": branches}}
                    )
            else:
                await self.inspections.close()
                if "search" in self.plan_active["plan"]:
                    await self.inspections.start_search(self.plan_active)
            return {
                **applied,
                "trigger": self.plan_trigger,
                "progress": self.planner_state(self.vehicles())["progress"],
            }
        finally:
            self.active_flight.difference_update(aircraft)
            if self.plan_task is asyncio.current_task():
                self.plan_task = None
            if self.plan_active is not None and self.plan_active is not active_before:
                self.launch(self.watch_plan(self.plan_active))

    def kept_flights(self, receipt):
        """Airborne drones of the active plan that this receipt does not task keep flying it."""
        if self.plan_active is None:
            return []
        armed = {v["id"] for v in self.vehicles() if v["armed"]}
        plan = self.plan_active["plan"]
        return [
            {
                "drone_id": branch["drone_id"],
                "branch": copy.deepcopy(branch),
                "mission": copy.deepcopy(self.plan_active["missions"][branch["drone_id"]]),
                "search": copy.deepcopy(plan["search"]) if "search" in plan else None,
            }
            for branch in plan["drones"]
            if branch["drone_id"] in armed and branch["drone_id"] not in receipt["missions"]
        ]

    def operator_points(self):
        """Map points the operator clicked in the current chat, oldest first."""
        reset = max((e["sequence"] for e in self.events if e["kind"] == "chat_reset"), default=0)
        points = []
        for event in self.events:
            if event["kind"] != "operator" or event["sequence"] <= reset:
                continue
            match = MAP_POINT.fullmatch(event["text"].strip())
            if match is not None:
                points.append({"north_m": float(match[1]), "east_m": float(match[2])})
        return points

    async def approve(self, name, arguments, source, call_id, text):
        approval_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.approvals[approval_id] = {
            "card": {
                "id": approval_id,
                "name": name,
                "arguments": arguments,
                "source": source,
                "call_id": call_id,
            },
            "future": future,
            "call_id": call_id,
        }
        self.event(
            "approval",
            text,
            name=name,
            arguments=arguments,
            result={"approval_id": approval_id},
            call_id=call_id,
            source=source,
        )
        try:
            approved = await future
        finally:
            del self.approvals[approval_id]
        assert approved, "Operator declined"

    async def execute(self, name, arguments, source, call_id):
        assert not self.fence_change, "Flight fences are being saved; wait for verification"
        if name in PLAN_TOOLS:
            return await self.execute_plan(name, arguments, source, call_id)
        validate_arguments(name, arguments)
        if name == "return_fleet":
            return await self.return_fleet(arguments["drone_ids"], source)
        if name == "return_home" and arguments["drone_id"] in self.return_tasks:
            drone = arguments["drone_id"]
            if source == "operator":
                await self.interrupt_agent("operator return home")
            state = next(state for state in self.vehicles() if state["id"] == drone)
            return {
                **compact_vehicle(state),
                "acknowledged_command": name,
                "return": dict(self.return_rows[drone]),
            }
        if name in INSPECTION_TOOLS:
            if name == "disregard_and_continue":
                return self.inspections.disregard_and_continue(
                    **arguments, source=source, call_id=call_id
                )
            if name == "publish_evidence":
                assert source == "agent", "Only Wonder publishes reviewed evidence"
                return self.inspections.publish_evidence(**arguments)
            if name == "confirm_evidence":
                assert source == "agent", "Only Wonder records operator identification"
                evidence = next(
                    (
                        event
                        for event in self.events
                        if event["id"] == arguments["evidence_id"]
                        and event["kind"] == "evidence"
                        and event["source"] == "agent"
                    ),
                    None,
                )
                assert evidence is not None, "Unknown accepted evidence"
                operator = next(
                    (event for event in reversed(self.events) if event["kind"] == "operator"),
                    None,
                )
                assert operator is not None and operator["id"] == arguments["operator_event_id"], (
                    "Identification must reference the latest operator message"
                )
                assert operator["timestamp_ms"] > evidence["timestamp_ms"], (
                    "Operator identification must follow evidence publication"
                )
                assert not any(
                    event["kind"] == "evidence_confirmation"
                    and event["result"]["evidence_id"] == evidence["id"]
                    for event in self.events
                ), "This evidence already has operator identification"
                return self.event(
                    "evidence_confirmation",
                    operator["text"],
                    result={
                        "evidence_id": evidence["id"],
                        "operator_event_id": operator["id"],
                        "statement": operator["text"],
                    },
                    source="agent",
                )
            if name == "get_candidate":
                return await self.inspections.get_candidate(**arguments)
            if name == "relock_candidate":
                assert self.fleet_ready, "MAVLink fleet is unavailable"
                assert self.plan_task is None, "A flight plan action is already in progress"
                return await self.inspections.relock_candidate(**arguments)
            if name == "follow_candidate":
                assert self.fleet_ready, "MAVLink fleet is unavailable"
                assert self.plan_task is None, "A flight plan action is already in progress"
                return await self.inspections.follow_candidate(**arguments)
            if name == "set_camera_view":
                assert self.plan_task is None, "A flight plan action is already in progress"
                return await self.inspections.set_camera_view(**arguments)
            if name == "face_heading":
                assert self.fleet_ready, "MAVLink fleet is unavailable"
                assert self.plan_task is None, "A flight plan action is already in progress"
                drone = arguments["drone_id"]
                assert not any(
                    row["drone_id"] == drone
                    and "rescue" in row
                    and row["rescue"]["phase"] in ("reviewing", "tracking", "held", "resuming")
                    for row in self.inspections.snapshot()
                ), (
                    "Candidate tracking owns this drone's heading; release the lock or use an override"
                )
                assert drone not in self.active_flight, (
                    "Aircraft already has a flight action in progress"
                )
                self.active_flight.add(drone)
                self.plan_task = asyncio.current_task()
                try:
                    state = await self.fleet.face_heading(**arguments)
                    return {
                        **compact_vehicle(state),
                        "heading_deg": state["heading_deg"],
                        "acknowledged_command": name,
                    }
                finally:
                    if self.plan_task is asyncio.current_task():
                        self.plan_task = None
                    self.active_flight.remove(drone)
            if name == "start_inspection":
                return await self.inspections.start(**arguments)
            if name == "stop_inspection":
                return self.inspections.stop(arguments["drone_id"], "Stopped by " + source)
            assert source == "agent", "Only Wonder can schedule its next turn"
            return self.schedule_wait(**arguments)
        if name == "ground_height":
            return ground_height(**arguments)
        if name == "ground_profile":
            return ground_profile(**arguments)
        if name == "find_place":
            return find(self.landmarks, arguments["name"])
        if name == "get_reported_search_area":
            from mission_planner.gcs.reported_area import resolve

            assert self.case is not None, "Select a saved rescue case with a reported search area"
            return resolve(self.case["report"]["brief"], self.landmarks)
        if name == "describe_location":
            return directions(self.landmarks, arguments["north_m"], arguments["east_m"])
        if name == "get_mission":
            vehicles = self.vehicles()
            return {
                **self.summary(vehicles),
                "briefing": None
                if self.case is None
                else {key: self.case["report"][key] for key in ("brief",)},
                "flight_constraints": flight_constraints(vehicles),
            }
        if name == "show_on_map":
            if arguments["target"] == "drone":
                assert self.fleet_ready, "MAVLink fleet is unavailable"
            self.map_focus = {**arguments, "id": str(uuid.uuid4())}
            return {"map_focus": self.map_focus}
        if name == "open_feed":
            assert self.fleet_ready, "MAVLink fleet is unavailable"
            vehicle = next(v for v in self.vehicles() if v["id"] == arguments["drone_id"])
            assert not vehicle["link_lost"], f"{vehicle['id']}: telemetry link lost"
            assert vehicle["camera_available"], (
                f"{vehicle['id']}: no active uploaded flight plan; camera unavailable"
            )
            self.feed_focus = {"type": "feed_focus", "id": str(uuid.uuid4()), **arguments}
            return {"drone_id": arguments["drone_id"], "opened": True}
        assert self.fleet_ready, "MAVLink fleet is unavailable: " + str(self.fleet_error)
        if name == "get_fleet":
            return {
                "vehicles": [
                    {
                        **planning_vehicle(v),
                        "link_lost": v["link_lost"],
                        "heartbeat_age_s": v["heartbeat_age_s"],
                    }
                    for v in self.vehicles()
                ],
                "planner": self.planner_state(self.vehicles()),
            }
        if name == "get_drone":
            vehicle = next(v for v in self.vehicles() if v["id"] == arguments["drone_id"])
            return {
                **planning_vehicle(vehicle),
                **{
                    key: vehicle[key]
                    for key in (
                        "link_lost",
                        "heartbeat_age_s",
                        "battery_voltage_v",
                        "gps_fix",
                        "satellites",
                        "ekf_healthy",
                    )
                },
                "waypoints": [
                    {
                        key: p[key]
                        for key in (
                            "seq",
                            "north_m",
                            "east_m",
                            "altitude_above_home_m",
                        )
                    }
                    for p in vehicle["mission"]
                    if p["seq"] > 0
                ],
                "status_text": vehicle["status_text"][-5:],
            }
        assert CATEGORIES[name] == "flight"
        if name == "resume":
            assert (
                self.plan_active is None
                or arguments["drone_id"] not in self.plan_active["missions"]
            ), "Set the remaining task in chat; Resume cannot resume a partial plan step"
        if name == "take_off":
            await self.approve(
                name,
                arguments,
                source,
                call_id,
                f"{arguments['drone_id']} · {name.replace('_', ' ')} awaiting operator approval",
            )
        aircraft = {arguments["drone_id"]}
        override = name in ("hold", "stop", "return_home", "land")
        if override:
            await self.cancel_returns([arguments["drone_id"]], "Flight override: " + name)
            self.cancel_wait("Flight override: " + name)
            self.inspections.invalidate("Flight override: " + name, arguments["drone_id"])
            if source == "operator":
                await self.interrupt_agent("operator " + name)
                self.conversation = []
            if self.plan_task is not None:
                task = self.plan_task
                task.cancel()
                await asyncio.wait([task])
            await self.inspections.flight_stopped(arguments["drone_id"])
            self.plan_preview = None
        else:
            assert self.plan_task is None, "A plan operation is already in progress"
            assert not any(
                row["drone_id"] in aircraft
                and "rescue" in row
                and row["rescue"]["phase"] in ("reviewing", "tracking", "held", "resuming")
                for row in self.inspections.snapshot()
            ), "Candidate tracking owns this drone; release the lock or use an override"
        assert aircraft.isdisjoint(self.active_flight), (
            "Aircraft already has a flight action in progress"
        )
        self.active_flight.update(aircraft)
        try:
            self.event(
                "tool_running",
                f"{arguments['drone_id']} · {name.replace('_', ' ')} running",
                name=name,
                arguments=arguments,
                call_id=call_id,
                source=source,
            )
            state = await self.fleet.execute("hold" if name == "stop" else name, arguments)
            if override:
                if name in ("hold", "stop"):
                    self.holds.add(arguments["drone_id"])
                else:
                    self.holds.discard(arguments["drone_id"])
            return {**compact_vehicle(state), "acknowledged_command": name}
        finally:
            self.active_flight.difference_update(aircraft)

    async def cancel_returns(self, names, reason):
        tasks = [self.cancel_return(name, reason) for name in names if name in self.return_tasks]
        if tasks:
            for result in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(result, Exception):
                    raise RuntimeError("Return cancellation failed: " + str(result)) from result

    def cancel_return(self, name, reason):
        task = self.return_tasks.pop(name)
        row = self.return_rows[name]
        row.update(status="stopped", waiting_for=None)
        self.return_event(row, "queued return cancelled: " + reason)
        task.cancel(reason)
        return task

    def return_event(self, row, text):
        self.event("mode", row["drone_id"] + ": " + text, result={"return": dict(row)})

    async def return_fleet(self, names, source):
        assert self.fleet_ready, "MAVLink fleet is unavailable"
        assert names and len(names) == len(set(names)) and set(names) <= set(NAMES), (
            "drone_ids must contain distinct known drones"
        )
        new = [name for name in names if name not in self.return_tasks]
        if not new:
            return {"returns": [dict(self.return_rows[name]) for name in names]}
        previous = self.plan_task
        self.plan_task = asyncio.current_task()
        try:
            self.cancel_wait("Coordinated fleet return")
            for name in new:
                self.inspections.invalidate("Coordinated fleet return", name)
            if source == "operator":
                await self.interrupt_agent("operator fleet return")
                self.conversation = []
            if previous is not None:
                previous.cancel()
                await asyncio.wait([previous])
            for name in new:
                await self.inspections.flight_stopped(name)
            self.plan_preview = None
            states = {state["id"]: state for state in self.vehicles()}
            for name in new:
                state = states[name]
                assert state["armed"] or state["landed_state"] == 1, name + ": invalid landed state"
                if state["armed"] and state["mode"] != "RTL":
                    self.fleet.check_return(state)
            for name in new:
                state = states[name]
                if state["armed"] and state["mode"] != "RTL":
                    await self.fleet.execute("hold", {"drone_id": name})
            approaches = await self.fleet.prepare_returns(
                [name for name in new if states[name]["armed"] and states[name]["mode"] != "RTL"]
            )
            for name in new:
                state = states[name]
                row = {
                    "drone_id": name,
                    "status": "queued" if state["armed"] else "landed",
                    "waiting_for": None,
                    "error": None,
                    "pending": False,
                }
                if state["armed"] and state["mode"] == "RTL":
                    row["status"] = "returning"
                self.return_rows[name] = row
            rows = [self.return_rows[name] for name in names]
            for row in rows:
                name = row["drone_id"]
                if name in new and row["status"] != "landed":
                    value = approaches[name] if name in approaches else None
                    self.return_tasks[name] = self.launch(self.run_return(row, rows, value))
                    self.return_event(row, "coordinated return queued")
            return {"returns": [dict(row) for row in rows]}
        finally:
            if self.plan_task is asyncio.current_task():
                self.plan_task = None

    async def run_return(self, row, batch, approach):
        name = row["drone_id"]
        try:
            while True:
                state = next(v for v in self.vehicles() if v["id"] == name)
                assert not state["link_lost"] and state["ekf_healthy"], (
                    name + ": coordinated return telemetry or EKF unhealthy"
                )
                if not state["armed"] and state["landed_state"] == 1:
                    row.update(status="landed", waiting_for=None)
                    self.return_event(row, "returned and landed")
                    if all(item["status"] == "landed" for item in batch):
                        landed = ", ".join(item["drone_id"] for item in batch)
                        airborne = ", ".join(
                            v["id"]
                            for v in self.vehicles()
                            if v["armed"] and v["landed_state"] in (2, 3, 4)
                        )
                        self.event(
                            "agent",
                            landed
                            + " landed and disarmed."
                            + (" Still airborne: " + airborne + "." if airborne else ""),
                            source="agent",
                        )
                    return
                if row["status"] == "queued":
                    await self.fleet.start_return_approach(name, approach)
                    row["status"] = "climbing"
                    self.return_event(row, "climbing for the checked return approach")
                if row["status"] in ("climbing", "transiting"):
                    status = self.fleet.return_approach_status(name, approach)
                    if status == "arrived":
                        await self.fleet.execute("hold", {"drone_id": name})
                        row["status"] = "waiting"
                        self.return_event(row, "at the home approach; checking the landing column")
                    elif status != row["status"]:
                        row["status"] = status
                        self.return_event(row, "flying home on the checked return approach")
                if row["status"] == "waiting":
                    conflict = await self.fleet.return_step(
                        name,
                        [
                            other
                            for other in self.return_tasks
                            if self.return_rows[other]["status"]
                            in ("queued", "climbing", "transiting")
                        ],
                    )
                    if conflict is None:
                        row.update(status="returning", waiting_for=None)
                        self.return_event(row, "RTL acknowledged")
                    elif row["waiting_for"] != conflict["other_drone_id"]:
                        row["waiting_for"] = conflict["other_drone_id"]
                        self.return_event(
                            row, "holding until " + row["waiting_for"] + " clears the return route"
                        )
                elif row["status"] == "returning":
                    assert state["mode"] == "RTL", name + ": coordinated return was superseded"
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            if row["status"] != "stopped":
                row.update(status="stopped", waiting_for=None)
                self.return_event(row, "queued return cancelled")
            raise
        except Exception as error:
            row.update(status="failed", error=str(error), waiting_for=None, pending=True)
            self.return_event(row, "coordinated return failed: " + str(error))
            for other, task in tuple(self.return_tasks.items()):
                if other != name and self.return_rows[other]["status"] in ("queued", "waiting"):
                    self.cancel_return(other, "Coordinated return stopped after a failure")
            raise RuntimeError(name + ": coordinated return failed: " + str(error)) from error
        finally:
            if name in self.return_tasks and self.return_tasks[name] is asyncio.current_task():
                del self.return_tasks[name]

    async def invoke(self, name, arguments, source, call_id):
        self.event(
            "tool_call", name, name=name, arguments=arguments, call_id=call_id, source=source
        )
        task = asyncio.create_task(self.execute(name, arguments, source, call_id))
        if source == "operator":
            self.operator_tasks.add(task)
        try:
            await asyncio.wait([task])
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait([task])
            if source == "operator":
                self.operator_tasks.remove(task)
            error = None if task.cancelled() else task.exception()
            if task.cancelled():
                result = {"ok": False, "error": "Operator override cancelled plan application"}
            elif error is None:
                result = {"ok": True, "state": task.result()}
            else:
                result = {"ok": False, "error": operator_error(error)}
                if name in PLAN_TOOLS and isinstance(error, AssertionError):
                    rejected = (
                        error
                        if isinstance(error, PlanRejected)
                        else PlanRejected("compile", operator_error(error))
                    )
                    version = rejected.rejection["version"]
                    result["rejection"] = {
                        **rejected.rejection,
                        "version": requested_version(name, arguments)
                        if version is None
                        else version,
                    }
            self.event(
                "tool_result",
                name,
                name=name,
                arguments=arguments,
                result=result,
                call_id=call_id,
                source=source,
            )
            if name in PLAN_TOOLS:
                self.event(
                    "plan_version",
                    name,
                    name=name,
                    arguments=arguments,
                    result={
                        "trigger": self.plan_trigger,
                        "application_state": self.vehicles(),
                        **result,
                    },
                    call_id=call_id,
                    source=source,
                )
        return result

    def context(self):
        vehicles = self.vehicles()
        context = {
            "fence_version": self.fleet.fences["version"],
            "flight_constraints": flight_constraints(vehicles),
            "briefing": None if self.case is None else {"brief": self.case["report"]["brief"]},
            "planner": planning_context(self.planner_state(vehicles)),
            "inspections": self.inspections.snapshot(),
            "telemetry_at_utc_ms": time.time() * 1000,
            "current_vehicles": [planning_vehicle(v) for v in vehicles],
            "autopilot_alerts": list(self.autopilot_alerts.values()),
            "returns": list(self.return_rows.values()),
            "evidence": [
                {
                    "id": event["id"],
                    "timestamp_ms": event["timestamp_ms"],
                    "drone_id": event["result"]["drone_id"],
                    "captured_at_ms": event["result"]["latest"]["captured_at_ms"],
                    **event["arguments"],
                    "confirmation": next(
                        (
                            confirmation["result"]
                            for confirmation in self.events
                            if confirmation["kind"] == "evidence_confirmation"
                            and confirmation["result"]["evidence_id"] == event["id"]
                        ),
                        None,
                    ),
                }
                for event in self.events
                if event["kind"] == "evidence" and event["source"] == "agent"
            ],
            "latest_operator_instruction": next(
                (
                    {
                        key: event[key]
                        for key in (
                            "id",
                            "sequence",
                            "timestamp_ms",
                            "kind",
                            "text",
                            "name",
                            "arguments",
                        )
                    }
                    for event in reversed(self.events)
                    if event["kind"] == "operator"
                    or (event["source"] == "operator" and event["kind"] == "tool_call")
                ),
                None,
            ),
            "historical_operator_events": [
                {
                    "sequence": event["sequence"],
                    "timestamp_ms": event["timestamp_ms"],
                    "kind": event["kind"],
                    "text": event["text"],
                    "name": event["name"],
                    "arguments": event["arguments"],
                    "result": planning_result(event["name"], event["result"])
                    if event["kind"] == "tool_result"
                    else event["result"],
                }
                for event in self.events
                if event["source"] == "operator" and event["kind"] in ("tool_result", "mode")
            ][-8:],
        }
        return copy.deepcopy(context)

    def decision_state(self):
        inspections = []
        for row in self.inspections.snapshot():
            phase = ""
            if "rescue" in row:
                phase = row["rescue"]["phase"]
                if phase in ("waiting", "searching"):
                    phase = "survey"
            inspections.append((row["id"], row["status"], phase))
        return (
            self.fleet.fences["version"],
            sorted((v["id"], v["mode"], v["armed"], v["landed_state"]) for v in self.vehicles()),
            sorted(inspections),
            sorted(self.autopilot_alerts),
            sorted((name, row["status"], row["error"]) for name, row in self.return_rows.items()),
        )

    async def watch_plan(self, receipt):
        while self.plan_active is receipt:
            if not self.agent_busy and not self.operator_tasks and self.plan_task is None:
                vehicles = self.vehicles()
                rows = self.inspections.pending_events()
                alerts = [row for row in self.autopilot_alerts.values() if row["pending"]]
                returns = [row for row in self.return_rows.values() if row["pending"]]
                if rows or alerts or returns:
                    self.cancel_wait("Mission event received")
                    wake = {
                        "reason": "rescue" if rows else "autopilot" if alerts else "return",
                        "results": [{"inspection": row} for row in rows],
                    }
                    if alerts:
                        wake["alerts"] = [dict(row) for row in alerts]
                        for row in alerts:
                            row["pending"] = False
                    if returns:
                        wake["returns"] = [dict(row) for row in returns]
                        for row in returns:
                            row["pending"] = False
                    for row in rows:
                        self.inspections.acknowledge(row["id"])
                    failed = [
                        row["drone_id"]
                        for row in rows
                        if row["status"] == "failed"
                        and row["drone_id"] in receipt["missions"]
                        and self.unfinished_work(receipt, row["drone_id"])
                    ]
                    if failed and self.plan_preview is None and not self.approvals:
                        wake["proposal"] = await self.propose_reassignment(receipt, failed[0])
                    self.event(
                        "mode", "Wonder reviewing mission events", result=wake, source="agent"
                    )
                    self.agent_busy = True
                    self.agent_task = self.launch(self.agent(None, wake=wake))
                elif (
                    self.plan_preview is None
                    and self.proposal is None
                    and not self.approvals
                    and not self.holds
                    and not self.return_tasks
                    and "tasking" in receipt
                    and due(
                        receipt,
                        self.planner_state(vehicles)["progress"],
                        vehicles,
                        self.lead_s(receipt),
                    )
                ):
                    self.cancel_wait("Search expansion proposed")
                    wake = {"reason": "proposal", "proposal": await self.propose_expansion(receipt)}
                    self.event("mode", "Wonder reviewing a proposal", result=wake, source="agent")
                    self.agent_busy = True
                    self.agent_task = self.launch(self.agent(None, wake=wake))
            vehicles = [v for v in self.vehicles() if v["id"] in receipt["missions"]]
            if (
                vehicles
                and all(not v["armed"] and v["landed_state"] == 1 for v in vehicles)
                and not self.inspections.pending_events()
                and not any(row["pending"] for row in self.autopilot_alerts.values())
                and not any(row["pending"] for row in self.return_rows.values())
            ):
                return
            await asyncio.sleep(0.1)

    def lead_s(self, receipt):
        lead = receipt["tasking"]["expansion"]["lead_seconds"]
        return EXPANSION_LEAD_S if lead is None else lead

    def unfinished_work(self, receipt, name):
        progress = self.planner_state(self.vehicles())["progress"]
        if name not in progress or progress[name]["current_step"] is None:
            return False
        steps = next(b["steps"] for b in receipt["plan"]["drones"] if b["drone_id"] == name)
        return any(step["kind"] == "survey" for step in steps[progress[name]["current_step"] :])

    async def propose_expansion(self, receipt):
        """Compile the next band for the airborne fleet as the pending preview, or record why not.

        The result is stored once per active plan; a rejection is not retried, Wonder decides.
        """
        version = receipt["plan"]["version"] + 1
        vehicles = self.vehicles()
        tasking, searched, continuing = expansion_request(
            receipt, self.planner_state(vehicles)["progress"], vehicles
        )
        self.plan_task = asyncio.current_task()
        try:
            compiled = await self.fleet.preview_task(
                tasking, version, self.operator_points(), searched, continuing
            )
        except PlanRejected as error:
            error.rejection["version"] = version
            self.proposal = {"kind": "expansion", "version": version, "rejection": error.rejection}
            self.event(
                "plan_version", f"Plan {version} · expansion not possible", result=self.proposal
            )
            return dict(self.proposal)
        finally:
            if self.plan_task is asyncio.current_task():
                self.plan_task = None
        self.plan_preview = compiled
        band = next(b["index"] for b in compiled["region"]["bands"] if b["status"] == "planned")
        self.proposal = {"kind": "expansion", "version": version, "band_index": band}
        self.event(
            "plan_version",
            f"Plan {version} · expansion to band {band} proposed",
            result={**self.proposal, "receipt": compiled},
        )
        return {**self.proposal, "receipt": planning_receipt(compiled)}

    async def propose_reassignment(self, receipt, failed):
        """Compile a remaining plan that sends the failed drone home and hands its rows on."""
        version = receipt["plan"]["version"] + 1
        self.plan_task = asyncio.current_task()
        try:
            vehicles = self.vehicles()
            request = reassignment(
                receipt, self.planner_state(vehicles)["progress"], vehicles, failed
            )
            compiled = await self.fleet.preview_plan(request)
        except (PlanRejected, AssertionError) as error:
            rejected = (
                error
                if isinstance(error, PlanRejected)
                else PlanRejected("compile", operator_error(error))
            )
            rejected.rejection["version"] = version
            result = {"kind": "reassignment", "failed": failed, "rejection": rejected.rejection}
            self.event("plan_version", f"Plan {version} · reassignment not possible", result=result)
            return result
        finally:
            if self.plan_task is asyncio.current_task():
                self.plan_task = None
        self.plan_preview = compiled
        self.proposal = {"kind": "reassignment", "version": version, "failed": failed}
        self.event(
            "plan_version",
            f"Plan {version} · reassignment after {failed} sensing failure proposed",
            result={**self.proposal, "receipt": compiled},
        )
        return {**self.proposal, "receipt": planning_receipt(compiled)}

    async def interrupt_agent(self, reason):
        if self.agent_task is None:
            self.agent_busy = False
            return
        async with self.agent_lock:
            task = self.agent_task
            if task is None:
                return
            assert task is not asyncio.current_task(), "Wonder cannot interrupt its own turn"
            self.event("agent", f"Wonder interrupted by {reason}.", source="agent")
            task.cancel()
            await asyncio.wait([task])
            answered = {
                item["call_id"]
                for item in self.conversation
                if "type" in item and item["type"] == "function_call_output"
            }
            pending = [
                item
                for item in self.conversation
                if "type" in item
                and item["type"] == "function_call"
                and item["call_id"] not in answered
            ]
            for call in pending:
                self.conversation.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": encode(
                            {
                                "ok": False,
                                "error": f"Interrupted by {reason} before the agent received this tool "
                                "result; inspect live state before continuing.",
                            }
                        ),
                    }
                )
            self.agent_task = None
            self.agent_busy = False

    def cancel_wait(self, reason):
        if self.agent_wait is not None:
            self.event("mode", "Wonder wait cancelled: " + reason, result=self.agent_wait)
            self.agent_wait = None
        if self.wait_task is not None:
            self.wait_task.cancel()
            self.wait_task = None

    def schedule_wait(self, conditions, timeout_s, description):
        assert self.agent_wait is None, "Wonder already has a pending wait"
        assert description.strip(), "Wait description is required"
        assert math.isfinite(timeout_s) and 0 < timeout_s <= 65535, (
            "Wait timeout must be finite positive seconds"
        )
        vehicles = self.vehicles()
        validate_conditions(
            conditions, self.planner_state(vehicles), vehicles, self.inspections.snapshot()
        )
        self.agent_wait = {
            "description": description,
            "conditions": conditions,
            "deadline_ms": time.time() * 1000 + timeout_s * 1000,
        }
        self.event("mode", "Wonder waiting: " + description, result=self.agent_wait, source="agent")
        self.wait_task = self.launch(self.watch(self.agent_wait, time.monotonic() + timeout_s))
        return self.agent_wait

    async def watch(self, waiting, deadline):
        while self.agent_wait is waiting:
            if self.agent_busy or self.operator_tasks or self.plan_task is not None:
                await asyncio.sleep(0.1)
                continue
            vehicles = self.vehicles()
            result = ready(
                waiting["conditions"],
                self.planner_state(vehicles),
                vehicles,
                self.inspections.snapshot(),
                self.holds,
            )
            if result is None and time.monotonic() >= deadline:
                result = {"reason": "deadline", "description": waiting["description"]}
            if result is None:
                await asyncio.sleep(0.1)
                continue
            if result["reason"] == "condition":
                for item in result["results"]:
                    if "inspection" in item and "rescue" in item["inspection"]:
                        self.inspections.acknowledge(item["inspection"]["id"])
            self.agent_wait = None
            self.wait_task = None
            self.event(
                "mode", "Wonder resumed: " + waiting["description"], result=result, source="agent"
            )
            self.agent_busy = True
            self.agent_task = self.launch(self.agent(None, wake=result))
            return

    async def agent(self, text, *, wake=None):
        retire_images(self.conversation)
        if wake is None:
            content = [] if text is None else [{"type": "input_text", "text": text}]
        else:
            assert text is None
            content = [
                {
                    "type": "input_text",
                    "text": "Automatic mission event; continue the agreed task. "
                    + encode(wake_message(wake)),
                }
            ]
            if wake["reason"] in ("condition", "rescue"):
                for result in wake["results"]:
                    if "inspection" in result and result["inspection"]["latest"] is not None:
                        evidence = self.inspections.evidence(result["inspection"]["id"])
                        content.extend(
                            evidence_message(evidence, [*self.conversation, {"content": content}])[
                                "content"
                            ]
                        )
        if content:
            self.conversation.append({"role": "user", "content": content})
        assert self.conversation, "Wonder continuation requires conversation history"
        for turn in count():
            flight = self.plan_active
            decision = self.decision_state()
            trace = self.directory / f"response-{time.time_ns()}"
            call_started = time.monotonic()
            try:
                instructions = await asyncio.to_thread(
                    planning_instructions, AGENT_INSTRUCTIONS, self.landmarks
                )
                live = await asyncio.to_thread(live_context, self.context(), self.landmarks)
            except Exception as error:
                raise RuntimeError(
                    "Wonder context preparation failed: " + operator_error(error)
                ) from error
            context_preparation_s = time.monotonic() - call_started
            task = asyncio.create_task(
                asyncio.to_thread(
                    self.model.respond,
                    None,
                    [],
                    self.effort,
                    TOOLS,
                    instructions=instructions,
                    trace_path=trace,
                    history=copy.deepcopy((*self.conversation, live)),
                    cache_key=self.id,
                )
            )
            try:
                await asyncio.wait([task])
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.wait([task])
            error = task.exception()
            if error is not None:
                diagnostic = {
                    "trace": str(trace.relative_to(self.directory)),
                    "exception_type": type(error).__name__,
                    "traceback": "".join(traceback.format_exception(error)),
                    "context_preparation_s": context_preparation_s,
                    "input_tokens": None,
                    "turn": turn,
                    "history_items": len(self.conversation),
                }
                if trace.with_suffix(".http.json").is_file():
                    diagnostic.update(json.loads(trace.with_suffix(".http.json").read_text()))
                self.event("error", operator_error(error), result=diagnostic, source="agent")
                return
            response = task.result()
            self.event(
                "model_usage",
                "Wonder response received",
                source="agent",
                result={
                    "response_id": response["id"],
                    "turn": turn,
                    "elapsed_s": time.monotonic() - call_started,
                    "context_preparation_s": context_preparation_s,
                    "input_tokens": response["usage"]["input_tokens"],
                    "cached_tokens": response["usage"]["input_tokens_details"]["cached_tokens"],
                    "output_tokens": response["usage"]["output_tokens"],
                    "reasoning_tokens": response["usage"]["output_tokens_details"][
                        "reasoning_tokens"
                    ],
                    "request_body_bytes": trace.with_suffix(".request.json").stat().st_size,
                    "trace": str(trace.relative_to(self.directory)),
                },
            )
            if self.plan_active is not flight:
                self.event(
                    "mode",
                    "Flight plan changed while Wonder was thinking; response discarded without executing tools.",
                    source="agent",
                )
                return
            calls = [
                (item, json.loads(item["arguments"]))
                for item in response["output"]
                if item["type"] == "function_call"
            ]
            assert all(call["name"] != "wait_for" for call, _ in calls[:-1]), (
                "wait_for must be the last tool call in a response"
            )
            changed = decision != self.decision_state()
            overrides = {"hold", "stop", "return_home", "return_fleet", "land"}
            current_state_tools = overrides | {
                "get_fleet",
                "get_drone",
                "get_mission",
                "find_place",
                "describe_location",
                "ground_height",
                "ground_profile",
                "wait_for",
                "publish_evidence",
                "confirm_evidence",
                "get_candidate",
                "follow_candidate",
                "disregard_and_continue",
                "set_camera_view",
            }
            has_overrides = any(call["name"] in overrides for call, _ in calls)
            if (
                changed
                and not has_overrides
                and not (calls and all(call["name"] in current_state_tools for call, _ in calls))
            ):
                self.event(
                    "mode",
                    "Aircraft or inspection state changed while Wonder was thinking; "
                    "outdated response discarded without executing tools.",
                    result={"state_before": decision, "state_after": self.decision_state()},
                    source="agent",
                )
                if wake is None:
                    self.event(
                        "agent",
                        "Mission state changed before that decision completed; no command was sent.",
                        source="agent",
                    )
                return
            self.conversation.extend(response["output"])
            for item in response["output"]:
                if item["type"] == "message" and not changed:
                    for content in item["content"]:
                        if content["type"] == "output_text":
                            self.event("agent", content["text"], source="agent")
            if not calls:
                return
            for call, arguments in calls:
                if self.plan_active is not flight:
                    result = {
                        "ok": False,
                        "error": "Flight plan changed during this response; command cancelled",
                    }
                    self.event("mode", result["error"], source="agent")
                elif call["name"] not in current_state_tools and (
                    has_overrides or decision != self.decision_state()
                ):
                    result = {
                        "ok": False,
                        "error": (
                            "Immediate overrides must finish before replanning"
                            if has_overrides
                            else "Aircraft or inspection state changed during this tool batch"
                        )
                        + "; read current state before issuing this decision.",
                    }
                    self.event(
                        "tool_result",
                        call["name"],
                        name=call["name"],
                        arguments=arguments,
                        result=result,
                        call_id=call["call_id"],
                        source="agent",
                    )
                else:
                    result = await self.invoke(call["name"], arguments, "agent", call["call_id"])
                    if call["name"] in ("request_launch", "apply_plan_change") and result["ok"]:
                        flight = self.plan_active
                        decision = self.decision_state()
                self.conversation.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": encode(planning_result(call["name"], result)),
                    }
                )
                if call["name"] == "get_candidate" and result["ok"]:
                    evidence = self.inspections.evidence(
                        arguments["inspection_id"], result["state"]["frame"]["frame_id"]
                    )
                    self.conversation.append(evidence_message(evidence, self.conversation))
                if call["name"] == "wait_for" and result["ok"]:
                    return
            if self.plan_active is not flight:
                return

    async def handle(self, message, websocket):
        assert not self.clearing, "Mission is ending; no new commands are accepted"
        assert isinstance(message, dict) and "type" in message, "Expected a typed message object"
        kind = message["type"]
        assert kind in MESSAGE_FIELDS, f"Unknown message type: {kind}"
        assert set(message) == MESSAGE_FIELDS[kind], f"Invalid fields for {kind}"
        if kind in ("start", "chat", "tool"):
            # Mission work opens a general mission when none is running.
            await self.begin(None)
        if kind == "video_diagnostic":
            record_video_diagnostic(self.directory, message["sample"])
        elif kind == "chat":
            assert isinstance(message["text"], str) and 1 <= len(message["text"].strip()) <= 4000
            self.cancel_wait("New operator message")
            conversation = self.conversation
            await self.interrupt_agent("new operator message")
            assert not self.clearing and self.conversation is conversation, (
                "Mission changed while interrupting Wonder; chat was not applied"
            )
            self.agent_busy = True
            self.event("operator", message["text"], source="operator")
            self.conversation.append(
                {"role": "user", "content": [{"type": "input_text", "text": message["text"]}]}
            )
            self.agent_task = self.launch(self.agent(None))
        elif kind == "new_chat":
            assert self.chat_ready(self.vehicles()), (
                "A new chat requires every drone landed and disarmed at its home pad"
            )
            reset = max(
                (e["sequence"] for e in self.events if e["kind"] == "chat_reset"), default=0
            )
            assert any(e["kind"] == "operator" and e["sequence"] > reset for e in self.events), (
                "The current chat has no operator message"
            )
            self.cancel_wait("New chat")
            conversation = self.conversation
            await self.interrupt_agent("a new chat")
            assert not self.clearing and self.conversation is conversation, (
                "Mission changed while interrupting Wonder; new chat was not started"
            )
            self.conversation = []
            self.event("chat_reset", "Operator started a new chat", source="operator")
        elif kind == "tool":
            self.launch(
                self.invoke(message["name"], message["arguments"], "operator", str(uuid.uuid4()))
            )
        elif kind == "approval":
            assert not self.fence_change, "Flight fences changed; the earlier approval is invalid"
            assert type(message["approved"]) is bool
            assert message["id"] in self.approvals, "Unknown or already resolved approval"
            item = self.approvals[message["id"]]
            assert not item["future"].done(), "Approval already resolved"
            self.event(
                "approval",
                f"{item['card']['name'].replace('_', ' ')} "
                + ("approved by operator" if message["approved"] else "declined by operator"),
                result={"id": message["id"], "approved": message["approved"]},
                name=item["card"]["name"],
                arguments=item["card"]["arguments"],
                call_id=item["call_id"],
                source="operator",
            )
            item["future"].set_result(message["approved"])
        elif kind == "set_fences":
            fences = await self.set_fences(message["fences"])
            await websocket.send(
                encode({"type": "fence_result", "version": fences["version"], "error": None})
            )
        elif kind == "reset_batteries":
            assert self.fleet_ready, "MAVLink fleet is unavailable"
            assert self.chat_ready(self.vehicles()), (
                "Battery reset needs every drone landed and disarmed at its home pad"
            )
            await self.fleet.reset_batteries()
        elif kind == "export_plan":
            assert message["drone_id"] in NAMES, "Unknown drone: " + message["drone_id"]
            assert self.fleet_ready, "MAVLink fleet is unavailable"
            plan = self.fleet.export_plan(message["drone_id"])
            await websocket.send(
                encode({"type": "plan", "drone_id": message["drone_id"], "plan": plan})
            )
        elif kind == "set_effort":
            assert message["effort"] in EFFORTS, "Unknown reasoning effort"
            self.effort = message["effort"]
            self.event("mode", "Reasoning effort set to " + self.effort, source="operator")
        elif kind == "end_mission":
            await self.end_mission()
        elif kind != "start":
            raise AssertionError("Mission is already connected")

    async def connection(self, websocket, first=None):
        assert not self.clearing, "Mission is ending; no new connections are accepted"
        assert self.admission_error is None, "Mission admission failed: " + str(
            self.admission_error
        )
        if first is None:
            first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        assert set(first) == MESSAGE_FIELDS["connect"] and first["type"] == "connect"
        if first["case_id"] is not None:
            assert self.case is not None and first["case_id"].lower() == self.case["id"].lower(), (
                "Service is running a different mission"
            )

        async def publish():
            run = focus_id = plans = None
            while True:
                # Starting or ending a mission replaces the run and its event history.
                if run != self.id:
                    run, events, sent = self.id, self.events, len(self.events)
                    await websocket.send(
                        encode({"type": "history", "events": events, "landmarks": self.landmarks})
                    )
                while sent < len(events):
                    await websocket.send(encode({"type": "event", "event": events[sent]}))
                    sent += 1
                current = (id(self.plan_preview), id(self.plan_active), id(self.plan_previous))
                if current != plans:
                    plans = current
                    await websocket.send(encode(self.plans()))
                await websocket.send(encode(self.state()))
                if self.feed_focus is not None and self.feed_focus["id"] != focus_id:
                    await websocket.send(encode(self.feed_focus))
                    focus_id = self.feed_focus["id"]
                await asyncio.sleep(0.1)

        async def consume():
            async for raw in websocket:
                message = json.loads(raw)
                task = asyncio.create_task(self.handle(message, websocket))
                await asyncio.wait([task])
                error = task.exception()
                if error is not None:
                    text = operator_error(error)
                    if message["type"] == "set_fences":
                        self.event(
                            "mode", "Flight fences were not applied: " + text, source="operator"
                        )
                        await websocket.send(
                            encode(
                                {
                                    "type": "fence_result",
                                    "version": self.fleet.fences["version"],
                                    "error": text,
                                }
                            )
                        )
                    else:
                        self.event("error", text)
                        await websocket.send(encode({"type": "error", "message": text}))

        tasks = [asyncio.create_task(publish()), asyncio.create_task(consume())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.wait(tasks)
            for task in tasks:
                if not task.cancelled() and task.exception() is not None:
                    raise task.exception()

    async def close(self):
        self.cancel_wait("Mission service shutdown")
        self.inspections.invalidate("Mission service shutdown")
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            results = await asyncio.gather(*self.tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    self.event("error", "Shutdown task: " + operator_error(result))
        try:
            await self.inspections.close()
        finally:
            if not self.fleet_closed:
                await self.fleet.close()
                self.fleet_closed = True
            await asyncio.to_thread(self.model.close)
            self.log.close()

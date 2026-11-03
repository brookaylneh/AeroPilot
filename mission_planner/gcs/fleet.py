"""Four direct ArduCopter processes and one MAVLink owner for each aircraft."""

import argparse
import asyncio
import copy
import json
import math
import os
import signal
import time
from collections import deque
from pathlib import Path

os.environ["MAVLINK20"] = "1"
from pymavlink import mavutil  # noqa: E402

from mission_planner.environment import load_env  # noqa: E402
from mission_planner.gcs.battery import (  # noqa: E402
    CUTOFF_PERCENT,
    LOW_VOLTAGE_V,
    VOLTAGE_FLOOR_PERCENT,
    available_seconds,
)
from mission_planner.gcs.fences import (  # noqa: E402
    default_fences,
    fence_items,
    read_fences,
    rtl_envelope,
    validate_fences,
)
from mission_planner.gcs.geodesy import (  # noqa: E402
    MAX_ALTITUDE_M,
    MIN_ALTITUDE_M,
    NAMES,
    ORIGIN,
    rtl_route,
    to_geodetic,
    to_local,
)
from mission_planner.gcs.planner import (  # noqa: E402
    mission_home,
    mission_item,
    mission_readback,
    validate_missions,
)
from mission_planner.gcs.rejection import separation
from mission_planner.gcs.sitl import check_ports, launches  # noqa: E402
from mission_planner.gcs.tracking import MAX_TRACKING_AGE_S
from mission_planner.gcs.validation import (  # noqa: E402
    CLEARANCE_M,
    separation_conflict,
    validate_ground_column,
    validate_position,
    validate_route,
    validate_separation,
)
from world.cosys.landmarks import load as load_landmarks  # noqa: E402
from world.cosys.terrain import terrain_height  # noqa: E402

# SIM_BATT_VOLTAGE of a full simulator battery (gcs-sitl.json expected parameters).
FULL_BATTERY_V = 12.6
MAV = mavutil.mavlink
RATES = {0: 2, 1: 5, 24: 5, 33: 10, 42: 5, 193: 5, 245: 5, 242: 1}
STOPPED_SPEED_MPS = 0.2
REQUIRED = (
    "HEARTBEAT",
    "SYS_STATUS",
    "GPS_RAW_INT",
    "GLOBAL_POSITION_INT",
    "EKF_STATUS_REPORT",
    "EXTENDED_SYS_STATE",
    "HOME_POSITION",
)


def course_degrees(north_cm_s, east_cm_s):
    assert all(type(v) is int and -32768 <= v <= 32767 for v in (north_cm_s, east_cm_s)), (
        "GLOBAL_POSITION_INT horizontal velocity must be int16 centimetres per second"
    )
    if math.hypot(north_cm_s, east_cm_s) / 100 < STOPPED_SPEED_MPS:
        return None
    return math.degrees(math.atan2(east_cm_s, north_cm_s)) % 360


def remaining_route(state, route):
    if not state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND:
        return [state]
    mode = state["mode"]
    if mode in ("BRAKE", "LOITER"):
        return [state]
    if mode == "AUTO":
        sequence = state["mission_current"]
        assert sequence is not None, state["id"] + ": mission progress is unavailable"
        return [state, *(point for point in state["mission"] if point["seq"] >= sequence)]
    assert mode in ("GUIDED", "RTL", "LAND"), state["id"] + ": untracked flight mode " + mode
    assert route, state["id"] + ": active route reservation is unavailable"
    if len(route) == 1:
        return [state]
    keys = ("north_m", "east_m", "altitude_above_home_m")
    assert all(math.isfinite(point[key]) for point in [state, *route] for key in keys), (
        state["id"] + ": reservation coordinates must be finite metres"
    )
    if mode == "RTL" and state["landed_state"] == MAV.MAV_LANDED_STATE_LANDING:
        return [state, route[-1]]
    return [state, *route]


def with_kept(receipt, kept):
    """The receipt plus airborne drones outside the plan that keep flying: their branches and
    missions, and their search policy when the plan has none of its own."""
    if not kept:
        return receipt
    plan = {**receipt["plan"], "drones": [*receipt["plan"]["drones"], *[k["branch"] for k in kept]]}
    searches = [k["search"] for k in kept if k["search"] is not None]
    if "search" not in plan and searches:
        plan["search"] = searches[0]
    missions = {**receipt["missions"], **{k["drone_id"]: k["mission"] for k in kept}}
    return {**receipt, "plan": plan, "missions": missions}


class Fleet:
    def __init__(self, directory, on_event, landmarks):
        self.directory = Path(directory).resolve()
        self.on_event = on_event
        self.vehicles = {}
        self.tasks = []
        self.command_lock = asyncio.Lock()
        self.planned_routes = {}
        self.control_versions = dict.fromkeys(NAMES, 0)
        self.landmarks = landmarks
        self.fences = default_fences()
        self.fences_verified = False
        self.fence_error = None
        self.fences_path = Path(os.environ["SNR_FENCES_PATH"])

    async def start(self):
        load_env()
        if self.fences_path.exists():
            self.fences = validate_fences(json.loads(self.fences_path.read_text()), {})
        self.directory.mkdir(parents=True, exist_ok=False)
        aircraft = launches(self.directory)
        preflight = check_ports([port for launch in aircraft for port in launch["ports"]])
        self.on_event({"kind": "sitl_ports_verified", **preflight})
        for index, launch in enumerate(aircraft):
            name, home, args = (launch[key] for key in ("name", "home", "arguments"))
            directory = self.directory / name
            directory.mkdir()
            connection = mavutil.mavlink_connection(
                f"udpin:127.0.0.1:{launch['mavlink_port']}",
                source_system=255,
                source_component=190,
                dialect="ardupilotmega",
            )
            logfile = (directory / "autopilot.log").open("w")
            process = await asyncio.create_subprocess_exec(
                *args, cwd=directory, stdout=logfile, stderr=asyncio.subprocess.STDOUT
            )
            self.vehicles[name] = {
                "connection": connection,
                "process": process,
                "logfile": logfile,
                "messages": {},
                "bootloader_preamble_count": 0,
                "times": {},
                "pending": [],
                "positions": deque(maxlen=200),
                "mission": [],
                "mission_speed_mps": None,
                "camera_available": False,
                "fence": [],
                "safety_parameters": {},
                "expected_parameters": launch["expected_parameters"],
                "status_text": [],
                "home": home,
                "sysid": index + 1,
                "started": time.monotonic(),
            }
            self.tasks.append(asyncio.create_task(self._receive(name), name=f"mavlink-{name}"))
            self.on_event(
                {
                    "kind": "autopilot_started",
                    "drone_id": name,
                    "pid": process.pid,
                    "arguments": args,
                }
            )
        await asyncio.gather(*(self._initialize(name) for name in NAMES))
        await asyncio.sleep(3)
        for state in self.snapshot():
            assert state["update_rate_hz"] >= 5, (state["id"], state["update_rate_hz"])
            self.planned_routes[state["id"]] = [state]
        validate_fences(self.fences, {s["id"]: s["home"] for s in self.snapshot()})
        self.fences_verified = True

    async def _receive(self, name):
        vehicle = self.vehicles[name]
        connection = vehicle["connection"]
        heartbeat_at = 0
        while True:
            assert vehicle["process"].returncode is None, f"{name} autopilot exited"
            now = time.monotonic()
            if now - heartbeat_at >= 1:
                connection.mav.heartbeat_send(MAV.MAV_TYPE_GCS, MAV.MAV_AUTOPILOT_INVALID, 0, 0, 0)
                heartbeat_at = now
            while (message := connection.recv_match(blocking=False)) is not None:
                kind = message.get_type()
                if vehicle["bootloader_preamble_count"] < 3:
                    # Pinned GCS_Common.cpp sends three SiK reset words before MAVLink.
                    assert kind == "BAD_DATA" and message.data in (b"0 ", b"0 0 ", b"0 0 0 "), (
                        f"{name}: unexpected serial bootstrap: {message}"
                    )
                    vehicle["bootloader_preamble_count"] += len(message.data) // 2
                    assert vehicle["bootloader_preamble_count"] <= 3
                    continue
                if kind == "BAD_DATA":
                    raise ValueError(f"{name}: invalid MAVLink frame: {message.to_dict()}")
                assert message.get_srcSystem() == vehicle["sysid"], f"Wrong SYSID on {name}"
                if message.get_srcComponent() != MAV.MAV_COMP_ID_AUTOPILOT1:
                    continue
                if (
                    kind == "HEARTBEAT"
                    and "HEARTBEAT" in vehicle["messages"]
                    and vehicle["messages"]["HEARTBEAT"].base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED
                    and not message.base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED
                ):
                    vehicle["camera_available"] = False
                vehicle["messages"][kind] = message
                vehicle["times"][kind] = time.monotonic()
                if kind == "GLOBAL_POSITION_INT":
                    vehicle["positions"].append((time.monotonic(), time.time() * 1000))
                if kind == "STATUSTEXT":
                    event = {
                        "kind": "statustext",
                        "drone_id": name,
                        "text": message.text,
                        "severity": message.severity,
                        "at_ms": time.time() * 1000,
                    }
                    vehicle["status_text"].append(event)
                    del vehicle["status_text"][:-20]
                    self.on_event(event)
                if kind == "HEARTBEAT":
                    mode = mavutil.mode_string_v10(message)
                    if "mode" not in vehicle or vehicle["mode"] != mode:
                        if mode == "RTL":
                            state = next(s for s in self.snapshot() if s["id"] == name)
                            self.planned_routes[name] = rtl_route(state)
                        self.on_event({"kind": "mode", "drone_id": name, "mode": mode})
                    vehicle["mode"] = mode
                for pending in list(vehicle["pending"]):
                    expected, predicate, future = pending
                    if kind == expected and predicate(message) and not future.done():
                        future.set_result(message)
                        vehicle["pending"].remove(pending)
            await asyncio.sleep(0.005)

    async def _expect(self, name, kind, predicate, send, timeout=10):
        vehicle = self.vehicles[name]
        future = asyncio.get_running_loop().create_future()
        pending = (kind, predicate, future)
        vehicle["pending"].append(pending)
        try:
            send()
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as error:
            raise TimeoutError(f"{name}: timed out waiting for {kind}") from error
        finally:
            if pending in vehicle["pending"]:
                vehicle["pending"].remove(pending)

    async def _until(self, name, predicate, timeout):
        deadline = time.monotonic() + timeout
        while not predicate():
            self._check_tasks()
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{name}: timed out waiting for requested vehicle state")
            await asyncio.sleep(0.05)

    def _check_tasks(self):
        for task in self.tasks:
            if task.done():
                task.result()
                raise RuntimeError("MAVLink receiver stopped")

    async def _command(self, name, command, params):
        assert len(params) == 7
        vehicle = self.vehicles[name]
        reply = await self._expect(
            name,
            "COMMAND_ACK",
            lambda m: m.command == command,
            lambda: vehicle["connection"].mav.command_long_send(
                vehicle["sysid"], 1, command, 0, *params
            ),
        )
        assert reply.result == MAV.MAV_RESULT_ACCEPTED, (
            f"{name}: command {command} rejected, MAV_RESULT={reply.result}"
        )

    async def _parameter(self, name, parameter, value):
        vehicle = self.vehicles[name]
        reply = await self._expect(
            name,
            "PARAM_VALUE",
            lambda m: m.param_id == parameter,
            lambda: vehicle["connection"].mav.param_set_send(
                vehicle["sysid"], 1, parameter.encode(), value, MAV.MAV_PARAM_TYPE_REAL32
            ),
        )
        assert math.isclose(reply.param_value, value, abs_tol=1e-5), (parameter, reply.param_value)
        self.on_event(
            {"kind": "parameter", "drone_id": name, "name": parameter, "value": reply.param_value}
        )

    async def _initialize(self, name):
        vehicle = self.vehicles[name]
        await self._until(name, lambda: "HEARTBEAT" in vehicle["messages"], 30)
        await self._command(name, MAV.MAV_CMD_REQUEST_MESSAGE, [148, 0, 0, 0, 0, 0, 0])
        await self._until(name, lambda: "AUTOPILOT_VERSION" in vehicle["messages"], 10)
        version = vehicle["messages"]["AUTOPILOT_VERSION"]
        assert version.flight_sw_version == 0x040702FF, "Requires official ArduCopter 4.7.2"
        assert bytes(version.flight_custom_version).decode("ascii") == "fb773567", (
            "Unexpected ArduCopter commit"
        )
        self.on_event({"kind": "firmware_verified", "drone_id": name, "version": version.to_dict()})
        for message_id, hz in RATES.items():
            await self._command(
                name, MAV.MAV_CMD_SET_MESSAGE_INTERVAL, [message_id, 1e6 / hz, 0, 0, 0, 0, 0]
            )
        await self._until(name, lambda: all(k in vehicle["messages"] for k in REQUIRED), 60)
        await self._until(
            name,
            lambda: (
                vehicle["messages"]["GPS_RAW_INT"].fix_type >= 3
                and self._ekf_healthy(vehicle["messages"]["EKF_STATUS_REPORT"])
            ),
            60,
        )
        home = vehicle["messages"]["HOME_POSITION"]
        north, east = to_local(home.latitude / 1e7, home.longitude / 1e7)
        home_error_m = math.dist(
            (north, east), (vehicle["home"]["north_m"], vehicle["home"]["east_m"])
        )
        self.on_event(
            {
                "kind": "home_comparison",
                "drone_id": name,
                "intended": vehicle["home"],
                "actual": {
                    "latitude_deg": home.latitude / 1e7,
                    "longitude_deg": home.longitude / 1e7,
                    "altitude_msl_m": home.altitude / 1000,
                    "north_m": north,
                    "east_m": east,
                },
                "home_error_m": home_error_m,
            }
        )
        assert home_error_m < 1, (
            f"{name}: HOME_POSITION differs from configured spawn by {home_error_m:.6f} m"
        )
        for parameter, expected in vehicle["expected_parameters"].items():
            reply = await self._expect(
                name,
                "PARAM_VALUE",
                lambda m, parameter=parameter: m.param_id == parameter,
                lambda: vehicle["connection"].mav.param_request_read_send(
                    vehicle["sysid"], 1, parameter.encode(), -1
                ),
            )
            assert math.isclose(reply.param_value, expected, abs_tol=1e-5), (
                name,
                parameter,
                reply.param_value,
                expected,
            )
            vehicle["safety_parameters"][parameter] = reply.param_value
        self.on_event(
            {
                "kind": "safety_parameters_verified",
                "drone_id": name,
                "values": vehicle["safety_parameters"],
            }
        )
        envelope = rtl_envelope({"altitude_msl_m": home.altitude / 1000}, self.landmarks)
        assert envelope["clearance_m"] >= CLEARANCE_M, (
            f"{name}: RTL terrain envelope clears only {envelope['clearance_m']:.3f} m"
        )
        self.on_event({"kind": "rtl_envelope_verified", "drone_id": name, **envelope})
        await self._upload_fences(name, fence_items(self.fences))
        await self._parameter(name, "FENCE_TYPE", 5)
        await self._parameter(name, "FENCE_ENABLE", 1)
        vehicle["mission"] = await self._download(name, MAV.MAV_MISSION_TYPE_MISSION)

    async def _upload_fences(self, name, items):
        await self._upload(name, items, MAV.MAV_MISSION_TYPE_FENCE)
        received = await self._download(name, MAV.MAV_MISSION_TYPE_FENCE)
        read_fences(received)
        self.vehicles[name]["fence"] = received
        assert len(received) == len(items), f"{name}: fence readback count differs"
        for index, (sent, actual) in enumerate(zip(items, received)):
            for key in ("command", "param1", "x", "y"):
                assert sent[key] == actual[key], f"{name}: fence vertex {index + 1} {key} differs"
        assert read_fences(received) == read_fences(items), f"{name}: fence polygons differ"

    async def set_fences(self, requested):
        async with self.command_lock:
            assert requested["version"] == self.fences["version"], (
                "Flight fences changed in another session; reopen the editor"
            )
            states = self.snapshot()
            assert len(states) == len(NAMES) and all(
                not s["armed"]
                and s["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND
                and not s["link_lost"]
                and s["ekf_healthy"]
                and s["gps_fix"] >= 3
                for s in states
            ), "Saving flight fences requires four healthy, landed and disarmed drones"
            checked = validate_fences(requested, {s["id"]: s["home"] for s in states})
            validate_fences(checked, {s["id"]: s for s in states})
            self.fences = {**checked, "version": checked["version"] + 1}
            self.fences_verified = False
            self.fence_error = None
            self.on_event({"kind": "fences", "stage": "uploading", "fences": self.fences})
            try:
                items = fence_items(checked)
                for name in NAMES:
                    await self._upload_fences(name, items)
                temporary = self.fences_path.with_suffix(".new")
                with temporary.open("x") as stream:
                    json.dump(self.fences, stream, allow_nan=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.fences_path)
            except Exception as error:
                self.fence_error = (
                    f"Flight fence save failed; launch is locked: {type(error).__name__}: {error}"
                )
                self.on_event(
                    {
                        "kind": "fences",
                        "stage": "failed",
                        "fences": self.fences,
                        "error": self.fence_error,
                    }
                )
                raise RuntimeError(self.fence_error) from error
            self.fences_verified = True
            self.on_event({"kind": "fences", "stage": "verified", "fences": self.fences})
            return self.fences

    @staticmethod
    def _ekf_healthy(message):
        required = (
            MAV.EKF_ATTITUDE | MAV.EKF_VELOCITY_HORIZ | MAV.EKF_POS_HORIZ_ABS | MAV.EKF_POS_VERT_ABS
        )
        return message.flags & required == required and not message.flags & (
            MAV.EKF_CONST_POS_MODE | MAV.EKF_UNINITIALIZED
        )

    async def _upload(self, name, items, mission_type):
        assert items
        vehicle = self.vehicles[name]
        mav, sysid = vehicle["connection"].mav, vehicle["sysid"]
        request = await self._expect(
            name,
            "MISSION_REQUEST",
            lambda m: m.mission_type == mission_type,
            lambda: mav.mission_count_send(sysid, 1, len(items), mission_type),
        )
        for seq, item in enumerate(items):
            assert request.seq == seq, f"Unexpected mission request {request.seq}, expected {seq}"

            def send():
                mav.mission_item_int_send(
                    sysid,
                    1,
                    seq,
                    item["frame"],
                    item["command"],
                    0,
                    1,
                    item["param1"],
                    item["param2"],
                    item["param3"],
                    item["param4"],
                    item["x"],
                    item["y"],
                    item["z"],
                    mission_type,
                )

            kind = "MISSION_ACK" if seq == len(items) - 1 else "MISSION_REQUEST"
            request = await self._expect(name, kind, lambda m: m.mission_type == mission_type, send)
        assert request.type == MAV.MAV_MISSION_ACCEPTED, (
            f"{name}: mission upload rejected {request.type}"
        )

    async def _download(self, name, mission_type):
        vehicle = self.vehicles[name]
        mav, sysid = vehicle["connection"].mav, vehicle["sysid"]
        count = await self._expect(
            name,
            "MISSION_COUNT",
            lambda m: m.mission_type == mission_type,
            lambda: mav.mission_request_list_send(sysid, 1, mission_type),
        )
        items = []
        for seq in range(count.count):
            item = await self._expect(
                name,
                "MISSION_ITEM_INT",
                lambda m: m.mission_type == mission_type and m.seq == seq,
                lambda: mav.mission_request_int_send(sysid, 1, seq, mission_type),
            )
            items.append(item.to_dict())
        mav.mission_ack_send(sysid, 1, MAV.MAV_MISSION_ACCEPTED, mission_type)
        return items

    async def clear_missions(self):
        async with self.command_lock:
            states = self.snapshot()
            assert all(
                not v["armed"] and v["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND
                for v in states
            ), "Land the fleet before clearing missions"
            for name, vehicle in self.vehicles.items():
                reply = await self._expect(
                    name,
                    "MISSION_ACK",
                    lambda message: message.mission_type == MAV.MAV_MISSION_TYPE_MISSION,
                    lambda: vehicle["connection"].mav.mission_clear_all_send(
                        vehicle["sysid"], 1, MAV.MAV_MISSION_TYPE_MISSION
                    ),
                )
                assert reply.type == MAV.MAV_MISSION_ACCEPTED, f"{name}: mission clear rejected"
                items = await self._download(name, MAV.MAV_MISSION_TYPE_MISSION)
                assert items == [], f"{name}: mission clear readback is not empty"
                vehicle["mission"] = items
                vehicle["mission_speed_mps"] = None
                vehicle["camera_available"] = False
            self.planned_routes = {state["id"]: [state] for state in self.snapshot()}

    def snapshot(self):
        self._check_tasks()
        result = []
        for name, vehicle in self.vehicles.items():
            messages = vehicle["messages"]
            assert all(k in messages for k in REQUIRED), f"{name} telemetry not initialized"
            heartbeat, position, gps = (
                messages[k] for k in ("HEARTBEAT", "GLOBAL_POSITION_INT", "GPS_RAW_INT")
            )
            north, east = to_local(position.lat / 1e7, position.lon / 1e7)
            assert abs(north) < 10000 and abs(east) < 10000, f"{name}: position outside local frame"
            assert 0 <= gps.fix_type <= 8 and 0 <= gps.satellites_visible <= 255
            assert position.hdg == 65535 or 0 <= position.hdg < 36000
            assert type(vehicle["camera_available"]) is bool
            assert -1 <= messages["SYS_STATUS"].battery_remaining <= 100
            samples = list(vehicle["positions"])
            hz = (len(samples) - 1) / (samples[-1][0] - samples[0][0]) if len(samples) >= 2 else 0
            age = time.monotonic() - vehicle["times"]["HEARTBEAT"]
            if time.monotonic() - vehicle["times"]["GLOBAL_POSITION_INT"] > 2:
                hz = 0

            def points(items):
                result = []
                for item in items:
                    if item["command"] == MAV.MAV_CMD_DO_CHANGE_SPEED:
                        continue
                    n, e = to_local(item["x"] / 1e7, item["y"] / 1e7)
                    altitude = item["z"]
                    if item["frame"] == MAV.MAV_FRAME_GLOBAL:
                        altitude -= home_message.altitude / 1000
                    if item["command"] == MAV.MAV_CMD_NAV_LAND:
                        altitude = terrain_height(n, e) - (
                            home_message.altitude / 1000 - ORIGIN["altitude_msl_m"]
                        )
                    result.append(
                        {
                            "seq": item["seq"],
                            "north_m": n,
                            "east_m": e,
                            "latitude_deg": item["x"] / 1e7,
                            "longitude_deg": item["y"] / 1e7,
                            "altitude_above_home_m": altitude,
                            "command": item["command"],
                        }
                    )
                return result

            home_message = messages["HOME_POSITION"]
            hn, he = to_local(home_message.latitude / 1e7, home_message.longitude / 1e7)
            battery = messages["SYS_STATUS"]
            result.append(
                {
                    "id": name,
                    "sysid": vehicle["sysid"],
                    "heartbeat_age_s": age,
                    "link_lost": age > 2
                    or time.monotonic() - vehicle["times"]["GLOBAL_POSITION_INT"] > 2,
                    "armed": bool(heartbeat.base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED),
                    "camera_available": vehicle["camera_available"],
                    "mode": vehicle["mode"],
                    "landed_state": messages["EXTENDED_SYS_STATE"].landed_state,
                    "latitude_deg": position.lat / 1e7,
                    "longitude_deg": position.lon / 1e7,
                    "north_m": north,
                    "east_m": east,
                    "altitude_above_home_m": position.relative_alt / 1000,
                    "heading_deg": None if position.hdg == 65535 else position.hdg / 100,
                    "course_deg": course_degrees(position.vx, position.vy),
                    "ground_speed_mps": math.hypot(position.vx, position.vy) / 100,
                    "climb_rate_mps": -position.vz / 100,
                    "battery_percent": None
                    if battery.battery_remaining == -1
                    else battery.battery_remaining,
                    "usable_flight_s": None
                    if battery.battery_remaining == -1
                    else available_seconds(battery.battery_remaining),
                    "battery_voltage_v": None
                    if battery.voltage_battery == 65535
                    else battery.voltage_battery / 1000,
                    "gps_fix": gps.fix_type,
                    "satellites": gps.satellites_visible,
                    "ekf_healthy": self._ekf_healthy(messages["EKF_STATUS_REPORT"]),
                    "mission_current": messages["MISSION_CURRENT"].seq
                    if "MISSION_CURRENT" in messages
                    else None,
                    "mission_total": len(vehicle["mission"]),
                    "mission": points(vehicle["mission"]),
                    "fence": read_fences(vehicle["fence"]) if vehicle["fence"] else None,
                    "home": {
                        "north_m": hn,
                        "east_m": he,
                        "latitude_deg": home_message.latitude / 1e7,
                        "longitude_deg": home_message.longitude / 1e7,
                        "altitude_msl_m": home_message.altitude / 1000,
                    },
                    "received_at_ms": samples[-1][1],
                    "update_rate_hz": hz,
                    "status_text": copy.deepcopy(vehicle["status_text"]),
                }
            )
        return result

    async def _mode(self, name, mode):
        modes = self.vehicles[name]["connection"].mode_mapping()
        await self._command(
            name,
            MAV.MAV_CMD_DO_SET_MODE,
            [MAV.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, modes[mode], 0, 0, 0, 0, 0],
        )
        await self._until(name, lambda: self.vehicles[name]["mode"] == mode, 10)

    def _validate_route(self, name, route):
        assert self.fences_verified, (
            "Flight fences are not verified on all four drones; launch is locked"
        )
        assert route
        assert self.landmarks is not None, "Landmark catalogue required for obstacle clearance"
        home = next(state["home"] for state in self.snapshot() if state["id"] == name)
        validate_route(
            route,
            self.vehicles[name]["fence"],
            home["altitude_msl_m"] - ORIGIN["altitude_msl_m"],
            self.landmarks,
        )
        self._validate_separation(name, route)

    def _validate_separation(self, name, route):
        states = self.snapshot()
        validate_separation(
            name,
            route,
            self.reservations(states),
            {state["id"]: state["home"]["altitude_msl_m"] for state in states},
        )

    def reservations(self, states):
        return [
            (state["id"], remaining_route(state, self.planned_routes[state["id"]]))
            for state in states
        ]

    def check_return(self, state):
        assert self.fences_verified, "Flight fences are not verified on all four drones"
        name = state["id"]
        assert not state["link_lost"] and state["ekf_healthy"] and state["gps_fix"] >= 3, (
            name + ": return requires healthy telemetry and GPS/EKF"
        )
        assert state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR, (
            name + ": return requires airborne aircraft"
        )
        route = rtl_route(state)
        validate_ground_column(route[-1], self.landmarks)
        validate_route(
            route[:-1],
            self.vehicles[name]["fence"],
            state["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"],
            self.landmarks,
        )
        return route

    async def return_step(self, name):
        async with self.command_lock:
            states = self.snapshot()
            state = next(state for state in states if state["id"] == name)
            assert state["mode"] == "BRAKE", name + ": queued return was superseded"
            route = self.check_return(state)
            waiting = None
            home_heights = {state["id"]: state["home"]["altitude_msl_m"] for state in states}
            for reservation in self.reservations(states):
                conflict = separation_conflict(name, route, [reservation], home_heights)
                if conflict is not None:
                    other = next(s for s in states if s["id"] == conflict["other_drone_id"])
                    if other["mode"] != "RTL" or not other["armed"]:
                        raise separation(conflict)
                    if waiting is None:
                        waiting = conflict
            if waiting is not None:
                return waiting
            await self._mode(name, "RTL")
            self.control_versions[name] += 1
            self.planned_routes[name] = route
            return None

    async def upload_mission(self, name, items):
        vehicle = self.vehicles[name]
        was_armed = bool(
            vehicle["messages"]["HEARTBEAT"].base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED
        )
        await self._upload(name, items, MAV.MAV_MISSION_TYPE_MISSION)
        received = await self._download(name, MAV.MAV_MISSION_TYPE_MISSION)
        assert len(received) == len(items), f"{name}: mission readback count mismatch"
        for index, (sent, actual) in enumerate(zip(items, received)):
            for key, expected in mission_readback(sent, home=index == 0).items():
                assert expected == actual[key], (
                    f"{name}: mission item {index} {key} mismatch: {actual[key]} != {expected}"
                )
        vehicle["mission"] = received
        vehicle["camera_available"] = len(received) > 1 and (
            not was_armed
            or bool(vehicle["messages"]["HEARTBEAT"].base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED)
        )

    async def preview_plan(self, plan):
        from mission_planner.gcs.planner import compile_plan

        assert self.fences_verified, (
            "Flight fences are not verified on all four drones; launch is locked"
        )
        version = self.fences["version"]
        states = self.snapshot()
        receipt = await asyncio.to_thread(
            compile_plan,
            copy.deepcopy(plan),
            states,
            {name: copy.deepcopy(vehicle["fence"]) for name, vehicle in self.vehicles.items()},
            self.landmarks,
            copy.deepcopy(self.reservations(states)),
        )
        assert self.fences_verified and self.fences["version"] == version, (
            "Flight fences changed during preview; preview the plan again"
        )
        return receipt

    async def reset_batteries(self):
        """Refill every landed simulator battery.

        The patched AirSim model re-initialises its state of charge when SIM_BATT_VOLTAGE changes,
        so the parameter is written away from and back to the full value; the autopilot's
        consumed-charge estimate is then reset to a full battery.
        """
        async with self.command_lock:
            states = self.snapshot()
            for state in states:
                assert (
                    not state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND
                ), f"{state['id']}: battery reset needs every drone landed and disarmed"
            for name in NAMES:
                await self._parameter(name, "SIM_BATT_VOLTAGE", FULL_BATTERY_V + 0.01)
                await self._parameter(name, "SIM_BATT_VOLTAGE", FULL_BATTERY_V)
                await self._command(name, MAV.MAV_CMD_BATTERY_RESET, [1, 100, 0, 0, 0, 0, 0])
                await self._until(
                    name,
                    lambda name=name: (
                        self.vehicles[name]["messages"]["SYS_STATUS"].battery_remaining == 100
                    ),
                    10,
                )
            self.on_event({"kind": "battery_reset", "drone_ids": list(NAMES)})

    async def preview_task(self, tasking, version, operator_points, searched=(), continuing=None):
        from mission_planner.gcs.tasking import plan as plan_task

        assert self.fences_verified, (
            "Flight fences are not verified on all four drones; launch is locked"
        )
        fence_version = self.fences["version"]
        states = self.snapshot()
        receipt = await asyncio.to_thread(
            plan_task,
            copy.deepcopy(tasking),
            version,
            states,
            {name: copy.deepcopy(vehicle["fence"]) for name, vehicle in self.vehicles.items()},
            self.landmarks,
            copy.deepcopy(self.reservations(states)),
            list(operator_points),
            searched=copy.deepcopy(list(searched)),
            continuing=copy.deepcopy(continuing),
        )
        assert self.fences_verified and self.fences["version"] == fence_version, (
            "Flight fences changed during preview; preview the plan again"
        )
        return receipt

    async def apply_plan(self, plan, kept=()):
        """Brake, upload and start every drone in the plan. kept lists airborne drones outside the
        plan that keep flying their current branch and mission: nothing is sent to them, the
        returned receipt reports them beside the new drones."""
        async with self.command_lock:
            receipt = await self.preview_plan(plan)
            states = self.snapshot()
            for name in receipt["missions"]:
                self.control_versions[name] += 1
            self.on_event(
                {
                    "kind": "plan_swap",
                    "version": plan["version"],
                    "stage": "holding",
                    "drone_ids": list(receipt["missions"]),
                }
            )
            timings = {}
            for state in states:
                name = state["id"]
                if name not in receipt["missions"]:
                    continue
                timings[name] = {"brake_at_ms": None, "upload_at_ms": None, "auto_at_ms": None}
                if state["armed"]:
                    timings[name]["brake_at_ms"] = time.time() * 1000
                    await self._mode(name, "BRAKE")
                    await self._until(
                        name,
                        lambda name=name: (
                            next(s for s in self.snapshot() if s["id"] == name)["ground_speed_mps"]
                            < STOPPED_SPEED_MPS
                        ),
                        15,
                    )
            states = self.snapshot()
            receipt = await self.preview_plan(plan)
            self.on_event(
                {
                    "kind": "plan_swap",
                    "version": plan["version"],
                    "stage": "held",
                    "states": states,
                    "drone_ids": list(receipt["missions"]),
                }
            )
            for name, mission in receipt["missions"].items():
                timings[name]["upload_at_ms"] = time.time() * 1000
                self.on_event(
                    {
                        "kind": "plan_swap",
                        "version": plan["version"],
                        "stage": "upload",
                        "drone_id": name,
                        "at_ms": timings[name]["upload_at_ms"],
                    }
                )
                await self.upload_mission(name, mission["items"])
                await self._command(name, MAV.MAV_CMD_DO_SET_MISSION_CURRENT, [1, 1, 0, 0, 0, 0, 0])
            self.on_event(
                {
                    "kind": "plan_swap",
                    "version": plan["version"],
                    "stage": "uploaded",
                    "receipt": with_kept(receipt, kept),
                    "drone_ids": list(receipt["missions"]),
                }
            )
            launching = []
            for state in states:
                name = state["id"]
                if name not in receipt["missions"]:
                    continue
                if not state["armed"]:
                    launching.append(name)
                    await self._mode(name, "GUIDED")
                    await self._command(
                        name, MAV.MAV_CMD_COMPONENT_ARM_DISARM, [1, 0, 0, 0, 0, 0, 0]
                    )
                    await self._until(
                        name,
                        lambda name=name: bool(
                            self.vehicles[name]["messages"]["HEARTBEAT"].base_mode
                            & MAV.MAV_MODE_FLAG_SAFETY_ARMED
                        ),
                        10,
                    )
            try:
                for name in launching:
                    vehicle = self.vehicles[name]
                    for kind, message_id in (("HOME_POSITION", 242), ("GLOBAL_POSITION_INT", 33)):
                        before = vehicle["times"][kind]
                        await self._command(
                            name, MAV.MAV_CMD_REQUEST_MESSAGE, [message_id, 0, 0, 0, 0, 0, 0]
                        )
                        await self._until(name, lambda: vehicle["times"][kind] > before, 10)
                states = self.snapshot()
                await asyncio.to_thread(
                    validate_missions,
                    receipt,
                    states,
                    {
                        name: copy.deepcopy(vehicle["fence"])
                        for name, vehicle in self.vehicles.items()
                    },
                    self.landmarks,
                    copy.deepcopy(self.reservations(states)),
                )
            except (AssertionError, TimeoutError) as error:
                raise RuntimeError(
                    f"Pre-AUTO safety check failed; no mission started: {error}"
                ) from error
            for name in receipt["missions"]:
                await self._command(name, MAV.MAV_CMD_MISSION_START, [0, 0, 0, 0, 0, 0, 0])
                await self._until(name, lambda name=name: self.vehicles[name]["mode"] == "AUTO", 10)
                timings[name]["auto_at_ms"] = time.time() * 1000
                self.planned_routes[name] = receipt["missions"][name]["route"]
            return {"receipt": with_kept(receipt, kept), "states": states, "timings": timings}

    async def face_heading(self, drone_id, heading_deg):
        assert drone_id in NAMES, f"Unknown drone id: {drone_id}"
        assert type(heading_deg) in (int, float) and math.isfinite(heading_deg), (
            "Heading must be finite degrees"
        )
        assert 0 <= heading_deg < 360, "Heading must be in [0, 360) degrees"
        vehicle = self.vehicles[drone_id]

        def current():
            state = next(s for s in self.snapshot() if s["id"] == drone_id)
            assert not state["link_lost"], f"{drone_id}: link lost"
            assert state["ekf_healthy"] and state["gps_fix"] >= 3, f"{drone_id}: GPS/EKF unhealthy"
            assert state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR, (
                f"{drone_id}: heading change requires airborne aircraft"
            )
            assert state["mode"] == "AUTO", f"{drone_id}: heading change requires AUTO flight"
            assert state["camera_available"], (
                f"{drone_id}: heading change requires a verified uploaded mission"
            )
            assert MIN_ALTITUDE_M <= state["altitude_above_home_m"] <= MAX_ALTITUDE_M, (
                f"{drone_id}: Altitude outside {MIN_ALTITUDE_M:g}–{MAX_ALTITUDE_M:g} m above home"
            )
            assert (
                state["heading_deg"] is not None
                and math.isfinite(state["heading_deg"])
                and 0 <= state["heading_deg"] < 360
            ), f"{drone_id}: heading telemetry unavailable or invalid"
            validate_position(drone_id, state, vehicle["fence"])
            return state

        async with self.command_lock:
            state = current()
            sequence, mission = state["mission_current"], vehicle["mission"]
            assert sequence is not None and 0 <= sequence < len(mission), (
                f"{drone_id}: current mission item unavailable"
            )
            await self._command(
                drone_id, MAV.MAV_CMD_CONDITION_YAW, [heading_deg, 0, 0, 0, 0, 0, 0]
            )

        def aligned():
            state = current()
            assert vehicle["mission"] is mission and state["mission_current"] == sequence, (
                f"{drone_id}: mission changed while facing the requested heading"
            )
            return abs((state["heading_deg"] - heading_deg + 180) % 360 - 180) <= 2

        await self._until(drone_id, aligned, 30)
        return current()

    def _tracking_state(self, name):
        assert name in NAMES, f"Unknown drone id: {name}"
        state = next(s for s in self.snapshot() if s["id"] == name)
        assert (
            not state["link_lost"] and 0 <= time.time() * 1000 - state["received_at_ms"] <= 2000
        ), f"{name}: tracking telemetry is stale"
        assert state["ekf_healthy"] and state["gps_fix"] >= 3, f"{name}: tracking GPS/EKF unhealthy"
        assert state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR, (
            f"{name}: tracking requires airborne aircraft"
        )
        assert state["camera_available"], f"{name}: tracking requires a verified mission"
        battery = state["battery_percent"]
        assert battery is not None and math.isfinite(battery) and 0 <= battery <= 100, (
            f"{name}: tracking battery telemetry is unavailable or invalid"
        )
        assert available_seconds(battery) > 0, (
            f"{name}: tracking battery {battery:g}% has no usable time above the "
            f"{CUTOFF_PERCENT:g}% charge floor and {VOLTAGE_FLOOR_PERCENT:.2f}% "
            f"voltage threshold ({LOW_VOLTAGE_V:g} V)"
        )
        self._validate_route(name, [state])
        return state

    async def _require_mission_resume(self, name):
        vehicle = self.vehicles[name]
        reply = await self._expect(
            name,
            "PARAM_VALUE",
            lambda m: m.param_id == "MIS_RESTART",
            lambda: vehicle["connection"].mav.param_request_read_send(
                vehicle["sysid"], 1, b"MIS_RESTART", -1
            ),
        )
        assert reply.param_value == 0, f"{name}: MIS_RESTART must be 0 to preserve search progress"

    async def tracking_start(self, name, step_item_range):
        first, last = step_item_range
        assert type(first) is int and type(last) is int and 1 <= first <= last
        async with self.command_lock:
            state = self._tracking_state(name)
            assert state["mode"] in ("AUTO", "BRAKE"), (
                f"{name}: candidate lock requires the active or held search route"
            )
            mode = state["mode"]
            mission = copy.deepcopy(self.vehicles[name]["mission"])
            assert first <= state["mission_current"] <= last < len(mission), (
                f"{name}: candidate arrived outside its observation step"
            )
            assert mission[state["mission_current"]]["command"] == MAV.MAV_CMD_NAV_WAYPOINT, (
                f"{name}: candidate lock requires an unfinished observed waypoint"
            )
            route = copy.deepcopy(self.planned_routes[name])
            version = self.control_versions[name]
            await self._require_mission_resume(name)
            state = self._tracking_state(name)
            assert state["mode"] == mode and self.control_versions[name] == version, (
                f"{name}: search flight was superseded before candidate pause"
            )
            assert self.vehicles[name]["mission"] == mission and (
                first <= state["mission_current"] <= last
            ), f"{name}: observation step changed before candidate pause"
            assert mission[state["mission_current"]]["command"] == MAV.MAV_CMD_NAV_WAYPOINT, (
                f"{name}: candidate lock requires an unfinished observed waypoint"
            )
            if mode == "AUTO":
                await self._mode(name, "BRAKE")

        def stopped():
            state = self._tracking_state(name)
            assert state["mode"] == "BRAKE", f"{name}: candidate pause was superseded"
            assert self.control_versions[name] == version, f"{name}: candidate pause was superseded"
            return (
                state["ground_speed_mps"] < STOPPED_SPEED_MPS
                and abs(state["climb_rate_mps"]) < STOPPED_SPEED_MPS
            )

        await self._until(name, stopped, 15)
        async with self.command_lock:
            state = self._tracking_state(name)
            assert state["mode"] == "BRAKE", f"{name}: candidate pause was superseded"
            assert self.control_versions[name] == version, f"{name}: candidate pause was superseded"
            sequence = state["mission_current"]
            assert self.vehicles[name]["mission"] == mission and first <= sequence <= last, (
                f"{name}: observation step changed before the aircraft stopped"
            )
            assert mission[sequence]["command"] == MAV.MAV_CMD_NAV_WAYPOINT, (
                f"{name}: candidate lock requires an unfinished observed waypoint"
            )
            speeds = [
                m["param2"]
                for m in mission[: sequence + 1]
                if m["command"] == MAV.MAV_CMD_DO_CHANGE_SPEED
            ]
            assert speeds, f"{name}: observation step has no recorded speed"
            saved = {
                "mission": mission,
                "sequence": sequence,
                "point": {k: state[k] for k in ("north_m", "east_m", "altitude_above_home_m")},
                "route": route,
                "speed_mps": speeds[-1],
                "control_version": version,
            }
            self.planned_routes[name] = [saved["point"]]
            return saved

    def _candidate_state(self, name, saved):
        state = self._tracking_state(name)
        assert self.control_versions[name] == saved["control_version"], (
            f"{name}: candidate control was superseded"
        )
        assert state["mode"] in ("BRAKE", "GUIDED"), f"{name}: candidate control was superseded"
        assert self.vehicles[name]["mission"] == saved["mission"], (
            f"{name}: saved search mission was replaced"
        )
        assert state["mission_current"] == saved["sequence"], (
            f"{name}: saved search progress changed"
        )
        return state

    async def tracking_follow(self, name, saved, speed_mps):
        assert math.isfinite(speed_mps) and 0.5 <= speed_mps <= 15
        async with self.command_lock:
            state = self._candidate_state(name, saved)
            assert state["mode"] == "BRAKE", f"{name}: candidate is already following"
            await self._mode(name, "GUIDED")
            # The first position target resets ArduPilot's GUIDED speed limits.
            self._send_tracking_target(name, state, state["heading_deg"])
            await self._command(name, MAV.MAV_CMD_DO_CHANGE_SPEED, [1, speed_mps, -1, 0, 0, 0, 0])

    def _send_tracking_target(self, name, point, yaw_deg):
        latitude, longitude = to_geodetic(point["north_m"], point["east_m"])
        vehicle = self.vehicles[name]
        vehicle["connection"].mav.set_position_target_global_int_send(
            0,
            vehicle["sysid"],
            1,
            MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            2552,
            round(latitude * 1e7),
            round(longitude * 1e7),
            point["altitude_above_home_m"],
            0,
            0,
            0,
            0,
            0,
            0,
            math.radians(yaw_deg),
            0,
        )

    async def tracking_target(self, name, point, yaw_deg, captured_at_ms=None):
        assert math.isfinite(yaw_deg) and 0 <= yaw_deg < 360
        async with self.command_lock:
            state = self._tracking_state(name)
            assert state["mode"] == "GUIDED", f"{name}: candidate control was superseded"
            self._validate_route(name, [state, point])
            if captured_at_ms is not None:
                assert 0 <= time.time() * 1000 - captured_at_ms <= MAX_TRACKING_AGE_S * 1000, (
                    f"{name}: candidate observation is too old at flight-command dispatch"
                )
            self._send_tracking_target(name, point, yaw_deg)
            self.planned_routes[name] = [state, point]
            return state

    async def tracking_resume(self, name, saved, breadcrumbs, speed_mps):
        assert 0.5 <= speed_mps <= 15 and math.isfinite(speed_mps)

        def current():
            return self._candidate_state(name, saved)

        state = current()
        points = [*reversed(breadcrumbs), saved["point"]]
        self._validate_route(name, [state, *points])
        if state["mode"] == "BRAKE":
            await self.tracking_follow(name, saved, speed_mps)
        for point in points:
            state = current()
            heading = (
                math.degrees(
                    math.atan2(
                        point["east_m"] - state["east_m"], point["north_m"] - state["north_m"]
                    )
                )
                % 360
            )
            await self.tracking_target(name, point, heading)

            def arrived():
                state = current()
                return (
                    math.dist(
                        [state[k] for k in saved["point"]], [point[k] for k in saved["point"]]
                    )
                    < 0.8
                )

            distance = math.dist(
                [state[k] for k in saved["point"]], [point[k] for k in saved["point"]]
            )
            await self._until(name, arrived, max(15, distance / speed_mps * 3))
        async with self.command_lock:
            state = current()
            await self._require_mission_resume(name)
            state = current()
            remaining = [state]
            for item in saved["mission"][saved["sequence"] :]:
                if item["command"] == MAV.MAV_CMD_DO_CHANGE_SPEED:
                    continue
                north, east = to_local(item["x"] / 1e7, item["y"] / 1e7)
                if item["command"] == MAV.MAV_CMD_NAV_LAND:
                    validate_ground_column({"north_m": north, "east_m": east}, self.landmarks)
                    break
                assert item["command"] in (
                    MAV.MAV_CMD_NAV_WAYPOINT,
                    MAV.MAV_CMD_NAV_LOITER_TIME,
                    MAV.MAV_CMD_NAV_LOITER_UNLIM,
                ), f"{name}: unsupported remaining search command"
                remaining.append(
                    {"north_m": north, "east_m": east, "altitude_above_home_m": item["z"]}
                )
            self._validate_route(name, remaining)
            await self._mode(name, "AUTO")
            await self._command(
                name, MAV.MAV_CMD_DO_CHANGE_SPEED, [1, saved["speed_mps"], -1, 0, 0, 0, 0]
            )
            vehicle = self.vehicles[name]
            progress = await self._expect(
                name,
                "MISSION_CURRENT",
                lambda m: True,
                lambda: vehicle["connection"].mav.command_long_send(
                    vehicle["sysid"],
                    1,
                    MAV.MAV_CMD_REQUEST_MESSAGE,
                    0,
                    42,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                ),
            )
            assert saved["sequence"] <= progress.seq < len(saved["mission"]), (
                f"{name}: resumed mission rewound or lost its search progress"
            )
            self.planned_routes[name] = saved["route"]
            return next(s for s in self.snapshot() if s["id"] == name)

    async def execute(self, name, args, *, expected_mode=None, expected_step=None):
        assert expected_step is None or expected_mode == "AUTO"
        version = None if expected_step is None else self.control_versions[args["drone_id"]]
        async with self.command_lock:
            assert self.fences_verified, (
                "Flight fences are not verified on all four drones; launch is locked"
            )
            drone = args["drone_id"]
            assert drone in NAMES, f"Unknown drone id: {drone}"
            states = self.snapshot()
            for parked in states:
                if parked["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND and not parked["armed"]:
                    self.planned_routes[parked["id"]] = [parked]
            state = next(s for s in states if s["id"] == drone)
            if expected_mode is None:
                self.control_versions[drone] += 1
            if expected_mode is not None:
                assert name == "hold" and expected_mode in ("AUTO", "GUIDED", "BRAKE")
                assert (
                    state["mode"] == expected_mode
                    and state["armed"]
                    and state["landed_state"] in (2, 3, 4)
                ), (
                    f"{drone}: automatic hold was superseded "
                    f"(expected {expected_mode}, mode={state['mode']}, "
                    f"armed={state['armed']}, landed_state={state['landed_state']})"
                )
            if expected_step is not None:
                assert self.control_versions[drone] == version, (
                    f"{drone}: automatic hold authority was superseded"
                )
                items, first, last = expected_step
                assert type(first) is int and type(last) is int and 1 <= first <= last
                mission = self.vehicles[drone]["mission"]
                assert len(mission) == len(items) and all(
                    actual[key] == value
                    for index, (sent, actual) in enumerate(zip(items, mission))
                    for key, value in mission_readback(sent, home=index == 0).items()
                ), f"{drone}: automatic hold mission was replaced"
                sequence = state["mission_current"]
                assert (
                    sequence is not None
                    and first <= sequence <= last < len(mission)
                    and mission[sequence]["command"] == MAV.MAV_CMD_NAV_WAYPOINT
                ), f"{drone}: automatic hold observation waypoint is no longer current"
            assert not state["link_lost"], f"{drone}: link lost"
            assert state["ekf_healthy"] and state["gps_fix"] >= 3, f"{drone}: GPS/EKF unhealthy"
            validate_position(drone, state, self.vehicles[drone]["fence"])
            if name == "take_off":
                assert (
                    not state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND
                ), "Takeoff requires disarmed, on-ground aircraft"
                validate_ground_column(state, self.landmarks)
                route = [{**state, "altitude_above_home_m": args["altitude_above_home_m"]}]
                self._validate_route(drone, route)
                route = [state, *route]
                self._validate_separation(drone, route)
                await self._mode(drone, "GUIDED")
                await self._command(drone, MAV.MAV_CMD_COMPONENT_ARM_DISARM, [1, 0, 0, 0, 0, 0, 0])
                await self._until(
                    drone,
                    lambda: (
                        self.vehicles[drone]["messages"]["HEARTBEAT"].base_mode
                        & MAV.MAV_MODE_FLAG_SAFETY_ARMED
                    ),
                    10,
                )
                await self._command(
                    drone,
                    MAV.MAV_CMD_NAV_TAKEOFF,
                    [0, 0, 0, 0, 0, 0, args["altitude_above_home_m"]],
                )
                self.planned_routes[drone] = route
            else:
                assert state["armed"], f"{drone}: aircraft is not armed"
                if name in ("fly_route", "fly_to"):
                    assert state["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR, (
                        "Route requires airborne aircraft"
                    )
                    route = args["waypoints"] if name == "fly_route" else [args]
                    assert 0 < args["speed_mps"] <= 15, "Speed must be >0 and <=15 m/s"
                    self._validate_route(drone, [state, *route])
                    if name == "fly_route":
                        items = [mission_home(state["home"])]
                        items.extend(
                            mission_item(p, p["altitude_above_home_m"], MAV.MAV_CMD_NAV_WAYPOINT)
                            for p in route
                        )
                        await self.upload_mission(drone, items)
                        await self._command(
                            drone, MAV.MAV_CMD_DO_SET_MISSION_CURRENT, [1, 1, 0, 0, 0, 0, 0]
                        )
                    await self._mode(drone, "AUTO" if name == "fly_route" else "GUIDED")
                    await self._command(
                        drone,
                        MAV.MAV_CMD_DO_CHANGE_SPEED,
                        [1, args["speed_mps"], -1, 0, 0, 0, 0],
                    )
                    if name == "fly_route":
                        self.vehicles[drone]["mission_speed_mps"] = args["speed_mps"]
                    if name == "fly_to":
                        lat, lon = to_geodetic(args["north_m"], args["east_m"])
                        vehicle = self.vehicles[drone]
                        vehicle["connection"].mav.set_position_target_global_int_send(
                            0,
                            vehicle["sysid"],
                            1,
                            MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                            3576,
                            round(lat * 1e7),
                            round(lon * 1e7),
                            args["altitude_above_home_m"],
                            0,
                            0,
                            0,
                            0,
                            0,
                            0,
                            0,
                            0,
                        )
                    self.planned_routes[drone] = [state, *route]
                elif name in ("hold", "resume", "return_home", "land"):
                    if name == "land":
                        validate_ground_column(state, self.landmarks)
                        self._validate_separation(
                            drone, [state, {**state, "altitude_above_home_m": 0}]
                        )
                    if name == "resume":
                        assert state["mission_total"] > 1, "No mission to resume"
                        assert self.vehicles[drone]["mission_speed_mps"] is not None, (
                            "Mission has no acknowledged flight speed"
                        )
                        self._validate_route(
                            drone, [state, *state["mission"][max(1, state["mission_current"]) :]]
                        )
                    if name == "return_home":
                        return_route = self.check_return(state)
                        self._validate_separation(drone, return_route)
                    await self._mode(
                        drone,
                        {"hold": "BRAKE", "resume": "AUTO", "return_home": "RTL", "land": "LAND"}[
                            name
                        ],
                    )
                    if name == "resume":
                        await self._command(
                            drone,
                            MAV.MAV_CMD_DO_CHANGE_SPEED,
                            [1, self.vehicles[drone]["mission_speed_mps"], -1, 0, 0, 0, 0],
                        )
                        self.planned_routes[drone] = [
                            state,
                            *state["mission"][max(1, state["mission_current"]) :],
                        ]
                    if name == "return_home":
                        self.planned_routes[drone] = return_route
                    if name == "hold":
                        self.planned_routes[drone] = [state]
                    if name == "land":
                        self.planned_routes[drone] = [state, {**state, "altitude_above_home_m": 0}]
                else:
                    raise ValueError(f"Unknown flight tool: {name}")
            return next(s for s in self.snapshot() if s["id"] == drone)

    def export_plan(self, drone_id):
        assert drone_id in NAMES, f"Unknown drone id: {drone_id}"
        vehicle = self.vehicles[drone_id]
        state = next(s for s in self.snapshot() if s["id"] == drone_id)
        assert len(vehicle["mission"]) > 1, "Cannot export an empty active mission"
        speeds = [
            m["param2"] for m in vehicle["mission"] if m["command"] == MAV.MAV_CMD_DO_CHANGE_SPEED
        ]
        speed = speeds[0] if speeds else vehicle["mission_speed_mps"]
        items = [
            {
                "AMSLAltAboveTerrain": None,
                "Altitude": m["z"],
                "AltitudeMode": 1,
                "autoContinue": bool(m["autocontinue"]),
                "command": m["command"],
                "doJumpId": i,
                "frame": 3 if m["frame"] == 6 else m["frame"],
                "params": [
                    m["param1"],
                    m["param2"],
                    m["param3"],
                    m["param4"],
                    m["x"] / 1e7,
                    m["y"] / 1e7,
                    m["z"],
                ],
                "type": "SimpleItem",
            }
            for i, m in enumerate(vehicle["mission"][1:], 1)
        ]
        home = state["home"]
        return {
            "fileType": "Plan",
            "version": 1,
            "groundStation": "SNR GCS",
            "geoFence": {
                "version": 2,
                "circles": [],
                "polygons": [
                    {
                        "version": 1,
                        "inclusion": inclusion,
                        "polygon": [list(to_geodetic(p["north_m"], p["east_m"])) for p in polygon],
                    }
                    for inclusion, polygons in (
                        (True, [state["fence"]["inclusion"]]),
                        (False, state["fence"]["exclusions"]),
                    )
                    for polygon in polygons
                ],
            },
            "rallyPoints": {"version": 2, "points": []},
            "mission": {
                "version": 2,
                "firmwareType": 3,
                "vehicleType": 2,
                "globalPlanAltitudeMode": 1,
                "cruiseSpeed": 15,
                "hoverSpeed": 0 if speed is None else speed,
                "plannedHomePosition": [
                    home["latitude_deg"],
                    home["longitude_deg"],
                    home["altitude_msl_m"],
                ],
                "items": items,
            },
        }

    async def close(self):
        tasks = self.tasks
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for vehicle in self.vehicles.values():
            if vehicle["process"].returncode is None:
                vehicle["process"].terminate()
        for vehicle in self.vehicles.values():
            await asyncio.wait_for(vehicle["process"].wait(), 10)
            vehicle["connection"].close()
            vehicle["logfile"].close()
        for result in results:
            assert result is None or isinstance(result, asyncio.CancelledError), result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / "events.jsonl").open("w") as log:

        def event(value):
            log.write(json.dumps({"at_ms": time.time() * 1000, **value}) + "\n")
            log.flush()

        fleet = Fleet(args.output / "sitl", event, load_landmarks())
        startup = asyncio.create_task(fleet.start())
        stopped = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait((startup, stopped), return_when=asyncio.FIRST_COMPLETED)
            if startup in done:
                await startup
                (args.output / "ready.json").write_text(
                    json.dumps(fleet.snapshot(), indent=2) + "\n"
                )
                print("Four autopilots ready; SIGTERM stops the fleet", flush=True)
                await stopped
        finally:
            startup.cancel()
            stopped.cancel()
            await asyncio.gather(startup, stopped, return_exceptions=True)
            await fleet.close()


if __name__ == "__main__":
    asyncio.run(main())

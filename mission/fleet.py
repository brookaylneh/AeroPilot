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

from mission.environment import load_env  # noqa: E402
from mission.geodesy import (  # noqa: E402
    MAX_ALTITUDE_M,
    MIN_ALTITUDE_M,
    MIN_SEPARATION_M,
    NAMES,
    ROOT,
    fence_vertices,
    homes,
    point_in_polygon,
    rtl_route,
    segment_distance,
    segment_in_polygon,
    to_geodetic,
    to_local,
)

MAV = mavutil.mavlink
RATES = {0: 2, 1: 5, 24: 5, 33: 10, 42: 5, 193: 5, 245: 5, 242: 1}
REQUIRED = (
    "HEARTBEAT",
    "SYS_STATUS",
    "GPS_RAW_INT",
    "GLOBAL_POSITION_INT",
    "EKF_STATUS_REPORT",
    "EXTENDED_SYS_STATE",
    "HOME_POSITION",
)
SAFETY_PARAMETERS = {
    "ARMING_SKIPCHK": 0,
    "FS_THR_ENABLE": 1,
    "RTL_ALT_M": 15,
    "RTL_ALT_FINAL_M": 0,
    "RTL_CLIMB_MIN_M": 0,
    "RTL_CONE_SLOPE": 3,
    "RTL_ALT_TYPE": 0,
    "FENCE_ALT_MAX": 100,
    "FENCE_MARGIN": 2,
}


class Fleet:
    def __init__(self, directory, on_event):
        self.directory = Path(directory).resolve()
        self.on_event = on_event
        self.vehicles = {}
        self.tasks = []
        self.command_lock = asyncio.Lock()
        self.planned_routes = {}

    async def start(self):
        load_env()
        self.directory.mkdir(parents=True, exist_ok=False)
        binary = ROOT / "vendor/ardupilot/build/sitl/bin/arducopter"
        assert binary.is_file(), f"Build required: {binary}"
        for index, (name, home) in enumerate(homes().items()):
            directory = self.directory / name
            directory.mkdir()
            connection = mavutil.mavlink_connection(
                f"udpin:127.0.0.1:{14600 + index}",
                source_system=255,
                source_component=190,
                dialect="ardupilotmega",
            )
            logfile = (directory / "autopilot.log").open("w")
            args = [
                str(binary),
                "--model",
                "quad",
                "--instance",
                str(index),
                "--sysid",
                str(index + 1),
                "--home",
                f"{home['latitude_deg']},{home['longitude_deg']},{home['altitude_msl_m']},{home['heading_deg']}",
                "--serial0",
                "none",
                "--serial1",
                f"udpclient:127.0.0.1:{14600 + index}",
                "--serial2",
                "none",
                "--defaults",
                str(ROOT / "vendor/ardupilot/Tools/autotest/default_params/copter.parm"),
            ]
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
                "fence": [],
                "safety_parameters": {},
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
        assert math.dist((north, east), (vehicle["home"]["north_m"], vehicle["home"]["east_m"])) < 1
        for parameter, expected in SAFETY_PARAMETERS.items():
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
        items = [
            self._item(
                point, 0, MAV.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION, len(fence_vertices())
            )
            for point in fence_vertices()
        ]
        await self._upload(name, items, MAV.MAV_MISSION_TYPE_FENCE)
        vehicle["fence"] = await self._download(name, MAV.MAV_MISSION_TYPE_FENCE)
        assert len(vehicle["fence"]) == len(fence_vertices())
        for intended, actual in zip(items, vehicle["fence"]):
            assert intended["x"] == actual["x"] and intended["y"] == actual["y"]
            assert actual["command"] == MAV.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION
            assert actual["param1"] == len(fence_vertices())
        await self._parameter(name, "FENCE_TYPE", 5)
        await self._parameter(name, "FENCE_ENABLE", 1)
        vehicle["mission"] = await self._download(name, MAV.MAV_MISSION_TYPE_MISSION)

    @staticmethod
    def _ekf_healthy(message):
        required = (
            MAV.EKF_ATTITUDE | MAV.EKF_VELOCITY_HORIZ | MAV.EKF_POS_HORIZ_ABS | MAV.EKF_POS_VERT_ABS
        )
        return message.flags & required == required and not message.flags & (
            MAV.EKF_CONST_POS_MODE | MAV.EKF_UNINITIALIZED
        )

    @staticmethod
    def _item(point, altitude, command, param1=0):
        latitude, longitude = to_geodetic(point["north_m"], point["east_m"])
        return {
            "frame": MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            "command": command,
            "param1": param1,
            "param2": 0,
            "param3": 0,
            "param4": 0,
            "x": round(latitude * 1e7),
            "y": round(longitude * 1e7),
            "z": altitude,
        }

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
            assert -1 <= messages["SYS_STATUS"].battery_remaining <= 100
            samples = list(vehicle["positions"])
            hz = (len(samples) - 1) / (samples[-1][0] - samples[0][0]) if len(samples) >= 2 else 0
            age = time.monotonic() - vehicle["times"]["HEARTBEAT"]
            if time.monotonic() - vehicle["times"]["GLOBAL_POSITION_INT"] > 2:
                hz = 0

            def points(items):
                result = []
                for item in items:
                    n, e = to_local(item["x"] / 1e7, item["y"] / 1e7)
                    result.append(
                        {
                            "seq": item["seq"],
                            "north_m": n,
                            "east_m": e,
                            "latitude_deg": item["x"] / 1e7,
                            "longitude_deg": item["y"] / 1e7,
                            "altitude_above_home_m": item["z"],
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
                    "mode": vehicle["mode"],
                    "landed_state": messages["EXTENDED_SYS_STATE"].landed_state,
                    "latitude_deg": position.lat / 1e7,
                    "longitude_deg": position.lon / 1e7,
                    "north_m": north,
                    "east_m": east,
                    "altitude_above_home_m": position.relative_alt / 1000,
                    "heading_deg": None if position.hdg == 65535 else position.hdg / 100,
                    "ground_speed_mps": math.hypot(position.vx, position.vy) / 100,
                    "climb_rate_mps": -position.vz / 100,
                    "battery_percent": None
                    if battery.battery_remaining == -1
                    else battery.battery_remaining,
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
                    "fence": points(vehicle["fence"]),
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

    def _fence(self, name):
        return [
            dict(zip(("north_m", "east_m"), to_local(m["x"] / 1e7, m["y"] / 1e7)))
            for m in self.vehicles[name]["fence"]
        ]

    def _validate_route(self, name, route):
        assert route
        polygon = self._fence(name)
        for p in route:
            assert all(math.isfinite(p[k]) for k in ("north_m", "east_m", "altitude_above_home_m"))
            assert point_in_polygon(p, polygon), "Waypoint is outside the search fence"
            assert MIN_ALTITUDE_M <= p["altitude_above_home_m"] <= MAX_ALTITUDE_M, (
                "Altitude must be 10–100 m above home"
            )

        for a, b in zip(route, route[1:]):
            assert segment_in_polygon(a, b, polygon), (
                "Route segment exits the launch corridor fence"
            )

        self._validate_separation(name, route)

    def _validate_separation(self, name, route):
        # Routes reserve their entire spatial corridor, independent of timing.
        def segments(points):
            values = [
                tuple(p[k] for k in ("north_m", "east_m", "altitude_above_home_m")) for p in points
            ]
            return list(zip(values, values[1:])) if len(values) > 1 else [(values[0], values[0])]

        reservations = list(self.planned_routes.items())
        reservations.extend((state["id"], [state]) for state in self.snapshot())
        for other, reserved in reservations:
            if other != name:
                for a, b in segments(route):
                    for c, d in segments(reserved):
                        assert segment_distance(a, b, c, d) >= MIN_SEPARATION_M, (
                            f"Route conflicts with {other}; minimum separation {MIN_SEPARATION_M} m"
                        )

    async def execute(self, name, args):
        async with self.command_lock:
            drone = args["drone_id"]
            assert drone in NAMES, f"Unknown drone id: {drone}"
            states = self.snapshot()
            for parked in states:
                if parked["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND and not parked["armed"]:
                    self.planned_routes[parked["id"]] = [parked]
            state = next(s for s in states if s["id"] == drone)
            assert not state["link_lost"], f"{drone}: link lost"
            assert state["ekf_healthy"] and state["gps_fix"] >= 3, f"{drone}: GPS/EKF unhealthy"
            assert point_in_polygon(state, self._fence(drone)), (
                f"{drone}: current position is outside the search fence; launch position conflicts with required fence"
            )
            if name == "take_off":
                assert (
                    not state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND
                ), "Takeoff requires disarmed, on-ground aircraft"
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
                        items = [self._item(state["home"], 0, MAV.MAV_CMD_NAV_WAYPOINT)]
                        items.extend(
                            self._item(p, p["altitude_above_home_m"], MAV.MAV_CMD_NAV_WAYPOINT)
                            for p in route
                        )
                        await self._upload(drone, items, MAV.MAV_MISSION_TYPE_MISSION)
                        self.vehicles[drone]["mission"] = await self._download(
                            drone, MAV.MAV_MISSION_TYPE_MISSION
                        )
                        assert len(self.vehicles[drone]["mission"]) == len(items)
                        for sent, received in zip(items[1:], self.vehicles[drone]["mission"][1:]):
                            for key in ("command", "x", "y"):
                                assert sent[key] == received[key], (
                                    f"Mission readback mismatch: {key}"
                                )
                            for key in ("param1", "param2", "param3", "param4", "z"):
                                assert math.isclose(sent[key], received[key], abs_tol=1e-4), (
                                    f"Mission readback mismatch: {key}"
                                )
                            assert received["frame"] in (
                                MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                                MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                            )
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
                        return_route = rtl_route(state)
                        self._validate_route(drone, return_route[:-1])
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
        assert vehicle["mission_speed_mps"] is not None, "Mission has no acknowledged flight speed"
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
                        "inclusion": True,
                        "polygon": [[m["x"] / 1e7, m["y"] / 1e7] for m in vehicle["fence"]],
                    }
                ],
            },
            "rallyPoints": {"version": 2, "points": []},
            "mission": {
                "version": 2,
                "firmwareType": 3,
                "vehicleType": 2,
                "globalPlanAltitudeMode": 1,
                "cruiseSpeed": 15,
                "hoverSpeed": vehicle["mission_speed_mps"],
                "plannedHomePosition": [
                    home["latitude_deg"],
                    home["longitude_deg"],
                    home["altitude_msl_m"],
                ],
                "items": items,
            },
        }

    async def close(self):
        for task in self.tasks:
            task.cancel()
        results = await asyncio.gather(*self.tasks, return_exceptions=True)
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

        fleet = Fleet(args.output / "sitl", event)
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

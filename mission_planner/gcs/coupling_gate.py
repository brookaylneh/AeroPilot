"""Fly the coupled fleet gate and record receipt-time MAVLink/Unreal pose agreement."""

import argparse
import asyncio
import json
import math
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import cosysairsim as airsim
import numpy as np

from mission_planner.environment import load_env
from mission_planner.gcs.fleet import MAV, Fleet
from mission_planner.gcs.geodesy import NAMES, ORIGIN
from mission_planner.gcs.validation import validate_route, validate_separation

ALTITUDES_M = (50, 60, 70, 80)
NORTH_OFFSETS_M = (24, 54, 84, 114, 144, 174, 144, 114, 84, 54, 24, 34)


def xyz(value):
    result = [float(value.x_val), float(value.y_val), float(value.z_val)]
    assert all(math.isfinite(v) for v in result)
    return result


def truth(client):
    result = {}
    for name in NAMES:
        before = time.monotonic()
        state = client.simGetGroundTruthKinematics(vehicle_name=name)
        after = time.monotonic()
        gps = client.getGpsData(vehicle_name=name)
        imu = client.getImuData(vehicle_name=name)
        assert gps.is_valid and gps.gnss.fix_type >= 3, name
        result[name] = {
            "rpc_started_s": before,
            "rpc_finished_s": after,
            "position_local_ned_m": xyz(state.position),
            "velocity_local_ned_mps": xyz(state.linear_velocity),
            "gps_timestamp_ns": gps.time_stamp,
            "simulator_timestamp_ns": imu.time_stamp,
        }
    return result


def plans(states):
    assert [s["id"] for s in states] == list(NAMES)
    return {
        state["id"]: [
            {
                "north_m": state["home"]["north_m"] + offset,
                "east_m": state["home"]["east_m"],
                "altitude_above_home_m": altitude,
            }
            for offset in NORTH_OFFSETS_M
        ]
        for state, altitude in zip(states, ALTITUDES_M)
    }


def diagnostics(fleet):
    now = time.monotonic()
    kinds = {
        "HEARTBEAT",
        "SYS_STATUS",
        "GPS_RAW_INT",
        "GLOBAL_POSITION_INT",
        "EKF_STATUS_REPORT",
        "EXTENDED_SYS_STATE",
        "SYSTEM_TIME",
        "COMMAND_ACK",
    }
    return {
        "utc_s": time.time(),
        "vehicles": {
            name: {
                "messages": {
                    kind: {
                        "age_s": now - vehicle["times"][kind],
                        "message": message.to_dict(),
                    }
                    for kind, message in vehicle["messages"].items()
                    if kind in kinds
                },
                "status_text": list(vehicle["status_text"]),
            }
            for name, vehicle in fleet.vehicles.items()
        },
    }


async def until(fleet, predicate, timeout, label):
    deadline = time.monotonic() + timeout
    while not predicate(fleet.snapshot()):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out: {label}")
        await asyncio.sleep(0.1)


async def run(output, shutdown_file, seconds, speed):
    load_env()
    assert os.environ["SNR_SITL_MODEL"] == "airsim-copter"
    assert seconds > 0 and 0 < speed <= 15
    assert not shutdown_file.exists(), shutdown_file
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "passed": False,
        "full_gate": seconds >= 600,
        "required_airborne_wall_and_simulator_s": seconds,
        "speed_mps": speed,
        "coordinate_frame": "metres north/east/up from mission origin",
        "pose_comparison": "Latest received MAVLink position versus RPC ground truth; receipt age and RPC bracket retained",
    }
    phase = {"name": "startup", "sortie": 0}
    latest = {}
    errors = {name: {label: [] for label in ("hover", "cruise", "transition")} for name in NAMES}
    sequences = {name: set() for name in NAMES}
    event_log = (output / "events.jsonl").open("x")
    samples = (output / "poses.jsonl").open("x")

    def event(value):
        value = {"utc_s": time.time(), **value}
        event_log.write(json.dumps(value, allow_nan=False) + "\n")
        event_log.flush()

    fleet = Fleet(output / "sitl", event)
    monitor = flight = None
    readers = []
    executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, main_task.cancel)
    try:
        await fleet.start()
        await asyncio.gather(*(fleet._parameter(name, "LOG_DISARMED", 1) for name in NAMES))
        initial = fleet.snapshot()
        assert [s["id"] for s in initial] == list(NAMES)
        (output / "ready.json").write_text(json.dumps(initial, indent=2) + "\n")
        native = json.loads((Path(os.environ["SNR_DRONE_DATA_DIR"]) / "runtime.json").read_text())
        assert native["schemaVersion"] == 1 and native["worldToMeters"] == 100
        assert native["worldOriginOffsetCm"] == dict(x=0, y=0, z=0)
        captured = datetime.fromisoformat(native["capturedAt"].replace("Z", "+00:00"))
        assert abs((datetime.now(timezone.utc) - captured).total_seconds()) < 10
        origins = {v["id"]: v["localNedOriginUeCm"] for v in native["vehicles"]}
        assert set(origins) == set(NAMES)
        report["local_ned_origins_ue_cm"] = origins
        routes = plans(initial)
        report["routes"] = routes
        for state, altitude in zip(initial, ALTITUDES_M):
            start = {**state, "altitude_above_home_m": altitude}
            validate_route([start, *routes[state["id"]]], fleet.vehicles[state["id"]]["fence"])
            validate_separation(state["id"], routes[state["id"]], list(routes.items()))
        client = await loop.run_in_executor(
            executor,
            lambda: airsim.MultirotorClient(
                ip="127.0.0.1", port=int(os.environ["SNR_COSYS_PORT"]), timeout_value=5
            ),
        )
        readers = [(output / "sitl" / name / "autopilot.log").open() for name in NAMES]

        async def observe():
            while True:
                started = time.monotonic()
                current_phase = dict(phase)
                ground = await loop.run_in_executor(executor, truth, client)
                states = fleet.snapshot()
                current = {}
                for state in states:
                    name = state["id"]
                    assert (
                        not state["link_lost"] and state["ekf_healthy"] and state["gps_fix"] >= 3
                    ), state
                    assert state["update_rate_hz"] >= 5, (name, state["update_rate_hz"])
                    position = fleet.vehicles[name]["messages"]["GLOBAL_POSITION_INT"]
                    age = time.monotonic() - fleet.vehicles[name]["times"]["GLOBAL_POSITION_INT"]
                    assert age <= 0.5, (name, "stale position for pose comparison", age)
                    local = ground[name]["position_local_ned_m"]
                    actual = [
                        origins[name][k] / 100 + local[i] * (1 if i < 2 else -1)
                        for i, k in enumerate("xyz")
                    ]
                    measured = [
                        state["north_m"],
                        state["east_m"],
                        position.alt / 1000 - ORIGIN["altitude_msl_m"],
                    ]
                    error = math.dist(measured, actual)
                    label = (
                        "hover"
                        if state["ground_speed_mps"] < 0.5 and abs(state["climb_rate_mps"]) < 0.3
                        else "cruise"
                        if state["ground_speed_mps"] >= 1
                        else "transition"
                    )
                    airborne = (
                        state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR
                    )
                    if airborne:
                        errors[name][label].append(error)
                    if state["mission_current"] is not None:
                        sequences[name].add(state["mission_current"])
                    current[name] = {
                        **ground[name],
                        "mavlink": position.to_dict(),
                        "mavlink_age_s": age,
                        "mavlink_position_neu_m": measured,
                        "ground_truth_position_neu_m": actual,
                        "position_error_m": error,
                        "motion": label,
                        "airborne": airborne,
                        **{
                            k: state[k]
                            for k in (
                                "mode",
                                "armed",
                                "landed_state",
                                "gps_fix",
                                "ekf_healthy",
                                "link_lost",
                                "update_rate_hz",
                                "altitude_above_home_m",
                                "mission_current",
                            )
                        },
                    }
                for name, stream in zip(NAMES, readers):
                    text = stream.read()
                    assert "No sensor message" not in text, (name, text)
                latest.update(current)
                samples.write(
                    json.dumps(
                        {
                            "utc_s": time.time(),
                            "monotonic_s": started,
                            **current_phase,
                            "vehicles": current,
                        },
                        allow_nan=False,
                    )
                    + "\n"
                )
                samples.flush()
                await asyncio.sleep(max(0, 0.1 - (time.monotonic() - started)))

        async def command(action, arguments):
            event({"kind": "gate_command", "action": action, "arguments": arguments})
            return await fleet.execute(action, arguments)

        async def takeoff(altitudes):
            mask = MAV.MAV_SYS_STATUS_PREARM_CHECK | MAV.MAV_SYS_STATUS_SENSOR_GPS

            def ready_to_arm(states):
                statuses = [fleet.vehicles[s["id"]]["messages"]["SYS_STATUS"] for s in states]
                for name, status in zip(NAMES, statuses):
                    assert status.onboard_control_sensors_present & mask == mask, name
                    assert status.onboard_control_sensors_enabled & mask == mask, name
                return all(
                    status.onboard_control_sensors_health & mask == mask for status in statuses
                )

            event({"kind": "prearm_wait_started", "telemetry": diagnostics(fleet)})
            await until(fleet, ready_to_arm, 30, "four GPS and prearm health flags")
            event({"kind": "prearm_ready", "telemetry": diagnostics(fleet)})
            await asyncio.gather(
                *(
                    command("take_off", {"drone_id": name, "altitude_above_home_m": altitude})
                    for name, altitude in zip(NAMES, altitudes)
                )
            )
            await until(
                fleet,
                lambda states: all(
                    s["armed"]
                    and s["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR
                    and abs(s["altitude_above_home_m"] - a) < 1
                    for s, a in zip(states, altitudes)
                ),
                120,
                "four takeoffs",
            )

        async def sequence():
            phase.update(name="takeoff", sortie=1)
            await takeoff(ALTITUDES_M)
            assert set(latest) == set(NAMES)
            start_wall = time.monotonic()
            start_sim = {name: latest[name]["simulator_timestamp_ns"] for name in NAMES}
            phase["name"] = "initial_hover"
            await asyncio.sleep(10)
            phase["name"] = "auto"
            await asyncio.gather(
                *(
                    command(
                        "fly_route",
                        {"drone_id": name, "waypoints": routes[name], "speed_mps": speed},
                    )
                    for name in NAMES
                )
            )
            await until(
                fleet,
                lambda states: all(s["mission_current"] >= 3 for s in states),
                180 / speed + 60,
                "AUTO progress before BRAKE",
            )
            phase["name"] = "brake"
            await asyncio.gather(*(command("hold", {"drone_id": name}) for name in NAMES))
            await until(
                fleet,
                lambda states: all(
                    s["mode"] == "BRAKE" and s["ground_speed_mps"] < 0.3 for s in states
                ),
                30,
                "BRAKE settled",
            )
            await asyncio.sleep(5)
            phase["name"] = "resumed_auto"
            await asyncio.gather(*(command("resume", {"drone_id": name}) for name in NAMES))
            await until(
                fleet,
                lambda states: all(
                    s["mission_current"] >= 12
                    and s["ground_speed_mps"] < 0.5
                    and math.dist(
                        (s["north_m"], s["east_m"]),
                        (routes[s["id"]][-1]["north_m"], routes[s["id"]][-1]["east_m"]),
                    )
                    < 2
                    for s in states
                ),
                700 / speed + 120,
                "twelve waypoints completed",
            )
            assert all(set(range(1, 13)) <= sequences[name] for name in NAMES), sequences
            phase["name"] = "sustained_hover"
            while (
                time.monotonic() - start_wall < seconds
                or min(
                    (latest[name]["simulator_timestamp_ns"] - start_sim[name]) / 1e9
                    for name in NAMES
                )
                < seconds
            ):
                assert all(v["airborne"] for v in latest.values()), latest
                assert time.monotonic() - start_wall < max(seconds * 2, 300), (
                    "Simulator did not sustain requested time"
                )
                await asyncio.sleep(0.1)
            report["sustained_wall_s"] = time.monotonic() - start_wall
            report["sustained_simulator_s"] = {
                name: (latest[name]["simulator_timestamp_ns"] - start_sim[name]) / 1e9
                for name in NAMES
            }
            phase["name"] = "rtl"
            await asyncio.gather(*(command("return_home", {"drone_id": name}) for name in NAMES))

            def landed(states):
                return all(
                    not s["armed"] and s["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND
                    for s in states
                )

            await until(fleet, landed, 360, "RTL land and disarm")
            phase.update(name="land_takeoff", sortie=2)
            await takeoff((10, 10, 10, 10))
            await asyncio.sleep(5)
            phase["name"] = "land"
            await asyncio.gather(*(command("land", {"drone_id": name}) for name in NAMES))
            await until(fleet, landed, 120, "LAND disarm")

        monitor = asyncio.create_task(observe())
        flight = asyncio.create_task(sequence())
        done, _ = await asyncio.wait((monitor, flight), return_when=asyncio.FIRST_COMPLETED)
        if monitor in done:
            await monitor
            raise RuntimeError("Pose monitor stopped")
        await flight
        report["passed"] = True
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        report["failure_telemetry"] = diagnostics(fleet)
        event({"kind": "gate_failure", **report["failure"]})
        raise
    finally:
        for task in (monitor, flight):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(t for t in (monitor, flight) if t is not None), return_exceptions=True
        )
        if "failure" in report:
            started = time.monotonic()
            await asyncio.sleep(2)
            report["failure_telemetry_drain_s"] = time.monotonic() - started
        report["final_telemetry"] = diagnostics(fleet)
        for task in fleet.tasks:
            task.cancel()
        await asyncio.gather(*fleet.tasks, return_exceptions=True)
        for stream in readers:
            stream.close()
        executor.shutdown(wait=True)
        samples.close()
        report["position_error_m"] = {
            name: {
                label: {
                    "count": len(values),
                    "p50": float(np.percentile(values, 50)) if values else None,
                    "p95": float(np.percentile(values, 95)) if values else None,
                    "max": max(values) if values else None,
                }
                for label, values in groups.items()
            }
            for name, groups in errors.items()
        }
        report["mission_sequences_seen"] = {
            name: sorted(values) for name, values in sequences.items()
        }
        report["position_error_pass"] = all(
            values and max(values) < limit
            for groups in errors.values()
            for label, limit in (("hover", 1), ("cruise", 2))
            for values in (groups[label],)
        )
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        marker = output / "ready-to-stop-world.json"
        marker.with_suffix(".tmp").write_text(
            json.dumps(
                {
                    "flight_sequence_complete": report["passed"],
                    "report": str(output / "report.json"),
                }
            )
            + "\n"
        )
        marker.with_suffix(".tmp").replace(marker)
        print(f"Ready to stop task-owned world: {marker}", flush=True)
        deadline = time.monotonic() + 60
        while not shutdown_file.exists():
            assert time.monotonic() < deadline, "World shutdown acknowledgement timed out"
            await asyncio.sleep(0.1)
        await fleet.close()
        report["autopilot_exit_codes"] = {
            name: v["process"].returncode for name, v in fleet.vehicles.items()
        }
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        event_log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shutdown-file", type=Path, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--speed", type=float, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.output, args.shutdown_file, args.seconds, args.speed))

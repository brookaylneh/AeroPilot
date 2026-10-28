"""Serve the real mission UI, then fly and measure the rectangular-world acceptance."""

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
from websockets.asyncio.server import serve

from mission_planner.environment import load_env
from mission_planner.gcs.coupling_gate import command_batch, diagnostics, truth, until
from mission_planner.gcs.fleet import MAV
from mission_planner.gcs.geodesy import NAMES, ORIGIN, fence_vertices, rtl_route
from mission_planner.gcs.service import Mission, read_case
from mission_planner.gcs.sitl import check_ports
from mission_planner.gcs.validation import validate_route, validate_separation
from world.cosys.terrain import BOUNDS

ALTITUDES = (50, 60, 70, 80)


def routes(states):
    assert [state["id"] for state in states] == list(NAMES)
    south = BOUNDS["min"]["x"] / 100
    targets = (
        (south + 200, BOUNDS["min"]["y"] / 100 + 40),
        (south + 200, BOUNDS["max"]["y"] / 100 - 40),
        (south + 150, -150),
        (south + 150, 150),
    )
    return {
        state["id"]: [
            {"north_m": n, "east_m": e, "altitude_above_home_m": altitude}
            for n, e in (
                (south + 40, state["home"]["east_m"]),
                target,
                (target[0] + 10, target[1]),
                (south + 40, state["home"]["east_m"]),
            )
        ]
        for state, altitude, target in zip(states, ALTITUDES, targets)
    }


def validate_sortie(states, planned, readbacks):
    assert [state["id"] for state in states] == list(NAMES)
    missions = {
        state["id"]: [{**state, "altitude_above_home_m": altitude}, *planned[state["id"]]]
        for state, altitude in zip(states, ALTITUDES)
    }
    columns = {state["id"]: [state, missions[state["id"]][0]] for state in states}
    returns = {state["id"]: rtl_route({**state, **planned[state["id"]][-1]}) for state in states}
    reservations = [*missions.items(), *columns.items(), *returns.items()]
    for name in NAMES:
        validate_route(missions[name], readbacks[name])
        validate_route(returns[name][:-1], readbacks[name])
        for route in (missions[name], columns[name], returns[name]):
            validate_separation(name, route, reservations)
    return returns


async def run(case_id, output):
    load_env()
    output.mkdir(parents=True, exist_ok=False)
    port = int(os.environ["SNR_MISSION_PORT"])
    check_ports([port])
    mission = Mission(read_case(case_id))
    fleet = mission.fleet
    loop = asyncio.get_running_loop()
    main = asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, main.cancel)
    report = {
        "passed": False,
        "flightPassed": False,
        "missionId": mission.id,
        "missionDirectory": str(mission.directory),
        "poseComparison": "Latest received MAVLink versus RPC ground truth; receipt age and RPC brackets retained; not synchronized estimation error",
    }
    samples = (output / "poses.jsonl").open("x")
    errors = {name: [] for name in NAMES}
    reached = {name: [] for name in NAMES}
    phase = "waiting_for_browser"
    observer = None
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        async with serve(mission.connection, "127.0.0.1", port, max_size=32768):
            # Startup failures must keep motor replies alive until the world-stop handshake.
            await fleet.start()
            mission.fleet_ready = True
            mission.event("mode", "Four autopilots connected; fences and missions read back.")
            mission.launch(mission.run_sensor())
            states = fleet.snapshot()
            planned = routes(states)
            report["routes"] = planned
            report["fences"] = {s["id"]: s["fence"] for s in states}
            expected = fence_vertices()
            for state in states:
                assert len(state["fence"]) == len(expected)
                assert all(
                    math.dist((a["north_m"], a["east_m"]), (b["north_m"], b["east_m"])) < 0.02
                    for a, b in zip(state["fence"], expected)
                )
            report["nominalRtlRoutes"] = validate_sortie(
                states, planned, {name: fleet.vehicles[name]["fence"] for name in NAMES}
            )
            (output / "ready.json").write_text(json.dumps(report, indent=2) + "\n")
            while not (output / "start-flight").exists():
                assert mission.sensor_error is None, mission.sensor_error
                fleet.snapshot()
                await asyncio.sleep(0.1)
            native = json.loads(
                (Path(os.environ["SNR_DRONE_DATA_DIR"]) / "runtime.json").read_text()
            )
            assert native["schemaVersion"] == 1 and native["worldToMeters"] == 100
            captured = datetime.fromisoformat(native["capturedAt"].replace("Z", "+00:00"))
            assert abs((datetime.now(timezone.utc) - captured).total_seconds()) < 10
            assert native["worldOriginOffsetCm"] == dict(x=0, y=0, z=0)
            origins = {v["id"]: v["localNedOriginUeCm"] for v in native["vehicles"]}
            assert set(origins) == set(NAMES)
            report["localNedOriginsUeCm"] = origins
            client = await loop.run_in_executor(
                executor,
                lambda: airsim.MultirotorClient(
                    ip="127.0.0.1", port=int(os.environ["SNR_COSYS_PORT"]), timeout_value=5
                ),
            )

            async def observe():
                while True:
                    ground = await loop.run_in_executor(executor, truth, client)
                    records = {}
                    for state in fleet.snapshot():
                        name = state["id"]
                        assert (
                            not state["link_lost"]
                            and state["ekf_healthy"]
                            and state["gps_fix"] >= 3
                        )
                        position = fleet.vehicles[name]["messages"]["GLOBAL_POSITION_INT"]
                        age = (
                            time.monotonic() - fleet.vehicles[name]["times"]["GLOBAL_POSITION_INT"]
                        )
                        assert age < 0.5, (name, age)
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
                        error = math.dist(actual, measured)
                        if state["armed"] and state["landed_state"] != 1:
                            errors[name].append(error)
                        if state["mode"] == "AUTO":
                            reached[name].append(actual[:2])
                        records[name] = {
                            **ground[name],
                            "mavlink": position.to_dict(),
                            "mavlinkAgeSeconds": age,
                            "groundTruthNeuM": actual,
                            "mavlinkNeuM": measured,
                            "errorM": error,
                            "mode": state["mode"],
                            "armed": state["armed"],
                            "missionCurrent": state["mission_current"],
                        }
                    samples.write(
                        json.dumps({"utcSeconds": time.time(), "phase": phase, "vehicles": records})
                        + "\n"
                    )
                    samples.flush()
                    assert mission.sensor_error is None, mission.sensor_error
                    await asyncio.sleep(0.1)

            async def fly():
                nonlocal phase
                phase = "prearm"
                mask = MAV.MAV_SYS_STATUS_PREARM_CHECK | MAV.MAV_SYS_STATUS_SENSOR_GPS

                def ready_to_arm(current):
                    statuses = [fleet.vehicles[s["id"]]["messages"]["SYS_STATUS"] for s in current]
                    for status in statuses:
                        assert status.onboard_control_sensors_present & mask == mask
                        assert status.onboard_control_sensors_enabled & mask == mask
                    return all(
                        status.onboard_control_sensors_health & mask == mask for status in statuses
                    )

                await until(fleet, ready_to_arm, 30, "four GPS and prearm health flags")
                report["prearmTelemetry"] = diagnostics(fleet)
                phase = "takeoff"
                await command_batch(
                    *(
                        fleet.execute(
                            "take_off", {"drone_id": name, "altitude_above_home_m": altitude}
                        )
                        for name, altitude in zip(NAMES, ALTITUDES)
                    )
                )
                await until(
                    fleet,
                    lambda current: all(
                        s["armed"]
                        and s["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR
                        and abs(s["altitude_above_home_m"] - a) < 1
                        for s, a in zip(current, ALTITUDES)
                    ),
                    120,
                    "four takeoffs",
                )
                phase = "auto"
                await command_batch(
                    *(
                        fleet.execute(
                            "fly_route",
                            {"drone_id": name, "waypoints": planned[name], "speed_mps": 15},
                        )
                        for name in NAMES
                    )
                )
                await until(
                    fleet,
                    lambda current: all(
                        s["mode"] == "AUTO"
                        and s["mission_current"] >= len(planned[s["id"]])
                        and s["ground_speed_mps"] < 0.5
                        and math.dist(
                            (s["north_m"], s["east_m"]),
                            (planned[s["id"]][-1]["north_m"], planned[s["id"]][-1]["east_m"]),
                        )
                        < 2
                        for s in current
                    ),
                    300,
                    "four AUTO routes completed",
                )
                phase = "rtl"
                await command_batch(
                    *(fleet.execute("return_home", {"drone_id": name}) for name in NAMES)
                )
                await until(
                    fleet,
                    lambda current: all(not s["armed"] and s["landed_state"] == 1 for s in current),
                    360,
                    "four RTL landings",
                )

            observer = asyncio.create_task(observe())
            flight = asyncio.create_task(fly())
            try:
                done, _ = await asyncio.wait(
                    (observer, flight), return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    await task
                assert flight.done(), "Pose observer stopped before flight"
            finally:
                flight.cancel()
                observer.cancel()
                await asyncio.gather(flight, observer, return_exceptions=True)
            report["closestEastWestEdgeM"] = {
                name: min(
                    min(abs(e - BOUNDS[bound]["y"] / 100) for bound in ("min", "max"))
                    for _, e in points
                )
                for name, points in reached.items()
            }
            assert report["closestEastWestEdgeM"]["Drone1"] <= 50
            assert report["closestEastWestEdgeM"]["Drone2"] <= 50
            assert all(errors[name] and reached[name] for name in NAMES)
            report["flightPassed"] = True
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        if observer is not None:
            observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)
        executor.shutdown(wait=True)
        samples.close()
        report["positionErrorM"] = {
            name: {
                "count": len(values),
                "p50": float(np.percentile(values, 50)),
                "p95": float(np.percentile(values, 95)),
                "max": max(values),
            }
            for name, values in errors.items()
            if values
        }
        report["finalTelemetry"] = diagnostics(fleet)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "ready-to-stop-world").touch(exist_ok=False)
        try:
            try:
                deadline = time.monotonic() + 60
                while not (output / "world-stopped").exists():
                    assert time.monotonic() < deadline, "World shutdown acknowledgement timed out"
                    await asyncio.sleep(0.1)
            finally:
                await mission.close()
            report["passed"] = report["flightPassed"]
        except BaseException as error:
            report["failure"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            report["autopilotExitCodes"] = {
                name: vehicle["process"].returncode for name, vehicle in fleet.vehicles.items()
            }
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.case, args.output))

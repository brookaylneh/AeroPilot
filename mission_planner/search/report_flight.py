"""Reduce one recorded browser flight and its DataFlash logs; never command aircraft."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from pymavlink import mavutil

from mission_planner.environment import ROOT, load_env
from mission_planner.gcs.geodesy import MIN_SEPARATION_M, NAMES
from mission_planner.search.detection import AGL_M
from mission_planner.search.timing import PARAMETERS
from world.cosys.terrain import WORLD, terrain_height


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataflash(path):
    log = mavutil.mavlink_connection(str(path))
    events = {28: [], 18: []}
    reached, parameters, firmware = {}, {name: [] for name in PARAMETERS}, set()
    while (message := log.recv_match(type=["EV", "MSG", "PARM"])) is not None:
        record = message.to_dict()
        if message.get_type() == "EV" and record["Id"] in events:
            events[record["Id"]].append(record["TimeUS"] / 1e6)
        elif message.get_type() == "PARM" and record["Name"] in parameters:
            parameters[record["Name"]].append(record["Value"])
        elif message.get_type() == "MSG":
            text = record["Message"]
            if text.startswith("Reached command #"):
                sequence = int(text.split("#")[1])
                assert sequence not in reached, "A mission waypoint was reached twice"
                reached[sequence] = record["TimeUS"] / 1e6
            elif text.startswith("ArduCopter V"):
                firmware.add(text)
    assert all(len(times) == 1 for times in events.values()), "Expected one complete flight"
    assert len(firmware) == 1
    for name, values in parameters.items():
        assert values and set(values) == {PARAMETERS[name]}, f"Timing parameter differs: {name}"
    return {
        "airborne_s": events[18][0] - events[28][0],
        "reached": reached,
        "parameters": {name: values[0] for name, values in parameters.items()},
        "firmware": next(iter(firmware)),
    }


def waypoint_sequence(route, point):
    fields = ("north_m", "east_m", "altitude_above_home_m")
    indices = [
        i + 1
        for i, waypoint in enumerate(route)
        if all(abs(waypoint[k] - point[k]) < 1e-6 for k in fields)
    ]
    assert len(indices) == 1, "Search endpoint must identify exactly one mission waypoint"
    return indices[0]


def duration(planned, measured):
    assert all(math.isfinite(value) and value > 0 for value in (planned, measured))
    error = abs(measured - planned) / planned
    return {
        "planned_s": planned,
        "measured_s": measured,
        "relative_error": error,
        "within_ten_percent": error <= 0.10,
    }


def tracking(states, start, end, home_z):
    assert 0 < start < end
    selected = [
        state
        for state in states
        if state["mode"] == "AUTO"
        and state["mission_current"] is not None
        and start < state["mission_current"] <= end
    ]
    assert selected, "No recorded telemetry inside this search leg"
    agl = np.array(
        [
            state["altitude_above_home_m"]
            + home_z
            - terrain_height(state["north_m"], state["east_m"])
            for state in selected
        ]
    )
    speed = np.array([state["ground_speed_mps"] for state in selected])
    assert np.isfinite(agl).all() and np.isfinite(speed).all() and (speed >= 0).all()
    return {
        "samples": len(selected),
        "min_agl_m": float(agl.min()),
        "max_agl_m": float(agl.max()),
        "max_agl_error_m": float(np.abs(agl - AGL_M).max()),
        "speed_p50_mps": float(np.median(speed)),
        "speed_min_mps": float(speed.min()),
        "speed_max_mps": float(speed.max()),
    }


def reduce(run):
    source = run / "results.json"
    data = json.loads(source.read_text())
    assert data["passed"], "Browser flight did not pass"
    plan = data["search"]["plan"]
    assert set(plan["routes"]) == set(NAMES)
    samples = data["samples"]
    intervals = np.diff([sample["at_s"] for sample in samples])
    assert len(intervals) and np.isfinite(intervals).all() and (intervals > 0).all()
    inputs = [
        source,
        ROOT / "world/cosys/world.json",
        ROOT / ".local/vendor/open-meadow/height.r16",
    ]
    files, drones, separation = {str(path): sha256(path) for path in inputs}, {}, []
    for sample in samples:
        vehicles = sample["vehicles"]
        for i, a in enumerate(vehicles):
            for b in vehicles[i + 1 :]:
                separation.append(
                    math.dist(
                        [
                            a["north_m"],
                            a["east_m"],
                            a["altitude_above_home_m"]
                            + WORLD["vehicles"][a["id"]]["spawnUeCm"]["z"] / 100,
                        ],
                        [
                            b["north_m"],
                            b["east_m"],
                            b["altitude_above_home_m"]
                            + WORLD["vehicles"][b["id"]]["spawnUeCm"]["z"] / 100,
                        ],
                    )
                )
    assert separation and all(math.isfinite(value) for value in separation)
    for name, route in plan["routes"].items():
        directory = ROOT / ".local/data/missions" / data["mission_id"] / "sitl" / name / "logs"
        logs = list(directory.glob("*.BIN"))
        assert len(logs) == 1, f"Expected exactly one DataFlash flight log for {name}"
        flight = dataflash(logs[0])
        files[str(logs[0])] = sha256(logs[0])
        states = [v for sample in samples for v in sample["vehicles"] if v["id"] == name]
        legs = []
        for leg in route["legs"]:
            start = waypoint_sequence(route["waypoints"], leg["start"])
            end = waypoint_sequence(route["waypoints"], leg["end"])
            measured = flight["reached"][end] - flight["reached"][start]
            legs.append(
                {
                    "start_sequence": start,
                    "end_sequence": end,
                    "duration": duration(leg["ends_s"] - leg["starts_s"], measured),
                    "tracking": tracking(
                        states, start, end, WORLD["vehicles"][name]["spawnUeCm"]["z"] / 100
                    ),
                }
            )
        drones[name] = {
            "airborne": duration(route["airborne_seconds"], flight["airborne_s"]),
            "waypoint_count": len(route["waypoints"]),
            "legs": legs,
            "firmware": flight["firmware"],
            "parameters": flight["parameters"],
        }
    times_pass = all(
        drone["airborne"]["within_ten_percent"]
        and all(leg["duration"]["within_ten_percent"] for leg in drone["legs"])
        for drone in drones.values()
    )
    tracking_pass = all(
        leg["tracking"]["max_agl_error_m"] <= 2
        for drone in drones.values()
        for leg in drone["legs"]
    )
    return {
        "passed": times_pass and tracking_pass and min(separation) >= MIN_SEPARATION_M,
        "timing_passed": times_pass,
        "tracking_passed": tracking_pass,
        "mission_id": data["mission_id"],
        "plan_id": plan["plan_id"],
        "drones": drones,
        "minimum_sampled_separation_m": min(separation),
        "samples": len(samples),
        "sample_interval_s": {"min": float(intervals.min()), "max": float(intervals.max())},
        "definitions": {
            "airborne": "DataFlash NOT_LANDED (28) to LAND_COMPLETE (18)",
            "leg_duration": "DataFlash Reached command at start to Reached command at end; home is sequence zero",
            "tracking": "AUTO browser samples: start sequence < current target sequence <= end sequence; excludes ingress",
            "agl": "Altitude above home plus recorded world spawn height minus public terrain heightfield; metres",
        },
        "input_sha256": files,
        "source_sha256": {
            str(path): sha256(path)
            for path in [
                Path(__file__),
                *(
                    ROOT / "mission_planner/search" / name
                    for name in ("planner.py", "terrain.py", "timing.py", "execution.py")
                ),
            ]
        },
    }


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), "Flight reports are immutable; use a new output"
    result = reduce(args.run)
    with args.output.open("x") as handle:
        handle.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    assert result["passed"], f"Flight acceptance failed; measured results saved to {args.output}"
    print(
        json.dumps({"passed": True, "mission_id": result["mission_id"], "output": str(args.output)})
    )


if __name__ == "__main__":
    main()

"""Fly four separated SimpleFlight paths and capture fixed 4K sensors for render measurement."""

import argparse
import fcntl
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mission_planner.environment import load_env
from world.cosys.client import (
    DATA,
    NAMES,
    capture,
    concurrent,
    configure_capture,
    connect,
    home,
    land,
    set_view,
    state,
    takeoff,
    terrain_height,
    to_local,
    validate_waypoint,
    write_json,
)


def run(output, hz):
    output.mkdir()
    period = 1 / hz
    stop = threading.Event()
    report = {
        "controller": "Cosys SimpleFlight",
        "clock": {"unixSeconds": time.time(), "monotonicSeconds": time.monotonic()},
        "captureHzPerDrone": hz,
        "captureResolution": [3840, 2160],
        "capturePhaseSeconds": {n: i * period / 4 for i, n in enumerate(NAMES)},
        "status": "preparing",
        "flight": {n: [] for n in NAMES},
        "captures": {n: [] for n in NAMES},
    }
    before = concurrent(lambda name: state(connect(), name))
    assert all(s["landed"] and not s["apiControl"] for s in before.values()), before
    report["before"] = before
    controlled = []
    points = {}
    for i, name in enumerate(NAMES):
        spawn = home(name)
        height = max(terrain_height(x, spawn["y"]) for x in range(-780, -729, 5)) + 35 + i * 12
        points[name] = dict(x=-780, y=spawn["y"], z=height)
        validate_waypoint(points[name])
        validate_waypoint({**points[name], "x": -730})
    report["flightStartWorldM"] = points
    write_json(output / "result.json", report)

    def position(name):
        c = connect()
        initial = state(c, name)
        assert initial["landed"] and not initial["apiControl"], initial
        set_view(c, name, -90, 0)
        controlled.append(name)
        takeoff(name)
        p = to_local(name, {**home(name), "z": points[name]["z"]})
        assert c.moveToPositionAsync(
            **dict(x=p["x"], y=p["y"], z=p["z"], velocity=4, timeout_sec=45, vehicle_name=name)
        ).get()
        p = to_local(name, points[name])
        assert c.moveToPositionAsync(
            p["x"], p["y"], p["z"], 4, timeout_sec=45, vehicle_name=name
        ).get()
        return state(c, name)

    def flight(name):
        c = connect()
        z = to_local(name, points[name])["z"]
        step = 0
        while not stop.is_set():
            vx = 2 if (step // 20) % 2 == 0 else -2
            assert c.moveByVelocityZAsync(vx, 0, z, 1, vehicle_name=name).get()
            sample = state(c, name)
            assert not sample["landed"], sample
            p = sample["positionWorldM"]
            assert -790 < p["x"] < -725 and abs(p["z"] - points[name]["z"]) < 2, sample
            report["flight"][name].append(sample)
            step += 1
        assert c.moveByVelocityZAsync(0, 0, z, 1, vehicle_name=name).get()

    def images(name, index, start):
        number = 0
        while not stop.is_set():
            scheduled = start + index * period / 4 + number * period
            if stop.wait(max(0, scheduled - time.monotonic())):
                break
            began = time.monotonic()
            info = capture(name, output / "sensors", f"{number:05d}", jpeg_quality=95)
            assert [info["width"], info["height"]] == [3840, 2160], info
            report["captures"][name].append(
                {
                    "scheduledMonotonicSeconds": scheduled,
                    "startedMonotonicSeconds": began,
                    "completedMonotonicSeconds": time.monotonic(),
                    "latenessSeconds": began - scheduled,
                    "capture": info,
                }
            )
            number += 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        try:
            configure_capture(2160)
            report["positioned"] = concurrent(position)
            flights = [pool.submit(flight, n) for n in NAMES]
            report["status"] = "ready"
            write_json(output / "result.json", report)
            print(json.dumps({"status": "ready", "output": str(output)}), flush=True)
            assert sys.stdin.readline().strip() == "capture", "Expected capture command"
            report["captureStartedMonotonicSeconds"] = time.monotonic()
            images_started = [
                pool.submit(images, n, i, report["captureStartedMonotonicSeconds"])
                for i, n in enumerate(NAMES)
            ]
            assert sys.stdin.readline().strip() == "stop", "Expected stop command"
            stop.set()
            report["captureStoppedMonotonicSeconds"] = time.monotonic()
            for future in flights + images_started:
                future.result()
            report["status"] = "measured"
        except Exception as error:
            report["status"] = "failed"
            report["failure"] = str(error)
            raise RuntimeError(f"Four-drone render workload failed; evidence: {output}") from error
        finally:
            stop.set()
            pool.shutdown(wait=True)
            try:
                report["landed"] = concurrent(land, names=controlled)
            finally:
                write_json(output / "result.json", report)
    print(
        json.dumps(
            {"status": report["status"], "captures": {n: len(report["captures"][n]) for n in NAMES}}
        ),
        flush=True,
    )


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("capture_hz", type=float)
    args = parser.parse_args()
    assert math.isfinite(args.capture_hz) and 0.1 <= args.capture_hz <= 5
    with (DATA / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.output, args.capture_hz)


if __name__ == "__main__":
    main()

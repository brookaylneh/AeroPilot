"""Deterministic engineering tests, not search assignments or perception."""

import argparse
import fcntl
import json
import math
import threading
import time
import traceback
from pathlib import Path

from client import (
    DATA,
    NAMES,
    ROOT,
    WORLD,
    all_states,
    capture,
    concurrent,
    configure_capture,
    connect,
    distance,
    entry_axis,
    home,
    land,
    move,
    native,
    set_view,
    state,
    takeoff,
    terrain_height,
    to_world,
    write_json,
)


def run_flight_tests(output, extended_private_evaluation=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    axis = entry_axis()
    cross = "y" if axis == "x" else "x"
    boundary = WORLD["searchBoundsCm"]["min"][axis] / 100
    report = {
        "schemaVersion": 1,
        "infrastructureVersion": WORLD["infrastructureVersion"],
        "portBoundary": WORLD["port"]["boundary"],
        "startedAt": time.time(),
        "purpose": "Engineering validation; waypoints have no mission meaning",
        "tests": {},
        "passed": False,
    }

    def save(label, result):
        report["tests"][label] = result
        write_json(output / "flight-tests.json", report)
        print(label, "PASS", flush=True)

    c = connect()
    assert c.ping()
    assert sorted(c.listVehicles()) == list(NAMES)
    before = all_states()
    runtime = native()
    for n, s in before.items():
        assert s["landed"], (n, "not landed")
        assert distance(s["positionWorldM"], home(n)) < 0.35, (n, s["positionWorldM"], home(n))
        assert s["positionWorldM"][axis] < boundary
        yaw = math.radians(WORLD["vehicles"][n]["yawDegrees"]) / 2
        assert (
            abs(s["orientationNed"]["z"] - math.sin(yaw)) < 0.01
            and abs(s["orientationNed"]["w"] - math.cos(yaw)) < 0.01
        )
        physical = next(v for v in runtime["vehicles"] if v["id"] == n)
        assert (
            distance({k: physical["positionUeCm"][k] / 100 for k in "xyz"}, s["positionWorldM"])
            < 0.03
        )
    assert runtime["worldOriginOffsetCm"] == dict(x=0, y=0, z=0)
    save("A_initialization", {"states": before, "runtime": runtime, "uniqueNames": list(NAMES)})
    trajectory = []
    stop = threading.Event()

    def monitor():
        monitor_client = connect()
        while not stop.is_set():
            try:
                states = {n: state(monitor_client, n) for n in NAMES}
                pairs = [
                    distance(states[a]["positionWorldM"], states[b]["positionWorldM"])
                    for i, a in enumerate(NAMES)
                    for b in NAMES[i + 1 :]
                ]
                trajectory.append(
                    {"wallTime": time.time(), "states": states, "minimumSeparationM": min(pairs)}
                )
            except Exception as e:
                trajectory.append({"error": str(e)})
            stop.wait(0.4)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        independent = {}
        for name in NAMES:
            baseline = all_states()
            takeoff(name)
            target = home(name)
            target["z"] += 8
            target[axis] += 2
            reached = move(name, target, 3)
            during = all_states()
            drift = {
                n: distance(baseline[n]["positionWorldM"], during[n]["positionWorldM"])
                for n in NAMES
                if n != name
            }
            assert max(drift.values()) < 0.15, drift
            move(name, {**home(name), "z": home(name)["z"] + 5}, 3)
            landed = land(name)
            independent[name] = {"reached": reached, "otherVehicleDriftM": drift, "landing": landed}
            print("Independent control", name, "verified", flush=True)
        save("B_independent_control", independent)
        departures = {}
        for i, name in enumerate(NAMES):
            takeoff(name)
            departures[name] = {**home(name), "z": home(name)["z"] + 20 + 6 * i}
            move(name, departures[name])
        save("C_coordinated_launch", {"states": all_states(), "departureWorldM": departures})
        waypoints = {
            name: {cross: -60 + 40 * i, axis: boundary + 40 + 15 * i, "z": departures[name]["z"]}
            for i, name in enumerate(NAMES)
        }
        for p in waypoints.values():
            assert p["z"] - terrain_height(p["x"], p["y"]) > 10
        reached = concurrent(lambda n: move(n, waypoints[n], 6))
        assert all(v["state"]["positionWorldM"][axis] > boundary for v in reached.values())
        save(
            "D_boundary_crossing",
            {
                "boundary": WORLD["port"]["boundary"],
                "worldAxis": axis,
                "boundaryWorldMetres": boundary,
                "states": all_states(),
            },
        )
        time.sleep(3)
        hovering = all_states()
        for n in NAMES:
            assert distance(hovering[n]["positionWorldM"], waypoints[n]) < 1.2
        save(
            "E_simultaneous_waypoints",
            {"waypointsAreTemporaryTestConditions": True, "reached": reached, "hover": hovering},
        )
        configure_capture(2160)
        for name in NAMES:
            set_view(c, name, -90)
        # Warm temporal history once before preserving frames.
        for name in NAMES:
            capture(name, output / "warmup", "warmup")
        start = time.perf_counter()
        frames = concurrent(lambda n: capture(n, output / "observations", "four-drone-4k"))
        for n, frame in frames.items():
            assert (frame["width"], frame["height"]) == (3840, 2160) and frame["timestampNs"] > 0
            assert (
                frame["vehicleStateBefore"]["timestampNs"]
                <= frame["timestampNs"]
                <= frame["vehicleStateAfter"]["timestampNs"]
            )
            assert (
                0.3
                < distance(
                    frame["cameraPositionWorldM"], frame["vehicleStateBefore"]["positionWorldM"]
                )
                < 0.8
            )
        save(
            "F_four_camera_acquisition",
            {
                "frames": frames,
                "batchWallSeconds": time.perf_counter() - start,
                "simultaneousRequests": True,
            },
        )
        second = dict(waypoints["Drone1"])
        second[cross] += 12
        move("Drone1", second, 4)
        set_view(c, "Drone1", -45, 20)
        capture("Drone1", output / "warmup", "changed-warmup")
        changed = capture("Drone1", output / "observations", "changed-oblique")
        assert changed["sha256"] != frames["Drone1"]["sha256"]
        assert (
            distance(changed["cameraPositionWorldM"], frames["Drone1"]["cameraPositionWorldM"]) > 8
        )
        qa = changed["cameraPoseLocalNed"]["orientation"]
        qb = frames["Drone1"]["cameraPoseLocalNed"]["orientation"]
        angle = math.degrees(2 * math.acos(min(1, abs(sum(qa[k] * qb[k] for k in "wxyz")))))
        assert angle > 30
        save("G_changed_observation", {"frame": changed, "cameraRotationDifferenceDegrees": angle})
        if extended_private_evaluation:
            # Explicitly invoked offline evaluation only. The ordinary browser
            # flight test never reads private target positions or these results.
            import benchmark
            import visibility

            performance = benchmark.run(output / "camera-benchmark", manage_flight=False)
            evaluation = visibility.run(manage_flight=False)
            report["extendedChecks"] = {
                "cameraBenchmarkPassed": performance["passed"],
                "controlledVisibilityAcquisitionPassed": evaluation["passed"],
            }
        concurrent(lambda n: move(n, departures[n], 6))
        lands = concurrent(land)
        for n, s in lands.items():
            assert s["landed"]
            assert (
                math.hypot(
                    s["positionWorldM"]["x"] - home(n)["x"], s["positionWorldM"]["y"] - home(n)["y"]
                )
                < 0.6
            )
        save("H_return_and_land", lands)
        monitor_errors = [s["error"] for s in trajectory if "error" in s]
        assert not monitor_errors, ("Telemetry failed during flight tests", monitor_errors)
        samples = [s for s in trajectory if "states" in s]
        minimum = min(s["minimumSeparationM"] for s in samples)
        assert minimum > 3.5, minimum
        aircraft_hits = [
            (s["wallTime"], n, v["collision"])
            for s in samples
            for n, v in s["states"].items()
            if v["collision"]["objectName"].startswith("Drone")
        ]
        assert not aircraft_hits, aircraft_hits
        events = []
        seen = set()
        port = WORLD["port"]["centerUeCm"]
        for sample in samples:
            for n, s in sample["states"].items():
                hit = s["collision"]
                key = (n, hit["timestampNs"])
                if hit["timestampNs"] and key not in seen:
                    seen.add(key)
                    point = to_world(n, hit["impactPointLocalNedM"])
                    assert (
                        abs(point["x"] - port["x"] / 100) <= 7.1
                        and abs(point["y"] - port["y"] / 100) <= 7.1
                        and abs(point["z"] - port["z"] / 100) < 0.2
                    ), (n, "Unexpected contact away from platform", point)
                    events.append(
                        {
                            "vehicleId": n,
                            "timestampNs": hit["timestampNs"],
                            "objectName": hit["objectName"],
                            "pointWorldM": point,
                        }
                    )
        report["verifiedPlatformContacts"] = events
        report.update(
            passed=True,
            minimumSeparationMetres=minimum,
            monitorSamples=len(samples),
            finishedAt=time.time(),
        )
    except Exception as e:
        report["failure"] = str(e)
        report["traceback"] = traceback.format_exc()
        print(report["traceback"], flush=True)
        # Recover through normal flight APIs, keeping each aircraft at its own safe height.
        recovery = {}
        for i, name in enumerate(NAMES):
            try:
                s = state(c, name)
                if not s["landed"]:
                    p = {
                        **home(name),
                        "z": max(home(name)["z"] + 12 + 6 * i, s["positionWorldM"]["z"]),
                    }
                    move(name, p, 4)
                    recovery[name] = land(name)
            except Exception as problem:
                recovery[name] = {"error": str(problem)}
        report["recovery"] = recovery
        raise
    finally:
        stop.set()
        thread.join(timeout=5)
        write_json(output / "flight-tests.json", report)
        (output / "flight-trajectory.json").write_text(json.dumps(trajectory) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "evidence/phase2a"))
    parser.add_argument("--extended-private-evaluation", action="store_true")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "controller.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_flight_tests(args.output, args.extended_private_evaluation)

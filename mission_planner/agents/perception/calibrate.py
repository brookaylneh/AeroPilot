"""Private calibration through the coupled mission fleet; failed outputs never resume."""

import argparse
import asyncio
import fcntl
import json
import math
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import numpy as np

from mission_planner.environment import ROOT, load_env

CONDITIONS = (
    [
        (p, p, "rust", "waiting", "none", 2160)
        for p in [
            p["id"] for p in json.loads((ROOT / "world/unreal/creator-people.json").read_text())
        ]
    ]
    + [
        (f"{p}-{color}", p, color, "waiting", "none", 2160)
        for p in ("adult-kelvin", "child-01")
        for color in ("blue", "forest", "sand", "charcoal", "cream")
    ]
    + [
        (f"{p}-{situation}", p, "rust", situation, "none", 2160)
        for p in ("adult-kelvin", "child-01")
        for situation in ("lost", "injured")
    ]
    + [
        ("adult-kelvin-daypack", "adult-kelvin", "rust", "waiting", "daypack", 2160),
        ("adult-kelvin-native", "adult-kelvin", "rust", "waiting", "none", 1080),
        ("child-01-native", "child-01", "rust", "waiting", "none", 1080),
    ]
)
assert len(CONDITIONS) == len({c[0] for c in CONDITIONS}) == 26
OFFSETS, ALONG = list(range(0, 25, 4)), list(range(-36, 37, 12))


async def scenario_response(process, log):
    line = await asyncio.wait_for(process.stdout.readline(), 90)
    if not line:
        code = await asyncio.wait_for(process.wait(), 30)
        raise RuntimeError(
            f"Scenario setup exited {code} before configuration: {log.read_text().strip()}"
        )
    return json.loads(line)


async def close_scenario(process, log, original_error):
    process.stdin.close()
    try:
        code = await asyncio.wait_for(process.wait(), 30)
    except asyncio.TimeoutError as error:
        detail = "Scenario restoration exceeded 30 s"
        if original_error is not None:
            raise TimeoutError(
                f"{type(original_error).__name__}: {original_error}; {detail}"
            ) from original_error
        raise TimeoutError(detail) from error
    if code != 0:
        detail = f"Scenario process exited {code}: {log.read_text().strip()}"
        if original_error is not None:
            raise RuntimeError(
                f"{type(original_error).__name__}: {original_error}; {detail}"
            ) from original_error
        raise RuntimeError(detail)


async def condition_flight(fleet, condition, scenario_id, directory):
    from mission_planner.agents.perception.geometry import Camera
    from mission_planner.gcs.fleet import MAV
    from world.cosys import client as sim
    from world.cosys import flight
    from world.cosys.capture import FrameCapture

    label, person, _, situation, _, height = condition
    assert not directory.exists(), "Archive a failed condition explicitly before rerunning by name"
    assert shutil.disk_usage(ROOT).free > 40 * 1024**3, "40 GiB capture reserve required"
    name = "Drone1"
    assert all(
        not s["armed"] and s["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND
        for s in fleet.snapshot()
    )
    runtime = sim.native()
    assert runtime["worldToMeters"] == 100 and runtime["worldOriginOffsetCm"] == dict(x=0, y=0, z=0)
    presets = json.loads((ROOT / "world/unreal/creator-people.json").read_text())
    preset = next(p for p in presets if p["id"] == person)
    assert situation in preset["supportedSituations"]
    scenario_log = directory.parent / f"{label}-scenario.log"
    with scenario_log.open("x") as log:
        process = await asyncio.create_subprocess_exec(
            "node",
            "mission_planner/agents/perception/calibration-scenario.mjs",
            scenario_id,
            json.dumps(condition),
            cwd=ROOT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=log,
        )
    frames = []
    try:
        configured = await scenario_response(process, scenario_log)

        def scene_actor():
            rpc = sim.connect()
            assert rpc.ping() and not rpc.simIsPause()
            actors = rpc.simListSceneObjects(".*BP_Person_.*")
            assert len(actors) == 1 and "_C_" in actors[0], actors
            sim.set_view(rpc, name, -90)
            return actors[0]

        actor = await asyncio.to_thread(scene_actor)

        def observe(rpc):
            pose = sim.pose(rpc.simGetObjectPose(actor))
            point = sim.to_world(name, pose["position"], runtime["globalNedOriginUeCm"])
            assert all(math.isfinite(v) for v in point.values())
            assert abs(point["z"] - sim.terrain_height(point["x"], point["y"])) < 0.15
            return dict(actor=actor, poseGlobalNed=pose, groundWorldM=point)

        target = {k: configured["authoring"]["behavior"]["origin"][k] / 100 for k in "xyz"}
        assert math.hypot(target["x"] - sim.home(name)["x"], target["y"] - sim.home(name)["y"]) > 80
        heights = [
            sim.terrain_height(target["x"] + dx, target["y"] + dy)
            for dx in (-2, 0, 2)
            for dy in (-2, 0, 2)
        ]
        assert max(heights) - min(heights) < 0.5, "Placement is not flat meadow"
        directory.mkdir(parents=True)
        sim.write_json(
            directory / "condition.json",
            dict(
                condition=condition,
                preset=preset,
                scenario=configured,
                diskFreeBytes=shutil.disk_usage(ROOT).free,
            ),
        )
        heading = math.radians(sim.WORLD["vehicles"][name]["yawDegrees"])
        forward, lateral = (
            np.array([math.cos(heading), math.sin(heading)]),
            np.array([-math.sin(heading), math.cos(heading)]),
        )
        await asyncio.to_thread(sim.configure_capture, height)
        capture = FrameCapture(directory, fleet)
        try:
            await flight.takeoff(fleet, name, 12)
            await flight.move(fleet, name, {**sim.home(name), "z": 65}, 5)
            await flight.move(fleet, name, {**target, "z": 65}, 5)
            for offset in OFFSETS:
                for along in ALONG:
                    xy = np.array([target[k] for k in "xy"]) + forward * along + lateral * offset
                    point = dict(zip("xy", xy - forward * sim.CAMERA_FORWARD))
                    point["z"] = sim.terrain_height(*xy) + 30 + sim.CAMERA_DOWN
                    reached = await flight.move(fleet, name, point, 5)
                    frame, _ = await capture.capture(
                        name,
                        f"frame-{offset:02d}-{along:+03d}",
                        observe=observe,
                        jpeg_quality=95,
                    )
                    assert frame["validation"]["accepted"], frame["validation"]
                    assert not frame["physicsPausedForCapture"]
                    assert (frame["width"], frame["height"]) == (height * 16 // 9, height)
                    camera = Camera(frame)
                    agl = camera.world["z"] - sim.terrain_height(
                        camera.world["x"], camera.world["y"]
                    )
                    assert abs(agl - 30) < 0.5
                    delta = np.array(
                        [camera.world[k] - frame["observation"]["groundWorldM"][k] for k in "xy"]
                    )
                    frame.update(
                        offsetM=offset,
                        alongM=along,
                        actualOffsetM=float(delta @ lateral),
                        actualAlongM=float(delta @ forward),
                        aglM=agl,
                        roundTripErrorPx=camera.check(),
                        waypoint=reached,
                    )
                    sim.write_json((directory / frame["file"]).with_suffix(".json"), frame)
                    frames.append(frame)
                    print(label, offset, along, flush=True)
        finally:
            try:
                current = flight.state(fleet, name)
                if current["armed"]:
                    if current["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR:
                        await flight.move(
                            fleet, name, {**flight.world_position(current), "z": 65}, 5
                        )
                        await flight.move(fleet, name, {**sim.home(name), "z": 65}, 5)
                    await flight.land(fleet, name)
            finally:
                await capture.close()
                await asyncio.to_thread(sim.configure_capture, runtime["vehicles"][0]["height"])
    finally:
        await close_scenario(process, scenario_log, sys.exc_info()[1])
    assert len(frames) == len(OFFSETS) * len(ALONG)
    result = {
        "condition": condition,
        "frames": len(frames),
        "roundTripErrorMaxPx": max(f["roundTripErrorPx"] for f in frames),
        "aglErrorMaxM": max(abs(f["aglM"] - 30) for f in frames),
        "captureLatencyMaxSeconds": max(f["captureLatencySeconds"] for f in frames),
        "physicsPausedForCapture": False,
    }
    sim.write_json(directory / "complete.json", result)
    return result


async def run(conditions, scenario_id, output, shutdown_file):
    from mission_planner.gcs.fleet import Fleet
    from world.cosys import client as sim

    assert os.environ["SNR_SITL_MODEL"] == "airsim-copter", "Calibration requires coupled physics"
    assert not shutdown_file.exists(), "World shutdown acknowledgement already exists"
    output.mkdir(parents=True, exist_ok=False)
    report = {"scenarioId": scenario_id, "conditions": conditions, "completed": []}
    with (
        open(sim.DATA / "controller.lock", "w") as lock,
        (output / "events.jsonl").open("w") as log,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def event(value):
            log.write(json.dumps({"at_ms": time.time() * 1000, **value}) + "\n")
            log.flush()

        fleet = Fleet(output / "sitl", event)
        try:
            await fleet.start()
            for condition in conditions:
                report["completed"].append(
                    await condition_flight(fleet, condition, scenario_id, output / condition[0])
                )
        except BaseException as error:
            report["failure"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            sim.write_json(output / "report.json", report)
            marker = output / "ready-to-stop-world.json"
            sim.write_json(marker, {"report": str(output / "report.json")})
            print(f"Ready to stop task-owned world: {marker}", flush=True)
            deadline = time.monotonic() + 60
            while not shutdown_file.exists():
                assert time.monotonic() < deadline, "World shutdown acknowledgement timed out"
                await asyncio.sleep(0.1)
            await fleet.close()
            report["autopilotExitCodes"] = {
                name: v["process"].returncode for name, v in fleet.vehicles.items()
            }
            sim.write_json(output / "report.json", report)


def main():
    load_env()

    def terminate(signum, frame):
        raise SystemExit(f"Signal {signum} in {frame.f_code.co_name}; capture interrupted")

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--condition", choices=[c[0] for c in CONDITIONS])
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--list", action="store_true")
    parser.add_argument("--scenario-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--shutdown-file", type=Path)
    args = parser.parse_args()
    if args.list:
        print(json.dumps(CONDITIONS, indent=2))
        return
    assert args.scenario_id, "A saved, flat-meadow placement is required"
    assert args.output and args.shutdown_file, "Flight requires --output and --shutdown-file"
    conditions = [c for c in CONDITIONS if args.all or c[0] == args.condition]
    asyncio.run(run(conditions, args.scenario_id, args.output, args.shutdown_file))


if __name__ == "__main__":
    main()

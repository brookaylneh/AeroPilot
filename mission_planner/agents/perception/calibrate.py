"""Private, stopped-frame calibration. One condition per invocation; no resume."""

import argparse
import fcntl
import json
import math
import os
import shutil
import signal
import subprocess
import time

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
DATA = ROOT / ".local/data/calibration"


def run(condition, scenario_id):
    from mission_planner.agents.perception.geometry import Camera
    from world.cosys import client as sim

    label, person, _, situation, _, height = condition
    directory = DATA / label
    assert not directory.exists(), "Archive a failed condition explicitly before rerunning by name"
    assert shutil.disk_usage(ROOT).free > 40 * 1024**3, "40 GiB capture reserve required"
    c, name = sim.connect(), "Drone1"
    assert c.ping() and not c.simIsPause()
    assert all(sim.state(c, n)["landed"] for n in sim.NAMES)
    runtime = sim.native()
    assert runtime["worldToMeters"] == 100 and runtime["worldOriginOffsetCm"] == dict(x=0, y=0, z=0)
    presets = json.loads((ROOT / "world/unreal/creator-people.json").read_text())
    preset = next(p for p in presets if p["id"] == person)
    assert situation in preset["supportedSituations"]
    with open(sim.DATA / "controller.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with subprocess.Popen(
            [
                "node",
                "mission_planner/agents/perception/calibration-scenario.mjs",
                scenario_id,
                json.dumps(condition),
            ],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        ) as process:
            configured = json.loads(process.stdout.readline())
            actors = c.simListSceneObjects(".*BP_Person_.*")
            assert len(actors) == 1 and "_C_" in actors[0], actors

            def observe(rpc):
                assert rpc.simIsPause()
                pose = sim.pose(rpc.simGetObjectPose(actors[0]))
                point = sim.to_world(name, pose["position"], runtime["globalNedOriginUeCm"])
                assert all(math.isfinite(v) for v in point.values())
                assert abs(point["z"] - sim.terrain_height(point["x"], point["y"])) < 0.15
                return dict(actor=actors[0], poseGlobalNed=pose, groundWorldM=point)

            target = {k: configured["authoring"]["behavior"]["origin"][k] / 100 for k in "xyz"}
            assert (
                math.hypot(target["x"] - sim.home(name)["x"], target["y"] - sim.home(name)["y"])
                > 80
            )
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
            sim.configure_capture(height)
            sim.set_view(c, name, -90)
            try:
                sim.takeoff(name)
                sim.move(name, {**sim.home(name), "z": 65}, 5)
                sim.move(name, {**target, "z": 65}, 5)
                for offset in OFFSETS:
                    for along in ALONG:
                        xy = (
                            np.array([target[k] for k in "xy"]) + forward * along + lateral * offset
                        )
                        point = dict(zip("xy", xy - forward * sim.CAMERA_FORWARD))
                        point["z"] = sim.terrain_height(*xy) + 30 + sim.CAMERA_DOWN
                        reached = sim.move(name, point, 5)
                        time.sleep(1.5)
                        frame = sim.capture(
                            name,
                            directory,
                            f"frame-{offset:02d}-{along:+03d}",
                            freeze=True,
                            observe=observe,
                            jpeg_quality=95,
                        )
                        assert (frame["width"], frame["height"]) == (height * 16 // 9, height)
                        camera = Camera(frame)
                        agl = camera.world["z"] - sim.terrain_height(
                            camera.world["x"], camera.world["y"]
                        )
                        assert abs(agl - 30) < 0.5
                        delta = np.array(
                            [
                                camera.world[k] - frame["observation"]["groundWorldM"][k]
                                for k in "xy"
                            ]
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
                        print(label, offset, along, flush=True)
            finally:
                sim.move(name, {**sim.state(c, name)["positionWorldM"], "z": 65}, 5)
                sim.move(name, {**sim.home(name), "z": 65}, 5)
                sim.land(name)
                sim.configure_capture(runtime["vehicles"][0]["height"])
            process.stdin.close()
            assert process.wait(timeout=30) == 0
            sim.write_json(directory / "complete.json", dict(frames=49))


def main():
    load_env()

    def terminate(signum, frame):
        raise SystemExit(f"Signal {signum} in {frame.f_code.co_name}; capture interrupted")

    signal.signal(signal.SIGTERM, terminate)
    os.environ.update(
        SNR_DRONE_DATA_DIR=str(ROOT / ".local/data/four-drones"),
        SNR_COSYS_PORT="41451",
        SNR_NATIVE_URL="http://127.0.0.1:5173/",
    )
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--condition", choices=[c[0] for c in CONDITIONS])
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--list", action="store_true")
    parser.add_argument("--scenario-id")
    args = parser.parse_args()
    if args.list:
        print(json.dumps(CONDITIONS, indent=2))
        return
    assert args.scenario_id, "A saved, flat-meadow placement is required"
    for condition in CONDITIONS:
        if args.all or condition[0] == args.condition:
            run(condition, args.scenario_id)


if __name__ == "__main__":
    main()

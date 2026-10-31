"""Short package release check in the renderer's isolated network namespace; no inference."""

import argparse
import asyncio
import functools
import json
import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cosysairsim as airsim
import numpy as np
from PIL import Image

from mission_planner.environment import load_env
from mission_planner.gcs.fleet import Fleet
from world.cosys import client as sim
from world.cosys.landmarks import load as load_landmarks
from world.cosys.terrain import terrain_height


async def check(output, recording, compare_reaches, renderer_log):
    load_env()
    output.mkdir(parents=True, exist_ok=False)
    assert os.environ["SNR_CONTAINER_NAME"] == "snr-gpu-grass-check"
    assert int(os.environ["SNR_COSYS_PORT"]) == 41451
    assert renderer_log.is_file(), renderer_log
    assert not compare_reaches or recording
    events = (output / "fleet.jsonl").open("x")
    fleet = Fleet(
        output / "sitl", lambda e: events.write(json.dumps(e, default=str) + "\n"), load_landmarks()
    )
    pool = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()

    async def call(fn, *args, **kwargs):
        return await loop.run_in_executor(pool, functools.partial(fn, *args, **kwargs))

    telemetry = (output / "camera.jsonl").open("x")
    rows = []
    phase = "startup"

    async def sample(seconds):
        end = time.monotonic() + seconds
        previous = None
        while time.monotonic() < end:
            native = sim.native()
            if native["capturedAt"] != previous:
                row = {"at": time.time(), "phase": phase, **native}
                telemetry.write(json.dumps(row, allow_nan=False) + "\n")
                telemetry.flush()
                rows.append(row)
                previous = native["capturedAt"]
            await asyncio.sleep(0.1)

    try:
        deadline = time.monotonic() + 120
        while not subprocess.run(
            ["ss", "-H", "-ltn", "sport = :41451"], check=True, capture_output=True, timeout=5
        ).stdout:
            assert time.monotonic() < deadline, "Packaged world did not open RPC"
            await asyncio.sleep(0.5)
        await fleet.start()
        while not (sim.DATA / "runtime.json").exists():
            assert time.monotonic() < deadline, "Packaged world did not publish telemetry"
            await asyncio.sleep(0.5)
        rpc = await call(sim.connect)
        assert await call(rpc.ping)
        session = sim.native()["sessionActor"]

        async def console(command):
            assert await call(rpc.simRunConsoleCommand, command), command

        await console(f"ke {session} CameraView start")
        phase = "studio-warmup"
        await sample(20)
        assert sim.native()["grassAllocatedInstances"] > 0, "No GPU grass allocated"
        if compare_reaches:
            for factor in (0.8, 0.9, 1.0):
                await console(f"pcg.RuntimeGeneration.GlobalRadiusMultiplier {factor}")
                phase = f"reach-{factor}-settle"
                await sample(5)
                phase = f"reach-{factor}"
                await sample(10)
        elif recording:
            phase = "studio"
            await sample(10)
        else:
            phase = "studio-60s"
            await sample(60)
        await console(f"HighResShot 1 filename={output / 'studio-entry.png'}")
        phase = "studio-frame"
        await sample(2)
        assert (output / "studio-entry.png").is_file()
        if recording:
            for u, v in ((0.52, 0.72), (0.73, 0.48), (0.45, 0.32)):
                await console(f"ke {session} CameraTravel {u} {v}")
                phase = "fast-travel"
                await sample(7)
                assert not sim.native()["camera"]["transitioning"], "Map flight did not finish"
                await console(f"HighResShot 1 filename={output / f'travel-{u}-{v}.png'}")
                await sample(1)
            nav_session = str(uuid.uuid4())
            phase = "survey-20mps"
            for sequence in range(100):
                value = {
                    "protocolVersion": 3,
                    "type": "navigation",
                    "session": nav_session,
                    "sequence": sequence,
                    "forward": 1,
                    "right": 0,
                    "up": 0,
                    "yaw": 0,
                    "pitch": 0,
                    "zoom": 0,
                    "mode": "survey",
                    "boost": False,
                }
                await console(
                    f"ke {session} CameraNavigation {json.dumps(value, separators=(',', ':'))}"
                )
                await sample(0.1)
                if sequence in (0, 50, 99):
                    await console(f"HighResShot 1 filename={output / f'survey-{sequence}.png'}")
            await sample(1.5)
            speeds = [r["camera"]["speedCmPerSecond"] for r in rows if r["phase"] == phase]
            assert speeds and max(speeds) >= 1999, speeds
        await console("pcg.RuntimeGeneration.GlobalRadiusMultiplier 1")
        await call(sim.enter_mission)
        assert sim.native()["mode"] == "mission"
        north, east = -350.0, 50.0
        ground = terrain_height(north + sim.CAMERA_FORWARD, east)
        local = sim.to_local("Drone1", {"x": north, "y": east, "z": ground + 60.05})
        kinematics = airsim.KinematicsState()
        kinematics.position = airsim.Vector3r(*(local[k] for k in "xyz"))
        kinematics.orientation = airsim.Quaternionr(0, 0, 0, 1)
        for key in (
            "linear_velocity",
            "angular_velocity",
            "linear_acceleration",
            "angular_acceleration",
        ):
            setattr(kinematics, key, airsim.Vector3r(0, 0, 0))
        await call(sim.set_view, rpc, "Drone1")
        phase = "mission-60m"
        for _ in range(40):
            await call(rpc.simSetKinematics, kinematics, True, "Drone1")
            await sample(0.25)
        assert sim.native()["grassAllocatedInstances"] > 0, "Mission GPU grass did not generate"
        requests = [airsim.ImageRequest("front_center", airsim.ImageType.Scene, False, False)]
        # Each capture's independent Lumen scene needs its initial surface cache renders.
        for _ in range(3):
            await call(rpc.simSetKinematics, kinematics, True, "Drone1")
            (frame,) = await call(rpc.simGetImages, requests, vehicle_name="Drone1")
            assert not frame.message and (frame.width, frame.height) == (3840, 2160), frame.message
        pixels = np.frombuffer(frame.image_data_uint8, np.uint8).reshape(2160, 3840, 3)
        assert np.isfinite(pixels).all() and pixels.max() > pixels.min()
        await call(Image.fromarray(pixels).save, output / "drone-60m.png")
        position = sim.to_world("Drone1", sim.xyz(frame.camera_position))
        height = position["z"] - terrain_height(position["x"], position["y"])
        assert abs(height - 60) < 0.2, height
        summary = {"droneHeightM": height, "profiles": {}}
        for label in sorted(
            {
                r["phase"]
                for r in rows
                if r["phase"].startswith("reach-") and not r["phase"].endswith("settle")
            }
            | {"studio-60s"}
        ):
            selected = [r for r in rows if r["phase"] == label]
            if not selected:
                continue
            values = np.asarray(
                [[r["camera"]["timing"][k] for k in ("frameMs", "gpuMs")] for r in selected]
            )
            assert values.ndim == 2 and values.shape[1] == 2 and np.isfinite(values).all()
            summary["profiles"][label] = {
                "samples": len(selected),
                "frameMsP50": float(np.median(values[:, 0])),
                "frameMsP95": float(np.percentile(values[:, 0], 95)),
                "gpuMsP50": float(np.median(values[:, 1])),
                "gpuMsP95": float(np.percentile(values[:, 1], 95)),
                "maxAllocatedInstances": max(r["grassAllocatedInstances"] for r in selected),
                "maxSceneAllocatedInstances": max(r["sceneAllocatedInstances"] for r in selected),
            }
        log = renderer_log.read_text()
        assert "GPU-Scene Instance data overflow" not in log, "GPU Scene overflowed"
        assert "LogPCG: Error:" not in log, "PCG runtime error; inspect renderer log"
        assert "Grass map generation is disabled" not in log, "GPU landscape masks unavailable"
        (output / "result.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        log_offset = renderer_log.stat().st_size
        await call(
            subprocess.run,
            ["docker", "kill", "--signal=TERM", "snr-gpu-grass-check"],
            check=True,
            timeout=10,
        )
        deadline = time.monotonic() + 60
        while b"UWorld::CleanupWorld for OpenMeadow," not in renderer_log.read_bytes()[log_offset:]:
            assert time.monotonic() < deadline, "Unreal EndPlay marker timed out; keep SITL running"
            await asyncio.sleep(0.25)
        await fleet.close()
        telemetry.close()
        events.close()
        pool.shutdown(wait=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--recording", action="store_true")
    parser.add_argument("--compare-reaches", action="store_true")
    parser.add_argument("--renderer-log", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(
        check(
            args.output.resolve(), args.recording, args.compare_reaches, args.renderer_log.resolve()
        )
    )


if __name__ == "__main__":
    main()

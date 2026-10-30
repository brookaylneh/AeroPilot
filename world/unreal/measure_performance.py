"""Packaged-world GPU, capture and memory cost under the mission and stress workloads."""

import argparse
import asyncio
import csv
import hashlib
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from operator import itemgetter
from pathlib import Path

import cosysairsim as airsim
import numpy as np
from PIL import Image

from mission_planner.environment import load_env
from mission_planner.gcs.fleet import Fleet
from mission_planner.gcs.geodesy import ORIGIN
from mission_planner.gcs.sitl import check_ports
from world.cosys import client as sim
from world.cosys.capture import shadow_floor
from world.cosys.flight import state, takeoff, until

ROOT = Path(__file__).resolve().parents[2]
SPEED_MPS = 5.0
SPACING_M = 16.0
LANE_EAST_M = (-45.0, -15.0, 15.0, 45.0)
ALTITUDE_M = (30.0, 34.0, 38.0, 42.0)
LANE_START_NORTH_M = -760.0
FIRST_CAPTURE_NORTH_M = -740.0
LANE_END_NORTH_M = -400.0
MISSION_CAPTURES = 20
STRESS_CAPTURES = 40
SPIKE_WINDOW_S = 0.1
SAMPLE_INTERVAL_S = 0.5
PLAN_SHA256 = "8ea528fb4ebf46180ba395fbf2949bbdc96e61aa9efd135c876eb1683d7f419d"
CONTAINER = "snr-unreal-valley-art-perf"
CSV_NAME = "measure-performance"
# Scalability cvars that switch Lumen off for every view, including the opted-in captures.
LUMEN_CVARS = ("r.Lumen.DiffuseIndirect.Allow", "r.Lumen.Reflections.Allow")
LAUNCH = {
    "SNR_PIXEL_STREAMING": "0",
    "SNR_MEDIA_CAPTURE": "0",
    "SNR_STREAM_HEIGHT": "2160",
    "SNR_STREAM_FPS": "60",
    "SNR_RHI_THREAD": "1",
    "SNR_PROFILE": "0",
    "SNR_CPU_LIMIT": "6",
    "SNR_MEMORY_LIMIT": "56g",
    "SNR_CONTAINER_NAME": CONTAINER,
}


def plan():
    result = {}
    for index, name in enumerate(sim.NAMES):
        east, altitude = LANE_EAST_M[index], ALTITUDE_M[index]
        first = FIRST_CAPTURE_NORTH_M + index * SPACING_M / len(sim.NAMES)
        captures = [first + k * SPACING_M for k in range(MISSION_CAPTURES)]
        assert LANE_START_NORTH_M < first and captures[-1] + SPACING_M < LANE_END_NORTH_M
        result[name] = {
            "home": sim.WORLD["vehicles"][name]["spawnUeCm"],
            "altitudeAboveHomeM": altitude,
            "start": {
                "north_m": LANE_START_NORTH_M,
                "east_m": east,
                "altitude_above_home_m": altitude,
            },
            "end": {"north_m": LANE_END_NORTH_M, "east_m": east, "altitude_above_home_m": altitude},
            "captureNorthM": captures,
        }
    return result


def stats(values):
    v = np.asarray(values, dtype=float)
    assert v.ndim == 1 and v.size and np.isfinite(v).all()
    return {
        "count": int(v.size),
        "p50": float(np.percentile(v, 50)),
        "p95": float(np.percentile(v, 95)),
        "max": float(v.max()),
    }


def smi(query):
    return subprocess.run(
        ["nvidia-smi", query, "--format=csv,noheader,nounits"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout


def gpu_processes():
    rows = smi("--query-compute-apps=pid,process_name,used_memory").splitlines()
    return {int(p): (n, float(m)) for p, n, m in (r.split(", ") for r in rows)}


def exclusive(processes, pid):
    foreign = {n for p, (n, _) in processes.items() if p != pid}
    # The installed live world shares the GPU; any other GPU process invalidates timings.
    assert all(n.endswith("/Binaries/Linux/ScenarioStudio") for n in foreign), foreign
    return foreign


def sample_gpu(samples, stopping):
    while not stopping.wait(SAMPLE_INTERVAL_S):
        processes, load = gpu_processes(), float(smi("--query-gpu=utilization.gpu"))
        samples.append({"s": time.monotonic(), "processes": processes, "gpuPercent": load})


def ram_peak(pid, reset):
    status = Path(f"/proc/{pid}/status").read_text().splitlines()
    (line,) = [r for r in status if r.startswith("VmHWM:")]
    value, unit = line.split()[1:]
    assert unit == "kB"
    if reset:
        Path(f"/proc/{pid}/clear_refs").write_text("5")
    return int(value) * 1024


def capture(rpc, name, origin, path=None):
    start = time.monotonic()
    (response,) = rpc.simGetImages(
        [airsim.ImageRequest("front_center", airsim.ImageType.Scene, False, False)],
        vehicle_name=name,
    )
    end = time.monotonic()
    assert not response.message, response.message
    assert (response.width, response.height) == (3840, 2160)
    assert len(response.image_data_uint8) == 3840 * 2160 * 3
    if path is not None:
        pixels = np.frombuffer(response.image_data_uint8, dtype=np.uint8).reshape(2160, 3840, 3)
        Image.fromarray(pixels, "RGB").save(path)
    position = sim.to_world(name, sim.xyz(response.camera_position), origin)
    return {
        "droneId": name,
        "startS": start,
        "endS": end,
        "cameraWorldM": position,
        "shadowFloorLuminanceP1": shadow_floor(response.image_data_uint8, 3840, 2160),
    }


def frames(path, start_rpc, stop_rpc):
    """Map CSV rows to host monotonic time through the timestamped STOP console event."""
    rows = list(csv.reader(path.open()))
    header, final, metadata = rows[0], rows[-2], rows[-1]
    assert header[0] == "EVENTS" and {"FrameTime", "GPUTime"} <= set(header), header
    # Columns first seen mid-capture are appended; the end header extends the first.
    assert final[: len(header)] == header and metadata[0] == "[HasHeaderRowAtEnd]"
    data = rows[1:-2]
    frame_ms = np.array([float(r[header.index("FrameTime")]) for r in data])
    gpu_ms = np.array([float(r[header.index("GPUTime")]) for r in data])
    (stop,) = [
        (i, float(e.rsplit("##", 1)[1]))
        for i, r in enumerate(data)
        for e in r[0].split(";")
        if e.startswith("Cmd: CsvProfile STOP##")
    ]
    ends = np.cumsum(frame_ms) / 1000
    row, offset = stop
    assert ends[row] - frame_ms[row] / 1000 - 0.05 <= offset <= ends[row] + 0.05, (
        offset,
        ends[row],
    )
    origin = sum(stop_rpc) / 2 - offset
    # The CSV begins with the frame in which START ran, which can begin before the request is sent;
    # summed FrameTime misses hitches outside frame timing (0.115 s over a 9.4M-instance run).
    assert start_rpc[0] - frame_ms[0] / 1000 <= origin <= start_rpc[1] + 0.2, (start_rpc, origin)
    return {
        "frameMs": frame_ms,
        "gpuMs": gpu_ms,
        "endS": origin + ends,
        "beginS": origin + ends - frame_ms / 1000,
        "anchorUncertaintyS": (stop_rpc[1] - stop_rpc[0]) / 2,
    }


def workload(timeline, captures, gpu, peak_ram, pid):
    w0, w1 = min(c["startS"] for c in captures), max(c["endS"] for c in captures)
    inside = (timeline["beginS"] >= w0) & (timeline["endS"] <= w1)
    spikes = {"frameMs": [], "gpuMs": []}
    for c in captures:
        near = (timeline["endS"] >= c["startS"] - SPIKE_WINDOW_S) & (
            timeline["beginS"] <= c["endS"] + SPIKE_WINDOW_S
        )
        for key in spikes:
            spikes[key].append(timeline[key][near].max())
    window = [s for s in gpu if w0 <= s["s"] <= w1]
    assert len(window) >= (w1 - w0) / SAMPLE_INTERVAL_S / 2, "GPU sampler stopped"
    foreign = set().union(*(exclusive(s["processes"], pid) for s in window))
    return {
        "seconds": w1 - w0,
        "captures": len(captures),
        "fleetCaptureHz": (len(captures) - 1) / (w1 - w0),
        "gpuFrameMs": stats(timeline["gpuMs"][inside]),
        "frameMs": stats(timeline["frameMs"][inside]),
        "captureMs": stats([(c["endS"] - c["startS"]) * 1000 for c in captures]),
        "shadowFloorLuminanceP1": stats([c["shadowFloorLuminanceP1"] for c in captures]),
        "captureSpikeFrameMs": stats(spikes["frameMs"]),
        "captureSpikeGpuMs": stats(spikes["gpuMs"]),
        "vramHighWaterMiB": max(s["processes"][pid][1] for s in window),
        "vramSamples": len(window),
        "hostGpuPercent": stats([s["gpuPercent"] for s in window]),
        "foreignGpuProcesses": sorted(foreign),
        "peakRamBytes": peak_ram,
    }


async def together(*coroutines):
    """gather that cancels and awaits the siblings of a failure (asyncio.TaskGroup needs 3.11)."""
    tasks = [asyncio.ensure_future(c) for c in coroutines]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.wait(tasks)


async def measure(output, lumen):
    load_env()
    run = output.with_suffix("")
    assert output.suffix == ".json" and not output.exists() and not run.exists()
    package = Path(os.environ["SNR_PACKAGE_ROOT"]).resolve(strict=True)
    binary = package / "ScenarioStudio/Binaries/Linux/ScenarioStudio"
    for name, key in (
        ("cosys-settings.json", "SNR_COSYS_SETTINGS"),
        ("world.json", "SNR_WORLD_CONFIG"),
    ):
        assert Path(os.environ[key]).read_bytes() == (package / name).read_bytes(), key
    assert os.environ["SNR_SITL_MODEL"] == "airsim-copter"
    routes = plan()
    digest = hashlib.sha256(json.dumps(routes, sort_keys=True).encode()).hexdigest()
    assert digest == PLAN_SHA256, f"Fixed routes changed: {digest}"
    settings = json.loads((package / "cosys-settings.json").read_text())
    assert settings["ApiServerPort"] == sim.PORT
    world_ports = [sim.PORT] + [v["ControlPortLocal"] for v in settings["Vehicles"].values()]
    check_ports(world_ports)
    exclusive(gpu_processes(), None)
    mavlink, rc = (int(os.environ[k]) for k in ("SNR_MAVLINK_BASE_PORT", "SNR_SITL_RC_BASE_PORT"))
    fleet_ports = [v["UdpPort"] for v in settings["Vehicles"].values()]
    fleet_ports += [mavlink + i for i in range(4)] + [rc + 10 * i for i in range(4)]
    run.mkdir(parents=True)
    events = (run / "events.jsonl").open("x")
    fleet = Fleet(run / "sitl", lambda e: events.write(json.dumps(e, default=str) + "\n"))
    log_path = run / "unreal.log"
    environment = {**os.environ, **LAUNCH, "SNR_UNREAL_HOME": str(run / "unreal-home")}
    launched = time.time()
    world = subprocess.Popen(
        ["systemd-run", "--user", "--scope", "-p", "MemoryMax=56G", "-p", "MemorySwapMax=0"]
        + ["bash", "world/unreal/scripts/unreal-container.sh", "run-packaged"],
        cwd=ROOT,
        env=environment,
        stdout=log_path.open("xb"),
        stderr=subprocess.STDOUT,
    )
    loop = asyncio.get_running_loop()
    pools = {n: ThreadPoolExecutor(max_workers=1, thread_name_prefix=n) for n in sim.NAMES}
    gpu, stopping = [], threading.Event()
    sampler = None
    try:
        # Cosys blocks the first frame on motor responses: start the autopilots once RPC listens.
        deadline, native = time.monotonic() + 900, sim.DATA / "runtime.json"
        listen = ["ss", "-H", "-ltn", f"sport = :{sim.PORT}"]
        while not subprocess.run(listen, check=True, capture_output=True, timeout=5).stdout:
            assert world.poll() is None and time.monotonic() < deadline, "Unreal did not start"
            await asyncio.sleep(1)
        await fleet.start()
        while not (native.exists() and native.stat().st_mtime > launched):
            assert world.poll() is None and time.monotonic() < deadline, "Unreal did not start"
            await asyncio.sleep(1)
        origins = {v["id"]: v["localNedOriginUeCm"] for v in sim.native()["vehicles"]}
        candidates = subprocess.run(
            ["pgrep", "-x", "ScenarioStudio"], check=True, capture_output=True, text=True
        ).stdout.split()
        (pid,) = [
            int(p)
            for p in candidates
            if Path(f"/proc/{p}/cmdline").read_bytes().split(b"\0")[0] == bytes(binary)
        ]
        sampler = threading.Thread(target=sample_gpu, args=(gpu, stopping))
        sampler.start()
        rpcs = {n: await loop.run_in_executor(pools[n], sim.connect) for n in sim.NAMES}
        console = rpcs[sim.NAMES[0]]
        for cvar in LUMEN_CVARS:
            assert await loop.run_in_executor(
                pools[sim.NAMES[0]], console.simRunConsoleCommand, f"{cvar} {int(lumen)}"
            )
        deadline = time.monotonic() + 10
        while (
            itemgetter("lumenGlobalIllumination", "lumenReflections")(sim.native()) != (lumen,) * 2
        ):
            assert time.monotonic() < deadline, "DroneFoundation did not report the Lumen arm"
            await asyncio.sleep(0.5)

        async def shoot(name, path=None):
            return await loop.run_in_executor(
                pools[name], capture, rpcs[name], name, origins[name], path
            )

        async def fly(name, point, timeout):
            await fleet.execute(
                "fly_route", {"drone_id": name, "waypoints": [point], "speed_mps": SPEED_MPS}
            )
            await until(
                fleet,
                name,
                lambda s: (
                    abs(s["north_m"] - point["north_m"]) < 1
                    and abs(s["east_m"] - point["east_m"]) < 1
                    and s["ground_speed_mps"] < 0.2
                ),
                timeout,
                "route arrival",
            )

        async def mission(name):
            route, result = routes[name], []
            for north in route["captureNorthM"]:
                assert state(fleet, name)["north_m"] < north, f"{name}: capture cannot keep up"
                trigger = await until(fleet, name, lambda s: s["north_m"] >= north, 60, "trigger")
                assert abs(trigger["ground_speed_mps"] - SPEED_MPS) < 0.5, trigger
                frame = await shoot(name)
                z = trigger["home"]["altitude_msl_m"] + route["altitudeAboveHomeM"]
                frame["poseErrorM"] = {
                    "north": frame["cameraWorldM"]["x"] - north,
                    "east": frame["cameraWorldM"]["y"] - route["end"]["east_m"],
                    "up": frame["cameraWorldM"]["z"] - (z - ORIGIN["altitude_msl_m"]),
                }
                error = frame["poseErrorM"]
                assert -0.5 <= error["north"] <= 3 and max(abs(error["east"]), abs(error["up"])) < 1
                result.append(frame)
            return result

        async def stress(name):
            return [await shoot(name) for _ in range(STRESS_CAPTURES)]

        await together(*(takeoff(fleet, n, routes[n]["altitudeAboveHomeM"]) for n in sim.NAMES))
        await together(*(fly(n, routes[n]["start"], 120) for n in sim.NAMES))
        # The arms' first renders at identical poses: the cold per-capture Lumen state is visible here.
        warmup = await together(*(shoot(n, run / f"warmup-{n}.png") for n in sim.NAMES))
        for command in ("csv.CompressionMode 0", f"CsvProfile STARTFILE={CSV_NAME}"):
            assert await loop.run_in_executor(
                pools[sim.NAMES[0]], console.simRunConsoleCommand, command
            )
        start_rpc = [time.monotonic()]
        assert await loop.run_in_executor(
            pools[sim.NAMES[0]], console.simRunConsoleCommand, "CsvProfile START"
        )
        start_rpc.append(time.monotonic())
        startup_ram = ram_peak(pid, True)
        flown = await together(
            *(mission(n) for n in sim.NAMES), *(fly(n, routes[n]["end"], 120) for n in sim.NAMES)
        )
        mission_ram = ram_peak(pid, True)
        stressed = await together(*(stress(n) for n in sim.NAMES))
        stress_ram = ram_peak(pid, False)
        stop_rpc = [time.monotonic()]
        assert await loop.run_in_executor(
            pools[sim.NAMES[0]], console.simRunConsoleCommand, "CsvProfile STOP"
        )
        stop_rpc.append(time.monotonic())
        await asyncio.sleep(5)
    finally:
        stopping.set()
        if sampler is not None:
            sampler.join()
        # An image RPC in flight at SIGTERM deadlocks teardown: ASimHUD::EndPlay joins the rpclib
        # workers while one waits, without timeout, for a frame the exiting game never draws.
        for pool in pools.values():
            pool.shutdown(wait=True)
        subprocess.run(["docker", "kill", "--signal=TERM", CONTAINER], check=True, timeout=30)
        deadline = time.monotonic() + 120
        while b"UWorld::CleanupWorld for OpenMeadow," not in log_path.read_bytes():
            assert time.monotonic() < deadline, "Unreal EndPlay marker timed out"
            await asyncio.sleep(0.1)
        await fleet.close()
        # SIGTERM ends the packaged game with the measured status 143 (world/cosys/README.md).
        exit_code = world.wait(timeout=300)
        assert exit_code == 143, f"Unreal container exit status {exit_code}"
        events.close()
    ports = check_ports(world_ports + fleet_ports)
    timeline = frames(
        run / "unreal-home/ScenarioStudio/Saved/Profiling/CSV" / f"{CSV_NAME}.csv",
        start_rpc,
        stop_rpc,
    )
    mission_frames = [f for drone in flown[: len(sim.NAMES)] for f in drone]
    stress_frames = [f for drone in stressed for f in drone]
    report = {
        "schemaVersion": 1,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "scriptSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "package": str(package),
        "gameBinarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "pakBytes": {
            str(p.relative_to(package)): p.stat().st_size
            for p in sorted((package / "ScenarioStudio/Content/Paks").iterdir())
        },
        "launch": LAUNCH,
        "lumen": lumen,
        "planSha256": digest,
        "plan": routes,
        "speedMps": SPEED_MPS,
        "spacingM": SPACING_M,
        "gpuTiming": "UE CSV profiler GPUTime = RHIGetGPUFrameCycles per frame",
        "frameAnchorUncertaintyS": timeline["anchorUncertaintyS"],
        "csvFrames": int(timeline["frameMs"].size),
        "startupPeakRamBytes": startup_ram,
        "mission": workload(timeline, mission_frames, gpu, mission_ram, pid),
        "stress": workload(timeline, stress_frames, gpu, stress_ram, pid),
        "missionPoseErrorM": {
            k: stats([f["poseErrorM"][k] for f in mission_frames]) for k in ("north", "east", "up")
        },
        "warmupCaptureMs": [(c["endS"] - c["startS"]) * 1000 for c in warmup],
        "warmupShadowFloorLuminanceP1": [c["shadowFloorLuminanceP1"] for c in warmup],
        "autopilotExitCodes": {n: v["process"].returncode for n, v in fleet.vehicles.items()},
        "portsFreeAfter": ports["ports"],
    }
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: report[k] for k in ("mission", "stress")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lumen", choices=("on", "off"), required=True)
    arguments = parser.parse_args()
    asyncio.run(measure(arguments.output.resolve(), arguments.lumen == "on"))

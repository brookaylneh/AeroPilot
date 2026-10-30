"""Runtime landscape grass in the packaged world: coverage at fixed nadir poses and a flight check.

Coverage at a pose is the fraction of pixels that differ between grass on and grass off
(`grass.Enable`). Every frame at a pose is taken from the same frozen kinematic state: the body is
reset to the hover pose with zero velocity, one simulation frame applies it, and the paused world is
captured, so two frames at a pose must be byte-identical and the grass regrowth after the switch is
timed. Frames without an off state (the old world, D2-B) get the texture proxy, the mean high-pass
luminance energy; the flight check reports each capture's texture as a ratio between the measured
off and on frames at the same height and requires the 60 m coverage target of that ratio with no
grass build in flight.
"""

import argparse
import asyncio
import io
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cosysairsim as airsim
import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter

from mission_planner.environment import load_env
from mission_planner.gcs.fleet import Fleet
from mission_planner.gcs.sitl import check_ports
from world.cosys import client as sim
from world.cosys.capture import nadir_error, shadow_floor
from world.cosys.flight import move, state, takeoff, until
from world.cosys.landmarks import load as load_landmarks
from world.cosys.terrain import terrain_height
from world.unreal.measure_performance import LAUNCH

ROOT = Path(__file__).resolve().parents[2]
CONTAINER = "snr-unreal-valley-art-grass"
DRONE = "Drone1"
# Coverage poses in the dry meadow; the flight continues over Meadow Creek between its head and the
# footbridge to Granite Lake (north, east m).
POSES = {"meadow": (-350.0, 50.0)}
CREEK = (85.0, 560.0)
LAKE = (360.0, -120.0)
HEIGHTS_M = (30.0, 60.0)
TARGET = {30.0: 0.90, 60.0: 0.75}
SPEED_MPS = 5.0
WARM_UP = 2
# Temporal anti-aliasing re-jitters and the tonemapper re-dithers every frame, so the coverage
# frames render without them.
DETERMINISTIC = ("r.AntiAliasingMethod 0", "r.Tonemapper.GrainQuantization 0")
SPACING_M = 16.0
FLIGHT_M = 60.0
# Grass clears or regrows within this long after `grass.Enable` changes.
REGROW_S = 60.0
HIGH_PASS_PX = 7


def texture(pixels):
    luminance = np.asarray(pixels, dtype=np.float32) @ np.array(
        [0.2126, 0.7152, 0.0722], np.float32
    )
    return float(np.abs(luminance - uniform_filter(luminance, HIGH_PASS_PX)).mean())


def differing(a, b):
    """Fraction of pixels whose largest channel difference exceeds 0, 1, 2, 4 and 8 of 255; the
    coverage is the fraction above 0, and the thresholds place the same-pose render noise."""
    assert a.shape == b.shape == (2160, 3840, 3)
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return {str(t): float((d > t).mean()) for t in (0, 1, 2, 4, 8)}


def analyse(on, again, off, regrown):
    differences = {
        "repeat": differing(on, again),
        "onOff": differing(on, off),
        "regrownOff": differing(regrown, off),
    }
    return differences, {"on": texture(on), "off": texture(off)}


def shoot(rpc, origin, taken, warm_up=False):
    """One nadir capture: pixels, camera world position, grass telemetry and the capture's index
    on this drone; every frame but a discarded warm-up must pass the shadow-floor assert."""
    (r,) = rpc.simGetImages(
        [airsim.ImageRequest("front_center", airsim.ImageType.Scene, False, False)],
        vehicle_name=DRONE,
    )
    assert not r.message and (r.width, r.height) == (3840, 2160), r.message
    pixels = np.frombuffer(r.image_data_uint8, dtype=np.uint8).reshape(2160, 3840, 3)
    assert nadir_error(sim.quat(r.camera_orientation)) < 1
    taken.append(None if warm_up else shadow_floor(r.image_data_uint8, 3840, 2160))
    camera = sim.to_world(DRONE, sim.xyz(r.camera_position), origin)
    return pixels, camera, sim.native()["grass"], {"index": len(taken), "shadowFloorP1": taken[-1]}


def save(pixels, path):
    buffer = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(
        buffer, format={".png": "PNG", ".jpg": "JPEG"}[path.suffix], quality=92
    )
    path.write_bytes(buffer.getvalue())


async def measure(out):
    load_env()
    assert not out.exists()
    package = Path(os.environ["SNR_PACKAGE_ROOT"]).resolve(strict=True)
    for name, key in (
        ("cosys-settings.json", "SNR_COSYS_SETTINGS"),
        ("world.json", "SNR_WORLD_CONFIG"),
    ):
        assert Path(os.environ[key]).read_bytes() == (package / name).read_bytes(), key
    settings = json.loads((package / "cosys-settings.json").read_text())
    assert settings["ApiServerPort"] == sim.PORT
    ports = [sim.PORT] + [v["ControlPortLocal"] for v in settings["Vehicles"].values()]
    check_ports(ports)
    (out / "frames").mkdir(parents=True)
    events = (out / "events.jsonl").open("x")
    fleet = Fleet(
        out / "sitl",
        lambda e: events.write(json.dumps(e, default=str) + "\n"),
        load_landmarks(),
    )
    launched = time.time()
    world = subprocess.Popen(
        ["systemd-run", "--user", "--scope", "-p", "MemoryMax=56G", "-p", "MemorySwapMax=0"]
        + ["bash", "world/unreal/scripts/unreal-container.sh", "run-packaged"],
        cwd=ROOT,
        env={
            **os.environ,
            **LAUNCH,
            "SNR_CONTAINER_NAME": CONTAINER,
            "SNR_UNREAL_HOME": str(out / "unreal-home"),
        },
        stdout=(out / "unreal.log").open("xb"),
        stderr=subprocess.STDOUT,
    )
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=1)
    poses, flight, taken = {}, [], []
    try:
        deadline, native = time.monotonic() + 900, sim.DATA / "runtime.json"
        listen = ["ss", "-H", "-ltn", f"sport = :{sim.PORT}"]
        while not subprocess.run(listen, check=True, capture_output=True, timeout=5).stdout:
            assert world.poll() is None and time.monotonic() < deadline, "Unreal did not start"
            await asyncio.sleep(1)
        await fleet.start()
        while not (native.exists() and native.stat().st_mtime > launched):
            assert world.poll() is None and time.monotonic() < deadline, "Unreal did not start"
            await asyncio.sleep(1)
        (origin,) = [v["localNedOriginUeCm"] for v in sim.native()["vehicles"] if v["id"] == DRONE]
        rpc = await loop.run_in_executor(pool, sim.connect)

        def call(fn, *args):
            return loop.run_in_executor(pool, fn, *args)

        async def console(command):
            assert await call(rpc.simRunConsoleCommand, command), command

        for command in DETERMINISTIC:
            await console(command)

        async def grass_state(predicate, description):
            """Poll the native grass telemetry until predicate(grass) holds; returns (grass, s)."""
            started = time.monotonic()
            while True:
                grass = sim.native()["grass"]
                if predicate(grass):
                    return grass, time.monotonic() - started
                assert time.monotonic() - started < REGROW_S, description
                await asyncio.sleep(0.5)

        async def settled(predicate):
            """Grass telemetry satisfying predicate on two polls a second apart: the hovering
            fleet's cull boundaries keep the instance count moving, so quiescence means no build
            in flight for a second, not an unchanged count."""
            _, seconds = await grass_state(predicate, "grass did not settle")
            await asyncio.sleep(1)
            after, more = await grass_state(predicate, "grass did not stay settled")
            return after, seconds + more + 1

        async def frozen(kinematics, repeats=1):
            """Captures from the body reset to the kinematics and paused after one frame; the
            autopilot stalls while paused, so the flight link must return before the next call."""
            await call(rpc.simSetKinematics, kinematics, True, DRONE)
            await call(rpc.simContinueForFrames, 1)
            while not await call(rpc.simIsPause):
                await asyncio.sleep(0.02)
            shots = [await call(shoot, rpc, origin, taken) for _ in range(repeats)]
            await call(rpc.simPause, False)
            # The autopilot's heartbeat returns within a second of the pause ending.
            await asyncio.sleep(3)
            deadline = time.monotonic() + 10
            while next(s for s in fleet.snapshot() if s["id"] == DRONE)["link_lost"]:
                assert time.monotonic() < deadline, "flight link did not return after the pause"
                await asyncio.sleep(0.2)
            return shots

        await takeoff(fleet, DRONE, HEIGHTS_M[0])
        await call(sim.set_view, rpc, DRONE)
        for label, (north, east) in POSES.items():
            ground = terrain_height(north, east)
            for height in HEIGHTS_M:
                arrival = await move(
                    fleet, DRONE, {"x": north, "y": east, "z": ground + height}, 4.0
                )
                # The capture's own Lumen scene has no surface-cache cards at a new place: its
                # first two captures there shade card-less hits black and are discarded.
                for _ in range(WARM_UP):
                    await call(shoot, rpc, origin, taken, True)
                # The grass around a new pose builds asynchronously: capture only once it has.
                _, settle_s = await settled(lambda g: g["instances"] > 0 and g["asyncTasks"] == 0)
                kinematics = await call(rpc.simGetGroundTruthKinematics, DRONE)
                for key in (
                    "linear_velocity",
                    "angular_velocity",
                    "linear_acceleration",
                    "angular_acceleration",
                ):
                    setattr(kinematics, key, airsim.Vector3r(0, 0, 0))
                (
                    (on, camera, grass_on, shot_on),
                    (again, camera_again, _, shot_again),
                ) = await frozen(kinematics, 2)
                await console("grass.Enable 0")
                grass_off, cleared_s = await grass_state(
                    lambda g: g["instances"] == 0, "grass did not clear"
                )
                ((off, camera_off, _, shot_off),) = await frozen(kinematics)
                await console("grass.Enable 1")
                grass_regrown, regrown_s = await settled(
                    lambda g: g["instances"] > 0 and g["asyncTasks"] == 0
                )
                ((regrown, camera_regrown, _, shot_regrown),) = await frozen(kinematics)
                for other in (camera_again, camera_off, camera_regrown):
                    assert all(abs(camera[k] - other[k]) < 1e-3 for k in "xyz"), (camera, other)
                key = f"{label}-{height:.0f}m"
                # Frame analysis runs off the loop that services the flight link (lost after 2 s).
                differences, textures = await call(analyse, on, again, off, regrown)
                # Encoding also runs off the loop that services the flight link.
                for suffix, pixels in (("on", on), ("on-again", again), ("off", off)):
                    await call(save, pixels, out / "frames" / f"{key}-{suffix}.png")
                poses[key] = {
                    "site": [north, east],
                    "cameraWorldM": camera,
                    "captures": {
                        "on": shot_on,
                        "again": shot_again,
                        "off": shot_off,
                        "regrown": shot_regrown,
                    },
                    "aboveTerrainM": camera["z"] - ground,
                    "positionErrorM": arrival["errorMetres"],
                    "repeat": differences["repeat"]["0"],
                    "coverage": differences["onOff"]["0"],
                    "target": TARGET[height],
                    "regrownIdentical": bool(np.array_equal(on, regrown)),
                    "differing": differences,
                    "texture": textures,
                    "grass": {"on": grass_on, "off": grass_off, "regrown": grass_regrown},
                    "settleSeconds": settle_s,
                    "clearSeconds": cleared_s,
                    "regrowSeconds": regrown_s,
                }
                print(json.dumps({key: poses[key]}), flush=True)
        (out / "coverage.json").write_text(
            json.dumps(
                {
                    "package": str(package),
                    "console": DETERMINISTIC,
                    "warmUpCaptures": WARM_UP,
                    "shadowFloorP1": taken,
                    "poses": poses,
                },
                indent=2,
            )
            + "\n"
        )
        # Flight check: one lane meadow -> creek -> lake at the 60 m target height over the highest
        # ground on the way, captured every SPACING_M of path travelled.
        legs = [np.array(POSES["meadow"]), np.array(CREEK), np.array(LAKE)]
        lengths = [float(np.linalg.norm(q - p)) for p, q in zip(legs, legs[1:])]
        samples = [
            p + (q - p) * s / n
            for p, q, n in zip(legs, legs[1:], lengths)
            for s in np.arange(0, n, 4)
        ]
        z = max(terrain_height(*q) for q in samples) + FLIGHT_M
        stations = np.arange(SPACING_M, sum(lengths), SPACING_M)
        reference = poses[f"meadow-{FLIGHT_M:.0f}m"]["texture"]
        await move(fleet, DRONE, {"x": legs[0][0], "y": legs[0][1], "z": z}, 4.0)
        altitude = round(state(fleet, DRONE)["altitude_above_home_m"])
        await fleet.execute(
            "fly_route",
            {
                "drone_id": DRONE,
                "waypoints": [
                    {"north_m": q[0], "east_m": q[1], "altitude_above_home_m": altitude}
                    for q in legs[1:]
                ],
                "speed_mps": SPEED_MPS,
            },
        )

        def travelled(current):
            """Path metres from the lane start: the second leg once the body has passed the creek."""
            q = np.array([current["north_m"], current["east_m"]])
            first = float(np.dot(q - legs[0], (legs[1] - legs[0]) / lengths[0]))
            if first < lengths[0]:
                return first
            return lengths[0] + float(np.dot(q - legs[1], (legs[2] - legs[1]) / lengths[1]))

        triggered = []
        for index, station in enumerate(stations):
            assert travelled(state(fleet, DRONE)) < station, f"capture {index} cannot keep up"
            trigger = await until(fleet, DRONE, lambda s: travelled(s) >= station, 60, "trigger")
            started = time.monotonic()
            triggered.append(started)
            pixels, camera, grass, shot = await call(shoot, rpc, origin, taken)
            path = out / "frames" / f"flight-{index:02d}.jpg"
            await call(save, pixels, path)
            detail = await call(texture, pixels)
            ratio = (detail - reference["off"]) / (reference["on"] - reference["off"])
            flight.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "capture": shot,
                    "captureMs": (time.monotonic() - started) * 1000,
                    "groundSpeedMps": trigger["ground_speed_mps"],
                    "cameraWorldM": camera,
                    "aboveTerrainM": camera["z"] - terrain_height(camera["x"], camera["y"]),
                    "textureRatio": ratio,
                    "grass": grass,
                    # No grass build may be in flight at a capture; the texture ratio is judged on
                    # the dry-meadow leg only (the creek and lake legs cross water and granite).
                    "passed": grass["asyncTasks"] == 0
                    and (station > lengths[0] or ratio >= TARGET[FLIGHT_M]),
                }
            )
        collision = await call(rpc.simGetCollisionInfo, DRONE)
        (out / "flight.json").write_text(
            json.dumps(
                {
                    "package": str(package),
                    "speedMps": SPEED_MPS,
                    "spacingM": SPACING_M,
                    "textureReference": reference,
                    "captureHz": (len(triggered) - 1) / (triggered[-1] - triggered[0]),
                    "collided": collision.has_collided,
                    "collisionObject": collision.object_name,
                    "captures": flight,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        subprocess.run(["docker", "stop", "--time", "30", CONTAINER], check=True, timeout=60)
        await fleet.close()
        world.wait(timeout=300)
        pool.shutdown(wait=True)
        events.close()
    check_ports(ports)
    for key, pose in poses.items():
        assert pose["repeat"] == 0, (key, pose["repeat"])
        assert pose["coverage"] >= pose["target"], (key, pose["coverage"], pose["target"])
    assert not collision.has_collided, collision.object_name
    assert all(c["passed"] for c in flight), [c["path"] for c in flight if not c["passed"]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="new directory for coverage.json, flight.json and frames/"
    )
    parser.add_argument(
        "--texture",
        nargs="*",
        type=Path,
        default=[],
        help="print the texture proxy of these frames instead",
    )
    arguments = parser.parse_args()
    if arguments.texture:
        print(
            json.dumps(
                {str(p): texture(Image.open(p).convert("RGB")) for p in arguments.texture}, indent=2
            )
        )
    else:
        asyncio.run(measure(arguments.output.resolve()))

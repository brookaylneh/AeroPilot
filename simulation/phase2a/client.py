"""Stock Cosys interfaces with explicit world/vehicle frames. No target knowledge."""

import hashlib
import json
import math
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import cosysairsim as airsim
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
WORLD = json.loads(
    Path(
        os.environ.get("SNR_WORLD_CONFIG", str(ROOT / "simulation/phase2a/world.json"))
    ).read_text()
)
# Explicit migration for the single released manifest predating boundary metadata.
if WORLD["infrastructureVersion"] == "four-drone-port-1":
    assert "boundary" not in WORLD["port"], "Unexpected v1 port metadata"
    WORLD["port"]["boundary"] = "west"
assert WORLD["port"]["boundary"] in ("south", "west"), "Unsupported launch boundary"
NAMES = tuple(f"Drone{i}" for i in range(1, 5))
assert set(WORLD["vehicles"]) == set(NAMES), "World must define exactly Drone1–Drone4"
CAMERA_FORWARD = 0.6
CAMERA_DOWN = 0.05  # Above the slab when landed, beyond the stock mesh footprint.
DATA = Path(os.environ.get("SNR_DRONE_DATA_DIR", str(ROOT / "data/phase2a-test")))
PORT = int(os.environ.get("SNR_COSYS_PORT", "41452"))
assert 1 <= PORT <= 65535, "Invalid Cosys RPC port"


def connect():
    return airsim.MultirotorClient(ip="127.0.0.1", port=PORT, timeout_value=120)


def xyz(v):
    return {"x": float(v.x_val), "y": float(v.y_val), "z": float(v.z_val)}


def quat(q):
    return {"w": float(q.w_val), "x": float(q.x_val), "y": float(q.y_val), "z": float(q.z_val)}


def pose(p):
    return {"position": xyz(p.position), "orientation": quat(p.orientation)}


def native():
    report = json.loads((DATA / "runtime.json").read_text())
    assert report["schemaVersion"] == 1, "Unsupported native telemetry schema"
    assert sorted(v["id"] for v in report["vehicles"]) == list(NAMES), (
        "Incomplete native aircraft telemetry"
    )
    captured = datetime.fromisoformat(report["capturedAt"].replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - captured).total_seconds()
    if not -1 <= age <= 10:
        raise RuntimeError(f"Native telemetry is stale or clock is invalid: age {age:.1f} s")
    return report


def write_json(path, value):
    """Publish complete reports atomically for concurrent browser readers."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def origin(name):
    return next(v["localNedOriginUeCm"] for v in native()["vehicles"] if v["id"] == name)


def to_world(name, p, origin_cm=None):
    o = origin(name) if origin_cm is None else origin_cm
    return {k: o[k] / 100 + p[k] * (1 if k != "z" else -1) for k in "xyz"}


def to_local(name, p, origin_cm=None):
    o = origin(name) if origin_cm is None else origin_cm
    return {k: (p[k] - o[k] / 100) * (1 if k != "z" else -1) for k in "xyz"}


def distance(a, b):
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in "xyz"))


def state(c, name):
    s = c.getMultirotorState(vehicle_name=name)
    p = xyz(s.kinematics_estimated.position)
    collision = c.simGetCollisionInfo(vehicle_name=name)
    return {
        "id": name,
        "timestampNs": s.timestamp,
        "positionLocalNedM": p,
        "positionWorldM": to_world(name, p),
        "orientationNed": quat(s.kinematics_estimated.orientation),
        "velocityLocalNedMps": xyz(s.kinematics_estimated.linear_velocity),
        "landed": s.landed_state == airsim.LandedState.Landed,
        "apiControl": c.isApiControlEnabled(vehicle_name=name),
        "collision": {
            "hasCollided": collision.has_collided,
            "timestampNs": collision.time_stamp,
            "objectName": collision.object_name,
            "normalLocalNed": xyz(collision.normal),
            "impactPointLocalNedM": xyz(collision.impact_point),
        },
    }


def all_states():
    c = connect()
    return {n: state(c, n) for n in NAMES}


def concurrent(fn, names=NAMES):
    # msgpack clients/futures are not shared across threads.
    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(zip(names, pool.map(fn, names)))


def configure_capture(height):
    assert height in (1080, 2160, 4320)
    req = {"id": str(uuid.uuid4()), "height": height}
    p = DATA / "capture-request.json"
    write_json(p, req)
    end = time.monotonic() + 20
    while time.monotonic() < end:
        r = native()
        if r.get("captureRequestId") == req["id"] and all(
            v["height"] == height for v in r["vehicles"]
        ):
            return
        time.sleep(0.1)
    raise TimeoutError("Capture profile was not applied by Unreal")


def set_view(c, name, pitch=-90, yaw=0):
    # Body-relative NED, using the same stock front_center camera for both views.
    c.simSetCameraPose(
        "front_center",
        airsim.Pose(
            airsim.Vector3r(CAMERA_FORWARD, 0, CAMERA_DOWN),
            airsim.euler_to_quaternion(0, math.radians(pitch), math.radians(yaw)),
        ),
        vehicle_name=name,
    )


def capture(name, directory, tag, freeze=False, observe=None, jpeg_quality=None):
    c = connect()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if freeze:
        c.simPause(True)
    try:
        observation = observe(c) if observe is not None else None
        before = state(c, name)
        start = time.perf_counter()
        r = c.simGetImages(
            [airsim.ImageRequest("front_center", airsim.ImageType.Scene, False, False)],
            vehicle_name=name,
        )[0]
        latency = time.perf_counter() - start
        after = state(c, name)
        if r.message or r.width <= 0 or r.height <= 0:
            raise RuntimeError(r.message or "Empty camera frame")
        data = np.frombuffer(r.image_data_uint8, dtype=np.uint8)
        assert data.size == r.width * r.height * 3, (r.width, r.height, data.size)
        # Pinned Cosys 3.5 RenderRequest explicitly writes R,G,B, with no flip.
        image = Image.fromarray(data.reshape(r.height, r.width, 3), "RGB")
        suffix = "png" if jpeg_quality is None else "jpg"
        path = directory / f"{tag}-{name}.{suffix}"
        if jpeg_quality is None:
            image.save(path)
        else:
            assert 1 <= jpeg_quality <= 100
            image.save(path, quality=jpeg_quality, subsampling=0)
        info = {
            "schemaVersion": 1,
            "vehicleId": name,
            "cameraId": "front_center",
            "imageType": "RGB",
            "file": path.name,
            "width": r.width,
            "height": r.height,
            "timestampNs": r.time_stamp,
            "cameraPoseLocalNed": {
                "position": xyz(r.camera_position),
                "orientation": quat(r.camera_orientation),
            },
            "cameraPositionWorldM": to_world(name, xyz(r.camera_position)),
            "localNedOriginUeCm": origin(name),
            "coordinateSystem": WORLD["coordinateSystem"],
            "horizontalFovDegrees": c.simGetCameraInfo("front_center", vehicle_name=name).fov,
            "vehicleStateBefore": before,
            "vehicleStateAfter": after,
            "physicsPausedForCapture": freeze,
            "vehiclePoseTiming": "Frozen physics state"
            if freeze
            else "Bracketing samples; camera pose and timestamp are supplied by Cosys at capture",
            "captureLatencySeconds": latency,
            "transportBytes": len(r.image_data_uint8),
            "pngBytes" if jpeg_quality is None else "jpegBytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if observe is not None:
            assert freeze, "Observations must share the frozen image state"
            info["observation"] = observe(c)
            assert info["observation"] == observation, "Observed actor moved during frozen capture"
        path.with_suffix(".json").write_text(json.dumps(info, indent=2) + "\n")
        return info
    finally:
        if freeze:
            c.simPause(False)


def takeoff(name):
    c = connect()
    c.enableApiControl(True, vehicle_name=name)
    assert c.armDisarm(True, vehicle_name=name)
    c.takeoffAsync(timeout_sec=20, vehicle_name=name).get()
    return state(c, name)


def move(name, point, speed=5):
    validate_waypoint(point)
    p = to_local(name, point)
    c = connect()
    c.moveToPositionAsync(
        p["x"],
        p["y"],
        p["z"],
        speed,
        timeout_sec=90,
        drivetrain=airsim.DrivetrainType.MaxDegreeOfFreedom,
        yaw_mode=airsim.YawMode(False, WORLD["vehicles"][name]["yawDegrees"]),
        vehicle_name=name,
    ).get()
    # The stock API completes inside its acceptance radius while the aircraft
    # can still be braking. Finish with a slower approach before asking it to
    # hold its current pose; otherwise hover can preserve an overshoot.
    time.sleep(0.7)
    for _ in range(2):
        actual = state(c, name)
        if (
            distance(actual["positionWorldM"], point) < 0.6
            and distance(actual["velocityLocalNedMps"], dict(x=0, y=0, z=0)) < 0.15
        ):
            break
        c.moveToPositionAsync(
            p["x"],
            p["y"],
            p["z"],
            0.8,
            timeout_sec=15,
            drivetrain=airsim.DrivetrainType.MaxDegreeOfFreedom,
            yaw_mode=airsim.YawMode(False, WORLD["vehicles"][name]["yawDegrees"]),
            vehicle_name=name,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            actual = state(c, name)
            if (
                distance(actual["positionWorldM"], point) < 0.6
                and distance(actual["velocityLocalNedMps"], dict(x=0, y=0, z=0)) < 0.2
            ):
                break
            time.sleep(0.2)
    c.hoverAsync(vehicle_name=name).get()
    time.sleep(0.7)
    actual = state(c, name)
    error = distance(actual["positionWorldM"], point)
    if error > 1.2:
        raise RuntimeError(f"{name} waypoint error {error:.2f} m")
    return {"state": actual, "waypointWorldM": point, "errorMetres": error}


def home(name):
    return {k: v / 100 for k, v in WORLD["vehicles"][name]["spawnUeCm"].items()}


def entry_axis():
    return {"south": "x", "west": "y"}[WORLD["port"]["boundary"]]


def land(name):
    c = connect()
    started = state(c, name)["timestampNs"]
    if state(c, name)["landed"]:
        c.armDisarm(False, vehicle_name=name)
        c.enableApiControl(False, vehicle_name=name)
        return state(c, name)
    # Stock land() can return early while still ascending: it counts z_velocity
    # <= its near-zero threshold. Establish descent before invoking it, and use
    # reported landed state as the completion gate. No flight dynamics changed.
    p = to_local(name, {**home(name), "z": home(name)["z"] + 1.5})
    c.moveToPositionAsync(p["x"], p["y"], p["z"], 2, timeout_sec=90, vehicle_name=name).get()
    c.moveToPositionAsync(p["x"], p["y"], p["z"], 0.5, timeout_sec=20, vehicle_name=name).get()
    c.moveByVelocityAsync(0, 0, 0.5, 1, vehicle_name=name).get()
    c.landAsync(timeout_sec=45, vehicle_name=name).get()
    deadline = time.monotonic() + 8
    s = state(c, name)

    def on_platform(s):
        # Cosys simGetCollisionInfo resets has_collided on read. Timestamp and
        # contact geometry survive other telemetry readers and identify the hit.
        return (
            s["collision"]["timestampNs"] >= started
            and s["collision"]["normalLocalNed"]["z"] < -0.9
            and abs(s["positionWorldM"]["z"] - home(name)["z"]) < 0.3
            and math.hypot(
                s["positionWorldM"]["x"] - home(name)["x"],
                s["positionWorldM"]["y"] - home(name)["y"],
            )
            < 1.2
            and distance(s["velocityLocalNedMps"], dict(x=0, y=0, z=0)) < 0.1
        )

    while not s["landed"] and not on_platform(s) and time.monotonic() < deadline:
        time.sleep(0.2)
        s = state(c, name)
    if not s["landed"] and not on_platform(s):
        raise RuntimeError(f"{name} has no verified ground contact")
    assert c.armDisarm(False, vehicle_name=name)
    time.sleep(0.3)
    s = state(c, name)
    if not s["landed"]:
        raise RuntimeError(f"{name} did not report landed after ground contact and disarm")
    c.enableApiControl(False, vehicle_name=name)
    return state(c, name)


_height = None


def validate_waypoint(point):
    if set(point) != set("xyz") or not all(
        isinstance(v, (int, float)) and math.isfinite(v) for v in point.values()
    ):
        raise ValueError("Waypoint must contain finite world metres x, y and z")
    bounds = WORLD["searchBoundsCm"]
    x, y = point["x"], point["y"]
    inside = all(bounds["min"][k] / 100 <= point[k] <= bounds["max"][k] / 100 for k in "xy")
    port = WORLD["port"]["centerUeCm"]
    axis = entry_axis()
    cross = "y" if axis == "x" else "x"
    approach = (
        abs(point[cross] - port[cross] / 100) <= 25
        and port[axis] / 100 - 26 <= point[axis] <= bounds["min"][axis] / 100
    )
    if not inside and not approach:
        raise ValueError("Waypoint is outside the full world and compact launch approach")
    ground = terrain_height(x, y) if inside else port["z"] / 100
    if point["z"] < ground + 1:
        raise ValueError("Airborne waypoint lacks ground clearance")


def terrain_height(x, y):
    global _height
    if _height is None:
        _height = (
            np.fromfile(ROOT / "vendor/open-meadow/height.r16", dtype="<u2")
            .reshape(2017, 2017)
            .astype(float)
            - 32768
        ) / 128
    b = WORLD["searchBoundsCm"]
    i = (x * 100 - b["min"]["x"]) / (b["max"]["x"] - b["min"]["x"]) * 2016
    j = (y * 100 - b["min"]["y"]) / (b["max"]["y"] - b["min"]["y"]) * 2016
    if not (0 <= i <= 2016 and 0 <= j <= 2016):
        raise ValueError("Outside searchable landscape")
    a, b = min(int(i), 2015), min(int(j), 2015)
    u, v = i - a, j - b
    return float(
        _height[b, a] * (1 - u) * (1 - v)
        + _height[b, a + 1] * u * (1 - v)
        + _height[b + 1, a] * (1 - u) * v
        + _height[b + 1, a + 1] * u * v
    )

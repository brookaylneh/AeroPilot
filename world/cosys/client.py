"""Stock Cosys interfaces with explicit world/vehicle frames. No target knowledge."""

import json
import math
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import cosysairsim as airsim

from world.cosys.terrain import terrain_height

ROOT = Path(__file__).resolve().parents[2]
WORLD = json.loads(
    Path(os.environ.get("SNR_WORLD_CONFIG", str(ROOT / "world/cosys/world.json"))).read_text()
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
DATA = Path(os.environ.get("SNR_DRONE_DATA_DIR", str(ROOT / ".local/data/four-drones")))
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
    s = c.simGetGroundTruthKinematics(vehicle_name=name)
    p = xyz(s.position)
    collision = c.simGetCollisionInfo(vehicle_name=name)
    return {
        "id": name,
        "timestampNs": c.getImuData(imu_name="Imu", vehicle_name=name).time_stamp,
        "stateSource": "Cosys ground truth",
        "positionLocalNedM": p,
        "positionWorldM": to_world(name, p),
        "orientationNed": quat(s.orientation),
        "velocityLocalNedMps": xyz(s.linear_velocity),
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


def enter_mission():
    if native()["mode"] == "mission":
        return
    set_session_mode("mission")


def reset_session():
    set_session_mode("studio")


def set_session_mode(mode):
    assert mode in ("studio", "mission")
    identity = str(uuid.uuid4())
    write_json(DATA / "mission-request.json", {"mode": mode, "id": identity})
    end = time.monotonic() + 20
    while time.monotonic() < end:
        report = native()
        if report["modeRequestId"] == identity:
            assert report["mode"] == mode
            return
        time.sleep(0.1)
    raise TimeoutError(f"Unreal did not acknowledge {mode} mode")


def set_view(c, name, pitch=-90, yaw=0):
    # The position is body-relative NED. The stabilized gimbal (Stabilization 1) holds the rotation
    # in the world frame (PIPCamera.cpp setCameraPose and Tick), so yaw is a heading from north.
    c.simSetCameraPose(
        "front_center",
        airsim.Pose(
            airsim.Vector3r(CAMERA_FORWARD, 0, CAMERA_DOWN),
            airsim.euler_to_quaternion(0, math.radians(pitch), math.radians(yaw)),
        ),
        vehicle_name=name,
    )


def home(name):
    return {k: v / 100 for k, v in WORLD["vehicles"][name]["spawnUeCm"].items()}


def entry_axis():
    return {"south": "x", "west": "y"}[WORLD["port"]["boundary"]]


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

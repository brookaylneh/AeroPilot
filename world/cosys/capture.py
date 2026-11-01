"""Unpaused RGB acquisition, pose validation and the shared capture record writer."""

import asyncio
import hashlib
import io
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import cosysairsim as airsim
import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation, Slerp

from mission_planner.gcs.geodesy import ORIGIN, to_local
from world.cosys import client as sim

LIMITS = {
    "nadirDegrees": 1.0,
    "cameraGroundTruthM": 0.75,
    "mavlinkGroundTruthM": 2.0,
    "truthBracketSeconds": 2.0,
    "truthSampleSeconds": 0.1,
    "mavlinkBracketSeconds": 0.5,
    "actorMotionM": 0.01,
}
REC709_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
# A full 4K percentile costs 0.12 s, which would slow the harness's back-to-back captures.
SHADOW_STRIDE = 4


def vector(value):
    result = np.array([value[k] for k in "xyz"], dtype=float)
    assert result.shape == (3,) and np.isfinite(result).all()
    return result


def rotation(value):
    q = np.array([value[k] for k in "xyzw"], dtype=float)
    assert np.isfinite(q).all() and abs(np.linalg.norm(q) - 1) < 1e-5
    return Rotation.from_quat(q)


def nadir_error(orientation):
    axis = rotation(orientation).apply([1, 0, 0])
    return math.degrees(math.acos(float(np.clip(axis[2], -1, 1))))


def shadow_floor(pixels, width, height, *, require_fill=True):
    """Sampled first-percentile luminance; calibration requires nonzero shadow fill."""
    rgb = np.frombuffer(pixels, dtype=np.uint8)
    assert rgb.size == width * height * 3, (rgb.size, width, height)
    grid = rgb.reshape(height, width, 3)[::SHADOW_STRIDE, ::SHADOW_STRIDE].reshape(-1, 3)
    p1 = float(np.percentile(grid.astype(np.float32) @ REC709_LUMA, 1))
    if require_fill:
        assert p1 > 0, "Exact-black shadows: the capture has no sky or indirect fill"
    return p1


def mavlink_sample(fleet, name):
    vehicle = fleet.vehicles[name]
    message = vehicle["messages"]["GLOBAL_POSITION_INT"]
    north, east = to_local(message.lat / 1e7, message.lon / 1e7)
    return {
        "receivedAtUnixNs": int(vehicle["positions"][-1][1] * 1e6),
        "timestampBootMs": message.time_boot_ms,
        "positionWorldM": {
            "x": north,
            "y": east,
            "z": message.alt / 1000 - ORIGIN["altitude_msl_m"],
        },
        "mode": vehicle["mode"],
        "landedState": vehicle["messages"]["EXTENDED_SYS_STATE"].landed_state,
    }


def truth_sample(rpc, name, origin):
    start = time.time_ns()
    first = rpc.getImuData(imu_name="Imu", vehicle_name=name).time_stamp
    state = rpc.simGetGroundTruthKinematics(vehicle_name=name)
    last = rpc.getImuData(imu_name="Imu", vehicle_name=name).time_stamp
    end = time.time_ns()
    assert 0 < first <= last and start < end
    return {
        "id": name,
        "timestampNs": (first + last) // 2,
        "timestampBracketNs": [first, last],
        "sampleUnixNs": (start + end) // 2,
        "sampleWallSeconds": (end - start) / 1e9,
        "positionLocalNedM": sim.xyz(state.position),
        "positionWorldM": sim.to_world(name, sim.xyz(state.position), origin),
        "orientationNed": sim.quat(state.orientation),
        "velocityLocalNedMps": sim.xyz(state.linear_velocity),
        "stateSource": "Cosys ground truth",
    }


def capture_time(timestamp, before, after):
    begin, end = before["timestampNs"], after["timestampNs"]
    assert begin < end, "Simulator clock must advance during capture"
    fraction = (timestamp - begin) / (end - begin)
    unix_ns = round(
        before["sampleUnixNs"] + fraction * (after["sampleUnixNs"] - before["sampleUnixNs"])
    )
    return fraction, unix_ns


def validate(frame, mavlink, observation_after):
    before, after = (frame[k] for k in ("vehicleStateBefore", "vehicleStateAfter"))
    fraction, unix_ns = capture_time(frame["timestampNs"], before, after)
    reasons = []
    if not 0 <= fraction <= 1:
        reasons.append("Image timestamp is outside the ground-truth bracket")
    bracket_seconds = (after["timestampNs"] - before["timestampNs"]) / 1e9
    if bracket_seconds > LIMITS["truthBracketSeconds"]:
        reasons.append("Ground-truth bracket exceeds its time tolerance")
    sample_seconds = max(
        max(s["sampleWallSeconds"], (s["timestampBracketNs"][1] - s["timestampBracketNs"][0]) / 1e9)
        for s in (before, after)
    )
    if sample_seconds > LIMITS["truthSampleSeconds"]:
        reasons.append("Ground-truth sample exceeds its time tolerance")
    interpolation = float(np.clip(fraction, 0, 1))
    body = (1 - interpolation) * vector(before["positionWorldM"]) + interpolation * vector(
        after["positionWorldM"]
    )
    rotations = Rotation.concatenate([rotation(s["orientationNed"]) for s in (before, after)])
    mount = Slerp([0, 1], rotations)(interpolation).apply(
        [sim.CAMERA_FORWARD, 0, sim.CAMERA_DOWN]
    ) * [1, 1, -1]
    camera = vector(frame["cameraPositionWorldM"])
    camera_error = float(np.linalg.norm(camera - body - mount))
    if camera_error > LIMITS["cameraGroundTruthM"]:
        reasons.append("Image camera position disagrees with ground truth")
    angle = nadir_error(frame["cameraPoseLocalNed"]["orientation"])
    if angle > LIMITS["nadirDegrees"]:
        reasons.append("Camera optical axis exceeds the nadir tolerance")
    pairs = [
        (a, b)
        for a, b in zip(mavlink, mavlink[1:])
        if a["receivedAtUnixNs"] <= unix_ns <= b["receivedAtUnixNs"]
    ]
    mav_error, mav_bracket = None, None
    if not pairs:
        reasons.append("Image time is outside the MAVLink receipt bracket")
    else:
        a, b = pairs[0]
        interval = b["receivedAtUnixNs"] - a["receivedAtUnixNs"]
        assert interval > 0
        mav_bracket = interval / 1e9
        t = (unix_ns - a["receivedAtUnixNs"]) / interval
        mav_body = (1 - t) * vector(a["positionWorldM"]) + t * vector(b["positionWorldM"])
        mav_error = float(np.linalg.norm(camera - mount - mav_body))
        if mav_bracket > LIMITS["mavlinkBracketSeconds"]:
            reasons.append("MAVLink receipt bracket exceeds its time tolerance")
        if mav_error > LIMITS["mavlinkGroundTruthM"]:
            reasons.append("Image-inferred body position disagrees with MAVLink")
    actor_motion = None
    if "observation" in frame:
        actor_motion = float(
            np.linalg.norm(
                vector(frame["observation"]["groundWorldM"])
                - vector(observation_after["groundWorldM"])
            )
        )
        if actor_motion > LIMITS["actorMotionM"]:
            reasons.append("Calibration actor moved during capture")
    return {
        "accepted": not reasons,
        "reasons": reasons,
        "tolerances": LIMITS,
        "nadirErrorDegrees": angle,
        "cameraGroundTruthErrorM": camera_error,
        "mavlinkGroundTruthErrorM": mav_error,
        "truthBracketSeconds": bracket_seconds,
        "truthSampleSeconds": sample_seconds,
        "mavlinkBracketSeconds": mav_bracket,
        "actorMotionM": actor_motion,
        "captureUnixNs": unix_ns,
        "utcMethod": "Linear simulator-clock to host-UTC interpolation across ground-truth samples",
        "utcBracketWallSeconds": (after["sampleUnixNs"] - before["sampleUnixNs"]) / 1e9,
    }


class FrameCapture:
    def __init__(self, directory, fleet):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fleet = fleet
        self.executors = {
            name: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"capture-{name}")
            for name in sim.NAMES
        }
        self.clients = {}
        self.active = set()

    def _acquire(self, name, observe):
        if name not in self.clients:
            self.clients[name] = sim.connect()
        rpc = self.clients[name]
        assert sim.native()["mode"] == "mission", "Drone footage requires mission mode"
        assert not rpc.simIsPause(), "Camera acquisition requires running physics"
        origin = sim.origin(name)
        observation = observe(rpc) if observe is not None else None
        before = truth_sample(rpc, name, origin)
        start = time.perf_counter()
        responses = rpc.simGetImages(
            [airsim.ImageRequest("front_center", airsim.ImageType.Scene, False, False)],
            vehicle_name=name,
        )
        latency = time.perf_counter() - start
        after = truth_sample(rpc, name, origin)
        observation_after = observe(rpc) if observe is not None else None
        assert not rpc.simIsPause(), "Physics was paused during camera acquisition"
        assert len(responses) == 1
        response = responses[0]
        assert not response.message, response.message
        assert (
            response.width > 0
            and response.height > 0
            and response.width * 9 == response.height * 16
        )
        assert len(response.image_data_uint8) == response.width * response.height * 3
        info = rpc.simGetCameraInfo("front_center", vehicle_name=name)
        assert abs(info.fov - 70) < 0.01
        frame = {
            "schemaVersion": 1,
            "vehicleId": name,
            "droneId": name,
            "cameraId": "front_center",
            "imageType": "RGB",
            "width": response.width,
            "height": response.height,
            "timestampNs": response.time_stamp,
            "cameraPoseLocalNed": {
                "position": sim.xyz(response.camera_position),
                "orientation": sim.quat(response.camera_orientation),
            },
            "cameraPositionWorldM": sim.to_world(name, sim.xyz(response.camera_position), origin),
            "localNedOriginUeCm": origin,
            "coordinateSystem": sim.WORLD["coordinateSystem"],
            "horizontalFovDegrees": info.fov,
            "vehicleStateBefore": before,
            "vehicleStateAfter": after,
            "physicsPausedForCapture": False,
            "vehiclePoseTiming": "Ground-truth brackets; image pose and timestamp sampled together by Cosys",
            "captureLatencySeconds": latency,
            "transportBytes": len(response.image_data_uint8),
            "shadowFloorLuminanceP1": shadow_floor(
                response.image_data_uint8, response.width, response.height
            ),
        }
        if observe is not None:
            frame["observation"] = observation
        return frame, response.image_data_uint8, observation_after

    def _write(self, frame, pixels, tag, quality):
        start = time.perf_counter()
        image = Image.fromarray(
            np.frombuffer(pixels, dtype=np.uint8).reshape(frame["height"], frame["width"], 3),
            "RGB",
        )
        path = self.directory / f"{tag}-{frame['vehicleId']}.jpg"
        assert not path.exists() and not path.with_suffix(".json").exists(), path
        encoded = io.BytesIO()
        image.save(encoded, format="JPEG", quality=quality, subsampling=0)
        encoded = encoded.getvalue()
        path.write_bytes(encoded)
        frame.update(
            file=path.name,
            imagePath=str(path),
            captureId=path.stem,
            jpegBytes=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
            writeLatencySeconds=time.perf_counter() - start,
        )
        path.with_suffix(".json").write_text(json.dumps(frame, indent=2, allow_nan=False) + "\n")
        return frame

    async def capture(self, name, tag, *, observe=None, jpeg_quality=95):
        assert name in sim.NAMES and name not in self.active
        assert 1 <= jpeg_quality <= 100 and Path(tag).name == tag
        self.active.add(name)
        start = time.perf_counter()
        loop = asyncio.get_running_loop()
        samples = [mavlink_sample(self.fleet, name)]

        def sample():
            current = mavlink_sample(self.fleet, name)
            if current["receivedAtUnixNs"] > samples[-1]["receivedAtUnixNs"]:
                samples.append(current)

        try:
            pending = loop.run_in_executor(self.executors[name], self._acquire, name, observe)
            while not pending.done():
                await asyncio.sleep(0.025)
                self.fleet._check_tasks()
                sample()
            frame, pixels, observation_after = await pending
            _, unix_ns = capture_time(
                frame["timestampNs"], frame["vehicleStateBefore"], frame["vehicleStateAfter"]
            )
            deadline = time.monotonic() + 2
            while samples[-1]["receivedAtUnixNs"] <= unix_ns:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{name}: no MAVLink sample after image acquisition")
                await asyncio.sleep(0.025)
                self.fleet._check_tasks()
                sample()
            frame["mavlinkSamples"] = samples
            frame["validation"] = validate(frame, samples, observation_after)
            frame["capturedAtUtc"] = datetime.fromtimestamp(unix_ns / 1e9, timezone.utc).isoformat()
            frame["acquisitionWallSeconds"] = time.perf_counter() - start
            return await loop.run_in_executor(
                self.executors[name], self._write, frame, pixels, tag, jpeg_quality
            )
        finally:
            self.active.remove(name)

    async def close(self):
        assert not self.active, "Finish capture requests before closing clients"
        loop = asyncio.get_running_loop()
        for name, rpc in self.clients.items():
            await loop.run_in_executor(self.executors[name], rpc.client.close)
        for executor in self.executors.values():
            executor.shutdown(wait=True)

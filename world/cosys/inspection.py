"""Request-driven forward RGB with its capture-time camera pose."""

import asyncio
import io
import json
import math
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import cosysairsim as airsim
import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from mission_planner.gcs.centering import Centering, quaternion_rotation, target_error
from world.cosys import client as sim
from world.cosys.capture import capture_time, rotation, shadow_floor, truth_sample

MOUNT_NED_M = (0.6, 0, 0.05)
SLEW_RAD_S = math.radians(12)


def capture_frame(rpc, name, camera_id, directory, revision, *, quality):
    started = time.monotonic()
    native = sim.native()
    assert native["mode"] == "mission", "Forward inspection requires running mission physics"
    assert not rpc.simIsPause(), "Forward inspection requires running physics"
    origin = next(v["localNedOriginUeCm"] for v in native["vehicles"] if v["id"] == name)
    fov = rpc.simGetCameraInfo(camera_id, vehicle_name=name).fov
    assert math.isfinite(fov) and 1 < fov < 179, "Forward camera field of view is invalid"
    before = truth_sample(rpc, name, origin)
    responses = rpc.simGetImages(
        [airsim.ImageRequest(camera_id, airsim.ImageType.Scene, False, False)], vehicle_name=name
    )
    after = truth_sample(rpc, name, origin)
    assert len(responses) == 1, "Forward capture returned an unexpected image count"
    response = responses[0]
    assert not response.message, response.message
    assert response.camera_name == camera_id, "Forward capture returned the wrong camera"
    assert (response.width, response.height) == (3840, 2160), (
        "Forward capture requires the installed 3840x2160 camera settings"
    )
    assert len(response.image_data_uint8) == response.width * response.height * 3
    fraction, unix_ns = capture_time(response.time_stamp, before, after)
    assert 0 <= fraction <= 1, "Forward image timestamp is outside its capture bracket"
    assert (after["timestampNs"] - before["timestampNs"]) / 1e9 <= 2, (
        "Forward image capture bracket exceeds two seconds"
    )
    info = rpc.simGetCameraInfo(camera_id, vehicle_name=name)
    assert math.isclose(info.fov, fov, abs_tol=0.01), (
        "Forward camera field of view changed during capture"
    )
    orientation = sim.quat(response.camera_orientation)
    rotation(orientation)
    position = sim.xyz(response.camera_position)
    assert all(np.isfinite(v) for v in position.values())
    image = Image.frombytes("RGB", (response.width, response.height), response.image_data_uint8)
    encoded = io.BytesIO()
    image.save(encoded, format="JPEG", quality=quality, subsampling=0)
    metadata = {
        "drone_id": name,
        "camera_id": camera_id,
        "width": response.width,
        "height": response.height,
        "timestamp_ns": response.time_stamp,
        "captured_at_ms": unix_ns / 1e6,
        "camera_pose_local_ned": {"position": position, "orientation": orientation},
        "camera_position_world_m": sim.to_world(name, position, origin),
        "local_ned_origin_ue_cm": origin,
        "horizontal_fov_deg": info.fov,
        "camera_view_revision": revision,
        "vehicle_state_before": before,
        "vehicle_state_after": after,
        "shadow_floor_luminance_p1": shadow_floor(
            response.image_data_uint8, response.width, response.height, require_fill=False
        ),
        "pixel_max": max(high for _, high in image.getextrema()),
        "capture_s": time.monotonic() - started,
    }
    if metadata["pixel_max"] == 0:
        path = directory / f"{response.time_stamp}-rejected.png"
        with path.open("xb") as stream:
            image.save(stream, format="PNG")
        with path.with_suffix(".json").open("x") as stream:
            json.dump(metadata, stream, allow_nan=False)
        raise AssertionError(f"Forward camera returned an entirely black frame; evidence: {path}")
    rgb = np.frombuffer(response.image_data_uint8, np.uint8).reshape(2160, 3840, 3)
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    bgr.setflags(write=False)
    return metadata, encoded.getvalue(), bgr


def point_camera(rpc, name, camera_id, desired):
    # PIPCamera.cpp 5.8-v3.5.0 treats setCameraPose rotation as a world target
    # when Gimbal.Stabilization is 1; its next Tick applies that target in world space.
    rpc.simSetCameraPose(
        camera_id,
        airsim.Pose(airsim.Vector3r(*MOUNT_NED_M), airsim.Quaternionr(*desired.as_quat())),
        vehicle_name=name,
    )
    deadline = time.monotonic() + 2
    while True:
        info = rpc.simGetCameraInfo(camera_id, vehicle_name=name)
        error = math.degrees(
            (desired.inv() * rotation(sim.quat(info.pose.orientation))).magnitude()
        )
        if error <= 0.25:
            return info, error
        assert time.monotonic() < deadline, (
            f"{name}: {camera_id} camera did not settle on its target"
        )
        time.sleep(0.01)


def route_look_ahead(points, position, distance_m):
    keys = ("north_m", "east_m")
    assert points and all(math.isfinite(p[k]) for p in [position, *points] for k in keys)
    assert math.isfinite(distance_m) and distance_m > 0, "Camera look-ahead must be positive"
    vertices = [points[0]]
    for point in points[1:]:
        if any(point[k] != vertices[-1][k] for k in keys):
            vertices.append(point)
    if len(vertices) == 1:
        return {k: vertices[0][k] for k in keys}
    before, after = vertices[:2]
    delta = [after[k] - before[k] for k in keys]
    length = math.hypot(*delta)
    fraction = max(
        0, sum((position[k] - before[k]) * delta[i] for i, k in enumerate(keys)) / length**2
    )
    fraction = min(1, fraction)
    distance_m += fraction * length
    for before, after in zip(vertices, vertices[1:]):
        delta = [after[k] - before[k] for k in keys]
        length = math.hypot(*delta)
        direction = [v / length for v in delta]
        if distance_m <= length:
            return {k: before[k] + distance_m * direction[i] for i, k in enumerate(keys)}
        distance_m -= length
    return {k: vertices[-1][k] for k in keys}


class ForwardCapture:
    def __init__(self, drone_id, directory):
        assert drone_id in sim.NAMES
        self.drone_id = drone_id
        self.directory = directory
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="inspection-" + drone_id
        )
        self.rpc = None
        self.aimed = None
        self.original_fov = None
        self.view_revision = 0
        self.target = None
        self.restore_target = None
        self.aim_task = None
        self.pose_lock = threading.RLock()
        self.follow_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="camera-follow-" + drone_id
        )
        self.follow_rpc = None
        self.follow_generation = 0
        self.visual_controlled = False
        self.centering = Centering()
        self.control_lock = asyncio.Lock()
        self.follow_diagnostic = None
        self.video_sample = None
        self.video_frames = deque(maxlen=12)
        self.capture_task = None
        self.captured_at = 0.0
        self.capture_started_at = 0.0

    def _aim(self, target):
        with self.pose_lock:
            if not self.visual_controlled:
                if self.rpc is None:
                    self.rpc = sim.connect()
                self.target, self.restore_target = dict(target), None
                self._aim_world(target, self.rpc)

    def _aim_world(self, target, rpc):
        assert set(target) == {"x", "y", "z"} and all(np.isfinite(v) for v in target.values())
        name = self.drone_id
        native = sim.native()
        assert native["mode"] == "mission", "Camera aim requires mission mode"
        origin = next(v["localNedOriginUeCm"] for v in native["vehicles"] if v["id"] == name)
        camera = rpc.simGetCameraInfo("fpv", vehicle_name=name).pose
        camera_position = np.array([getattr(camera.position, k + "_val") for k in "xyz"])
        target_ned = np.array(
            [(target[k] - origin[k] / 100) * (1 if k != "z" else -1) for k in "xyz"]
        )
        direction = target_ned - camera_position
        assert np.linalg.norm(direction) > 1, "Camera target is too close to aim"
        angles = np.array(
            [
                math.atan2(direction[1], direction[0]),
                -math.atan2(direction[2], math.hypot(direction[0], direction[1])),
            ]
        )
        now = time.monotonic()
        current = rotation(sim.quat(camera.orientation))
        desired = Rotation.from_euler("ZYX", [*angles, 0])
        dt = 0.05 if self.aimed is None else min(0.1, max(0.001, now - self.aimed[1]))
        step = (current.inv() * desired).as_rotvec()
        length = np.linalg.norm(step)
        limit = SLEW_RAD_S * dt
        if length > limit:
            desired = current * Rotation.from_rotvec(step * limit / length)
        if length > 1e-7:
            point_camera(rpc, name, "fpv", desired)
        self.aimed = desired.as_euler("ZYX")[:2], now
        with (self.directory / "camera-aim.jsonl").open("a") as log:
            log.write(
                json.dumps(
                    {
                        "timestamp_ms": time.time() * 1000,
                        "drone_id": name,
                        "elapsed_s": dt,
                        "step_deg": math.degrees(min(length, limit)),
                        "remaining_deg": math.degrees(max(0, length - limit)),
                    }
                )
                + "\n"
            )

    def _route_aim(self):
        with self.pose_lock:
            if self.visual_controlled:
                return
            target = self.target if self.target is not None else self.restore_target
            if target is None:
                return
            if self.follow_rpc is None:
                self.follow_rpc = sim.connect(timeout_s=3)
            self._aim_world(target, self.follow_rpc)

    async def _maintain_aim(self):
        while True:
            started = time.monotonic()
            pending = asyncio.get_running_loop().run_in_executor(
                self.follow_executor, self._route_aim
            )
            try:
                await asyncio.shield(pending)
            finally:
                if not pending.done():
                    await pending
            await asyncio.sleep(max(0, 0.05 - (time.monotonic() - started)))

    def _start_aim(self):
        if self.aim_task is not None:
            if self.aim_task.done():
                self.aim_task.result()
            return
        self.aim_task = asyncio.create_task(self._maintain_aim())

    async def aim(self, target):
        await asyncio.get_running_loop().run_in_executor(self.executor, self._aim, target)
        self._start_aim()

    def begin_follow(self):
        self.follow_generation += 1
        self.visual_controlled = True
        self.follow_diagnostic = None
        self.centering = Centering()
        return self.follow_generation

    def cancel_follow(self):
        # Keep the last view until an explicit view/reset; an old SAM aim must not take over.
        self.follow_generation += 1

    def _follow(self, box, generation, expires, observed_q):
        started = time.monotonic()
        with self.pose_lock:
            if self.follow_rpc is None:
                self.follow_rpc = sim.connect(timeout_s=3)
            rpc, name = self.follow_rpc, self.drone_id
            body = rpc.simGetVehiclePose(vehicle_name=name)
            info = rpc.simGetCameraInfo("fpv", vehicle_name=name)
            if generation != self.follow_generation:
                return {"status": "stopped"}, None
            if time.monotonic() >= expires:
                return {"status": "stale"}, None
            q = info.pose.orientation
            movement = self.centering.step(
                box, info.fov, [q.w_val, q.x_val, q.y_val, q.z_val], observed_q, time.monotonic()
            )
            desired = Rotation.from_euler(
                "ZYX", [movement[k + "_rad"] for k in ("yaw", "pitch", "roll")]
            )
            assert self.aimed is not None, "Camera view must be initialized before following"
            if generation != self.follow_generation:
                return {"status": "stopped"}, None
            if time.monotonic() >= expires:
                return {"status": "stale"}, None
            measured, readback_error = point_camera(rpc, name, "fpv", desired)
            body_after = rpc.simGetVehiclePose(vehicle_name=name)
            actual = rotation(sim.quat(measured.pose.orientation))
            body_motion = math.degrees(
                (
                    rotation(sim.quat(body.orientation)).inv()
                    * rotation(sim.quat(body_after.orientation))
                ).magnitude()
            )
            assert math.isclose(measured.fov, info.fov, abs_tol=0.01), (
                "Camera field of view changed during centering"
            )
            camera_q = actual.as_quat()[[3, 0, 1, 2]].tolist()
            _, error = target_error(box, measured.fov, camera_q, observed_q)
            movement.update(
                error_x_px=float(error[0]),
                error_y_px=float(error[1]),
                centered=bool(abs(error[0]) <= 9.6 and abs(error[1]) <= 5.4),
                readback_error_deg=readback_error,
                readback_body_motion_deg=body_motion,
                camera_q=camera_q,
            )
            return {
                "status": "moved",
                "yaw_delta_deg": movement["yaw_delta_deg"],
                "pitch_delta_deg": movement["pitch_delta_deg"],
                "elapsed_ms": (time.monotonic() - started) * 1000,
            }, movement

    async def end_follow(self, generation=None):
        async with self.control_lock:
            if generation is not None and generation != self.follow_generation:
                return
            self.follow_diagnostic = None

    async def follow(self, box, generation, expires, observed_q):
        async with self.control_lock:
            if generation != self.follow_generation:
                return {"status": "stopped"}
            if time.monotonic() >= expires:
                return {"status": "stale"}
            assert self.visual_controlled, "Visual guidance no longer owns the camera"
            quaternion_rotation(observed_q)
            pending = asyncio.get_running_loop().run_in_executor(
                self.follow_executor, self._follow, box, generation, expires, observed_q
            )
            try:
                movement, diagnostic = await asyncio.shield(pending)
            finally:
                if not pending.done():
                    await pending
            self.follow_diagnostic = diagnostic
            return movement

    def _view(self, target, zoom=1):
        self.cancel_follow()
        self.visual_controlled = False
        self._aim(target)
        current = self.rpc.simGetCameraInfo("fpv", vehicle_name=self.drone_id).fov
        assert math.isfinite(current) and 1 < current < 179, (
            "Forward camera field of view is invalid"
        )
        if self.original_fov is None:
            self.original_fov = current
        fov = math.degrees(2 * math.atan(math.tan(math.radians(self.original_fov) / 2) / zoom))
        if not math.isclose(current, fov, abs_tol=0.01):
            self.rpc.simSetCameraFov("fpv", fov, vehicle_name=self.drone_id)
        measured = self.rpc.simGetCameraInfo("fpv", vehicle_name=self.drone_id).fov
        assert math.isclose(measured, fov, abs_tol=0.01), (
            "Camera zoom readback differs from request"
        )
        self.view_revision += 1
        return {
            "target": dict(target),
            "zoom": zoom,
            "horizontal_fov_deg": measured,
            "revision": self.view_revision,
        }

    async def view(self, target, zoom=1):
        assert type(zoom) in (int, float) and zoom in (1, 2, 4), "Camera zoom must be 1x, 2x or 4x"
        self.cancel_follow()
        await self.end_follow()
        pending = asyncio.get_running_loop().run_in_executor(
            self.executor, self._view, target, zoom
        )
        try:
            result = await asyncio.shield(pending)
            self._start_aim()
            return result
        finally:
            if not pending.done():
                await pending

    def _reset(self):
        self.cancel_follow()
        with self.pose_lock:
            self.visual_controlled = False
            self._restore()

    def _restore(self):
        self.target = None
        if self.aimed is not None:
            body = self.rpc.simGetVehiclePose(vehicle_name=self.drone_id)
            heading = rotation(sim.quat(body.orientation)).as_euler("ZYX")[0]
            native = sim.native()
            origin = next(
                v["localNedOriginUeCm"] for v in native["vehicles"] if v["id"] == self.drone_id
            )
            camera = self.rpc.simGetCameraInfo("fpv", vehicle_name=self.drone_id).pose
            position = sim.to_world(self.drone_id, sim.xyz(camera.position), origin)
            self.restore_target = {
                "x": position["x"] + 1000 * math.cos(heading),
                "y": position["y"] + 1000 * math.sin(heading),
                "z": position["z"],
            }
            self._aim_world(self.restore_target, self.rpc)
        self._restore_fov()

    def _restore_fov(self):
        if self.original_fov is not None:
            self.rpc.simSetCameraFov("fpv", self.original_fov, vehicle_name=self.drone_id)
            measured = self.rpc.simGetCameraInfo("fpv", vehicle_name=self.drone_id).fov
            assert math.isclose(measured, self.original_fov, abs_tol=0.01), (
                "Camera field of view was not restored"
            )
            self.original_fov = None

    async def reset(self):
        self.cancel_follow()
        await self.end_follow()
        await asyncio.get_running_loop().run_in_executor(self.executor, self._reset)
        self._start_aim()

    def _capture(self):
        if self.rpc is None:
            self.rpc = sim.connect()
        return capture_frame(
            self.rpc, self.drone_id, "fpv", self.directory, self.view_revision, quality=95
        )

    async def _next_frame(self):
        await asyncio.sleep(max(0, 0.2 - (time.monotonic() - self.capture_started_at)))
        self.capture_started_at = time.monotonic()
        pending = asyncio.get_running_loop().run_in_executor(self.executor, self._capture)
        try:
            metadata, jpeg, bgr = await asyncio.shield(pending)
            if self.video_sample is not None:
                assert metadata["timestamp_ns"] > self.video_sample[0]["timestamp_ns"], (
                    "The camera returned a repeated capture timestamp"
                )
            self.captured_at = time.monotonic()
            self.video_sample = metadata, jpeg
            self.video_frames.append((metadata, bgr))
            return metadata, jpeg
        finally:
            try:
                if not pending.done():
                    await pending
            finally:
                self.capture_task = None

    async def capture(self, after_timestamp_ns=0):
        assert type(after_timestamp_ns) is int and after_timestamp_ns >= 0
        if self.aim_task is not None and self.aim_task.done():
            self.aim_task.result()
        if (
            self.video_sample is not None
            and self.video_sample[0]["timestamp_ns"] > after_timestamp_ns
            and self.video_sample[0]["camera_view_revision"] == self.view_revision
            and time.monotonic() - self.captured_at < 0.2
        ):
            return self.video_sample
        if self.capture_task is None:
            self.capture_task = asyncio.create_task(self._next_frame())
        return await asyncio.shield(self.capture_task)

    async def close(self):
        if self.capture_task is not None:
            await self.capture_task
        self.cancel_follow()
        await self.end_follow()
        if self.aim_task is not None:
            if not self.aim_task.done():
                self.aim_task.cancel()
                await asyncio.wait([self.aim_task])
            if not self.aim_task.cancelled():
                self.aim_task.result()
            self.aim_task = None
        if self.follow_rpc is not None:
            await asyncio.get_running_loop().run_in_executor(
                self.follow_executor, self.follow_rpc.client.close
            )
        self.follow_executor.shutdown(wait=True)
        if self.rpc is not None:
            await asyncio.get_running_loop().run_in_executor(self.executor, self._restore_fov)
            await asyncio.get_running_loop().run_in_executor(self.executor, self.rpc.client.close)
            self.rpc = None
        self.follow_rpc = None
        self.executor.shutdown(wait=True)
        self.video_frames.clear()

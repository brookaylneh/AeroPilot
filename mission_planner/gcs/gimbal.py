"""MAVLink Gimbal v2 camera control; angles use radians and NED coordinates."""

import math
import time

import numpy as np
from pymavlink.dialects.v20 import ardupilotmega as MAV
from scipy.spatial.transform import Rotation

DEVICE_ID = 1
LOCK_FLAGS = (
    MAV.GIMBAL_MANAGER_FLAGS_ROLL_LOCK
    | MAV.GIMBAL_MANAGER_FLAGS_PITCH_LOCK
    | MAV.GIMBAL_MANAGER_FLAGS_YAW_LOCK
)


def quaternion_rotation(q):
    values = np.asarray(q, dtype=float)
    assert values.shape == (4,) and np.isfinite(values).all(), "Expected finite w,x,y,z quaternion"
    assert abs(np.linalg.norm(values) - 1) < 1e-3, "Gimbal quaternion is not normalized"
    return Rotation.from_quat(values[[1, 2, 3, 0]])


def visible_box(box, width=960, height=540):
    assert len(box) == 4 and all(math.isfinite(v) for v in box), "Tracker box must be finite xyxy"
    x1, y1, x2, y2 = box
    assert x1 < x2 and y1 < y2, "Tracker box must have positive width and height"
    clipped = [max(0, x1), max(0, y1), min(width, x2), min(height, y2)]
    return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None


def wrapped(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


class Centering:
    def __init__(self):
        self.at = None
        self.rates = np.zeros(2)

    def step(self, box, fov_deg, camera_q, observed_q, now):
        assert visible_box(box) is not None, "Target is outside the camera image"
        assert math.isfinite(fov_deg) and 1 < fov_deg < 179, "Invalid horizontal camera FOV"
        assert math.isfinite(now), "Invalid controller timestamp"
        dt = 0.1 if self.at is None else now - self.at
        assert dt > 0, "Camera guidance timestamps must increase"
        dt = min(dt, 0.2)
        self.at = now
        focal = 960 / (2 * math.tan(math.radians(fov_deg) / 2))
        error = np.array([(box[0] + box[2]) / 2 - 480, (box[1] + box[3]) / 2 - 270])
        rotation = quaternion_rotation(camera_q)
        forward = rotation.apply([1, 0, 0])
        target = quaternion_rotation(observed_q).apply([1, error[0] / focal, error[1] / focal])
        present = rotation.inv().apply(target)
        assert present[0] > 0, "Observed target is behind the current camera"
        error = present[1:] / present[0] * focal
        yaw = math.atan2(forward[1], forward[0])
        pitch = -math.atan2(forward[2], math.hypot(forward[0], forward[1]))
        desired = np.array(
            [
                wrapped(math.atan2(target[1], target[0]) - yaw),
                -math.atan2(target[2], math.hypot(target[0], target[1])) - pitch,
            ]
        )
        centered = bool(abs(error[0]) <= 960 * 0.01 and abs(error[1]) <= 540 * 0.01)
        wanted = (
            np.zeros(2) if centered else np.clip(desired * 1.5, -math.radians(12), math.radians(12))
        )
        self.rates += (1 - math.exp(-dt / 0.12)) * (wanted - self.rates)
        if centered:
            self.rates[:] = 0
        delta = self.rates * dt
        return {
            "yaw_rad": wrapped(yaw + delta[0]),
            "pitch_rad": float(pitch + delta[1]),
            "yaw_delta_deg": math.degrees(delta[0]),
            "pitch_delta_deg": math.degrees(delta[1]),
            "error_x_px": float(error[0]),
            "error_y_px": float(error[1]),
            "centered": centered,
        }


class Gimbal:
    def __init__(self, sysid, connection, command, until):
        self.sysid = sysid
        self.connection = connection
        self.command = command
        self.until = until
        self.messages = {}
        self.times = {}
        self.controller = Centering()
        self.claimed = False

    def receive(self, message):
        kind = message.get_type()
        if kind not in (
            "GIMBAL_MANAGER_INFORMATION",
            "GIMBAL_MANAGER_STATUS",
            "GIMBAL_DEVICE_ATTITUDE_STATUS",
            "GLOBAL_POSITION_INT",
        ):
            return
        if message.get_srcSystem() != self.sysid:
            return
        if (
            kind in ("GIMBAL_MANAGER_INFORMATION", "GIMBAL_MANAGER_STATUS")
            and message.gimbal_device_id != DEVICE_ID
        ):
            return
        if kind == "GIMBAL_DEVICE_ATTITUDE_STATUS":
            if message.get_srcComponent() not in (1, MAV.MAV_COMP_ID_GIMBAL):
                return
            if message.gimbal_device_id not in (0, DEVICE_ID):
                return
        elif message.get_srcComponent() != MAV.MAV_COMP_ID_AUTOPILOT1:
            return
        self.messages[kind] = message
        self.times[kind] = time.monotonic()

    def owns(self):
        if "GIMBAL_MANAGER_STATUS" not in self.messages:
            return False
        status = self.messages["GIMBAL_MANAGER_STATUS"]
        return (
            status.primary_control_sysid == self.connection.mav.srcSystem
            and status.primary_control_compid == self.connection.mav.srcComponent
        )

    async def acquire(self, world_q):
        assert not self.claimed, "The camera already has a visual controller"
        assert "GIMBAL_MANAGER_STATUS" in self.messages, "The gimbal manager is unavailable"
        status = self.messages["GIMBAL_MANAGER_STATUS"]
        assert time.monotonic() - self.times["GIMBAL_MANAGER_STATUS"] <= 1, "Stale gimbal manager"
        assert self.owns() or (status.primary_control_sysid, status.primary_control_compid) == (
            0,
            0,
        ), "Another controller owns the main-camera gimbal"
        quaternion_rotation(world_q)
        await self.command(
            MAV.MAV_CMD_DO_GIMBAL_MANAGER_CONFIGURE,
            [
                self.connection.mav.srcSystem,
                self.connection.mav.srcComponent,
                -1,
                -1,
                0,
                0,
                DEVICE_ID,
            ],
        )
        self.claimed = True
        ray = quaternion_rotation(world_q).apply([1, 0, 0])
        self.point(-math.atan2(ray[2], math.hypot(ray[0], ray[1])), math.atan2(ray[1], ray[0]))
        await self.command(
            MAV.MAV_CMD_REQUEST_MESSAGE,
            [MAV.MAVLINK_MSG_ID_GIMBAL_MANAGER_STATUS, 0, 0, 0, 0, 0, 0],
        )
        await self.until(
            lambda: (
                self.owns()
                and self.messages["GIMBAL_MANAGER_STATUS"].flags & MAV.GIMBAL_MANAGER_FLAGS_YAW_LOCK
                and "GIMBAL_DEVICE_ATTITUDE_STATUS" in self.messages
                and bool(
                    self.messages["GIMBAL_DEVICE_ATTITUDE_STATUS"].flags
                    & MAV.GIMBAL_DEVICE_FLAGS_YAW_LOCK
                )
            ),
            3,
        )
        self.controller = Centering()

    def heading(self):
        assert "GLOBAL_POSITION_INT" in self.messages, "Main-camera guidance needs aircraft heading"
        position = self.messages["GLOBAL_POSITION_INT"]
        assert (
            0 <= position.hdg < 36000 and time.monotonic() - self.times["GLOBAL_POSITION_INT"] <= 1
        ), "Main-camera guidance needs fresh aircraft heading"
        return math.radians(position.hdg / 100)

    def feedback(self):
        for kind in (
            "GIMBAL_MANAGER_INFORMATION",
            "GIMBAL_MANAGER_STATUS",
            "GIMBAL_DEVICE_ATTITUDE_STATUS",
        ):
            assert kind in self.messages, f"Missing {kind} for the main camera"
            if kind != "GIMBAL_MANAGER_INFORMATION":
                assert time.monotonic() - self.times[kind] <= 1, f"Stale {kind} for the main camera"
        assert self.claimed and self.owns(), "Main-camera gimbal ownership changed"
        info = self.messages["GIMBAL_MANAGER_INFORMATION"]
        required = (
            MAV.GIMBAL_MANAGER_CAP_FLAGS_HAS_PITCH_AXIS
            | MAV.GIMBAL_MANAGER_CAP_FLAGS_HAS_PITCH_LOCK
            | MAV.GIMBAL_MANAGER_CAP_FLAGS_HAS_YAW_AXIS
            | MAV.GIMBAL_MANAGER_CAP_FLAGS_HAS_YAW_LOCK
        )
        assert info.cap_flags & required == required, "Main camera requires pitch and yaw control"
        assert all(
            math.isfinite(v) for v in (info.pitch_min, info.pitch_max, info.yaw_min, info.yaw_max)
        ), "The gimbal must report its mechanical angle limits"
        assert info.pitch_min < info.pitch_max and info.yaw_min < info.yaw_max, (
            "Invalid main-camera mechanical limits"
        )
        pose = self.messages["GIMBAL_DEVICE_ATTITUDE_STATUS"]
        assert pose.failure_flags == 0, f"Gimbal reports failure flags {pose.failure_flags}"
        assert pose.flags & MAV.GIMBAL_DEVICE_FLAGS_YAW_LOCK, "Visual guidance requires yaw lock"
        orientation = quaternion_rotation(pose.q)
        vehicle_frame = bool(pose.flags & MAV.GIMBAL_DEVICE_FLAGS_YAW_IN_VEHICLE_FRAME)
        earth_frame = bool(pose.flags & MAV.GIMBAL_DEVICE_FLAGS_YAW_IN_EARTH_FRAME)
        assert not (vehicle_frame and earth_frame), "Conflicting gimbal yaw coordinate frames"
        if vehicle_frame:
            orientation = Rotation.from_euler("Z", self.heading()) * orientation
        quaternion = orientation.as_quat()[[3, 0, 1, 2]].tolist()
        return info, quaternion

    def point(self, pitch, yaw):
        assert math.isfinite(pitch) and math.isfinite(yaw), "Gimbal target must be finite radians"
        self.connection.mav.gimbal_manager_set_pitchyaw_send(
            self.sysid, 1, LOCK_FLAGS, DEVICE_ID, pitch, yaw, math.nan, math.nan
        )

    def center(self, box, fov_deg, observed_q):
        started = time.monotonic()
        info, orientation = self.feedback()
        movement = self.controller.step(box, fov_deg, orientation, observed_q, started)
        pitch = max(info.pitch_min, min(info.pitch_max, movement["pitch_rad"]))
        yaw = movement["yaw_rad"]
        if info.yaw_max - info.yaw_min < 2 * math.pi - 0.01:
            heading = self.heading()
            yaw = wrapped(heading + max(info.yaw_min, min(info.yaw_max, wrapped(yaw - heading))))
        movement["limited"] = not (
            math.isclose(pitch, movement["pitch_rad"], abs_tol=1e-6)
            and math.isclose(wrapped(yaw - movement["yaw_rad"]), 0, abs_tol=1e-6)
        )
        self.point(pitch, yaw)
        return {
            "status": "moved",
            "yaw_delta_deg": movement["yaw_delta_deg"],
            "pitch_delta_deg": movement["pitch_delta_deg"],
            "elapsed_ms": (time.monotonic() - started) * 1000,
        }, movement

    async def release(self):
        if not self.claimed:
            return
        await self.command(
            MAV.MAV_CMD_DO_GIMBAL_MANAGER_CONFIGURE, [-3, -3, -1, -1, 0, 0, DEVICE_ID]
        )
        await self.command(
            MAV.MAV_CMD_REQUEST_MESSAGE,
            [MAV.MAVLINK_MSG_ID_GIMBAL_MANAGER_STATUS, 0, 0, 0, 0, 0, 0],
        )
        await self.until(lambda: not self.owns(), 3)
        self.claimed = False

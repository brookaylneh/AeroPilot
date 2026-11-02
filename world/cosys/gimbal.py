"""A MAVLink Gimbal v2 device attached to the existing Cosys fpv camera."""

import asyncio
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cosysairsim as airsim
import numpy as np
from pymavlink.dialects.v20 import ardupilotmega as MAV
from scipy.spatial.transform import Rotation

from mission_planner.gcs.gimbal import LOCK_FLAGS, quaternion_rotation
from world.cosys import client as sim
from world.cosys.capture import rotation


class CosysGimbal:
    def __init__(self, name, sysid, connection):
        self.name = name
        self.sysid = sysid
        self.connection = connection
        self.encoder = MAV.MAVLink(connection, srcSystem=sysid, srcComponent=MAV.MAV_COMP_ID_GIMBAL)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gimbal-" + name)
        self.pose_lock = threading.RLock()
        self.rpc = None
        self.target = None
        self.target_at = None
        self.target_flags = 0
        self.active = False
        self.started = time.monotonic()
        self.last_heartbeat = 0.0
        self.pose = None
        self.fov = None
        self.mount_position = None
        self.inbox = []
        self.running = False

    def information(self):
        flags = 0
        for key in (
            "HAS_ROLL_AXIS",
            "HAS_ROLL_LOCK",
            "HAS_PITCH_AXIS",
            "HAS_PITCH_LOCK",
            "HAS_YAW_AXIS",
            "HAS_YAW_LOCK",
            "SUPPORTS_INFINITE_YAW",
        ):
            flags |= getattr(MAV, "GIMBAL_DEVICE_CAP_FLAGS_" + key)
        self.encoder.gimbal_device_information_send(
            round((time.monotonic() - self.started) * 1000),
            b"Cosys",
            b"fpv",
            self.name.encode(),
            1,
            1,
            self.sysid,
            flags,
            0,
            -math.pi,
            math.pi,
            -math.pi / 2,
            math.pi / 2,
            -math.pi,
            math.pi,
        )

    def receive(self, message):
        if (
            not self.running
            or message.get_srcSystem() != self.sysid
            or message.get_srcComponent() != 1
        ):
            return
        if message.get_type() in (
            "GIMBAL_MANAGER_STATUS",
            "COMMAND_LONG",
            "GIMBAL_DEVICE_SET_ATTITUDE",
        ):
            self.inbox.append(message)

    def _receive(self, message):
        kind = message.get_type()
        if kind == "GIMBAL_MANAGER_STATUS" and message.gimbal_device_id == 1:
            self.active = (
                message.primary_control_sysid == self.connection.mav.srcSystem
                and message.primary_control_compid == self.connection.mav.srcComponent
                and bool(message.flags & MAV.GIMBAL_MANAGER_FLAGS_YAW_LOCK)
            )
        elif kind == "COMMAND_LONG" and message.target_component == MAV.MAV_COMP_ID_GIMBAL:
            assert message.target_system == self.sysid, "Gimbal request targets another aircraft"
            assert message.command == MAV.MAV_CMD_REQUEST_MESSAGE, (
                f"Unsupported simulated gimbal command {message.command}"
            )
            assert message.param1 == MAV.MAVLINK_MSG_ID_GIMBAL_DEVICE_INFORMATION, (
                "Unsupported gimbal information request"
            )
            self.information()
            self.encoder.command_ack_send(
                message.command, MAV.MAV_RESULT_ACCEPTED, 0, 0, self.sysid, 1
            )
        elif (
            kind == "GIMBAL_DEVICE_SET_ATTITUDE"
            and message.target_component == MAV.MAV_COMP_ID_GIMBAL
        ):
            assert message.target_system == self.sysid, "Gimbal attitude targets another aircraft"
            assert not message.flags & (
                MAV.GIMBAL_DEVICE_FLAGS_RETRACT | MAV.GIMBAL_DEVICE_FLAGS_NEUTRAL
            ), "The simulated fpv mount does not implement a retract mechanism"
            if self.active:
                assert message.flags & MAV.GIMBAL_DEVICE_FLAGS_YAW_LOCK, (
                    "Main-camera tracking requires world yaw lock"
                )
            quaternion_rotation(message.q)
            assert all(
                math.isnan(v)
                for v in (
                    message.angular_velocity_x,
                    message.angular_velocity_y,
                    message.angular_velocity_z,
                )
            ), "The main-camera profile requires finite angle targets, not persistent rate commands"
            self.target = list(message.q)
            self.target_at = time.monotonic()
            self.target_flags = message.flags

    def _sample(self):
        with self.pose_lock:
            if self.rpc is None:
                self.rpc = airsim.MultirotorClient(ip="127.0.0.1", port=sim.PORT, timeout_value=3)
            body = self.rpc.simGetVehiclePose(vehicle_name=self.name)
            info = self.rpc.simGetCameraInfo("fpv", vehicle_name=self.name)
            body_rotation = rotation(sim.quat(body.orientation))
            if self.mount_position is None:
                self.mount_position = body_rotation.inv().apply(
                    np.array(
                        [
                            getattr(info.pose.position, k + "_val")
                            - getattr(body.position, k + "_val")
                            for k in "xyz"
                        ]
                    )
                )
                assert np.isfinite(self.mount_position).all(), "Invalid fpv camera mount position"
            if self.active and self.target_flags & MAV.GIMBAL_DEVICE_FLAGS_YAW_LOCK:
                assert self.target is not None and self.target_at is not None, (
                    "Gimbal manager has not supplied a target"
                )
                assert time.monotonic() - self.target_at < 0.5, "ArduPilot gimbal output stopped"
                relative = (body_rotation.inv() * quaternion_rotation(self.target)).as_quat()
                self.rpc.simSetCameraPose(
                    "fpv",
                    airsim.Pose(
                        airsim.Vector3r(*self.mount_position), airsim.Quaternionr(*relative)
                    ),
                    vehicle_name=self.name,
                )
                info = self.rpc.simGetCameraInfo("fpv", vehicle_name=self.name)
            q = info.pose.orientation
            # AP_Mount forwards device attitude with earth roll/pitch and vehicle-frame yaw.
            forward = body_rotation.apply([1, 0, 0])
            heading = math.atan2(forward[1], forward[0])
            relative_yaw = Rotation.from_euler("Z", -heading) * quaternion_rotation(
                [q.w_val, q.x_val, q.y_val, q.z_val]
            )
            return relative_yaw.as_quat()[[3, 0, 1, 2]].tolist(), info.fov

    async def run(self):
        self.running = True
        loop = asyncio.get_running_loop()
        try:
            while True:
                tick = time.monotonic()
                messages, self.inbox = self.inbox, []
                for message in messages:
                    self._receive(message)
                if tick - self.last_heartbeat >= 1:
                    self.encoder.heartbeat_send(
                        MAV.MAV_TYPE_GIMBAL, MAV.MAV_AUTOPILOT_INVALID, 0, 0, MAV.MAV_STATE_ACTIVE
                    )
                    self.last_heartbeat = tick
                pending = loop.run_in_executor(self.executor, self._sample)
                try:
                    self.pose, self.fov = await asyncio.shield(pending)
                finally:
                    if not pending.done():
                        await pending
                self.encoder.gimbal_device_attitude_status_send(
                    self.sysid,
                    1,
                    round((time.monotonic() - self.started) * 1000),
                    LOCK_FLAGS | MAV.GIMBAL_DEVICE_FLAGS_YAW_IN_VEHICLE_FRAME,
                    self.pose,
                    math.nan,
                    math.nan,
                    math.nan,
                    0,
                )
                await asyncio.sleep(max(0, 0.05 - (time.monotonic() - tick)))
        except Exception as error:
            raise RuntimeError(
                f"{self.name}: main-camera MAVLink gimbal failed: {error}"
            ) from error
        finally:
            self.running = False
            self.inbox.clear()
            try:
                if self.rpc is not None:
                    await loop.run_in_executor(self.executor, self.rpc.client.close)
            finally:
                self.executor.shutdown(wait=True, cancel_futures=True)

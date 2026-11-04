"""Independent camera-pose sampling with simulator and MAVLink motion context."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from world.cosys.capture import nadir_error
from world.cosys.client import NAMES, connect, pose, quat, xyz

SAMPLE_INTERVAL_SECONDS = 0.05


class CameraMonitor:
    def __init__(self, directory, fleet):
        self.directory = Path(directory)
        self.fleet = fleet
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-metrics")
        self.rpc = None
        self.stopping = asyncio.Event()
        self.task = None

    def _sample(self):
        if self.rpc is None:
            self.rpc = connect()
        result = []
        for name in NAMES:
            start = time.time_ns()
            camera = self.rpc.simGetCameraInfo("front_center", vehicle_name=name)
            imu = self.rpc.getImuData(imu_name="Imu", vehicle_name=name)
            end = time.time_ns()
            result.append(
                {
                    "droneId": name,
                    "sampleStartedUnixNs": start,
                    "sampleFinishedUnixNs": end,
                    "simulatorTimestampNs": imu.time_stamp,
                    "cameraPoseLocalNed": pose(camera.pose),
                    "nadirErrorDegrees": nadir_error(quat(camera.pose.orientation)),
                    "bodyOrientationNed": quat(imu.orientation),
                    "bodyAngularVelocityRadPerSecond": xyz(imu.angular_velocity),
                    "bodySpecificForceMps2": xyz(imu.linear_acceleration),
                }
            )
        return result

    async def start(self):
        assert self.task is None
        self.directory.mkdir(parents=True, exist_ok=True)
        self.task = asyncio.create_task(self._run(), name="camera-metrics")

    async def _run(self):
        loop = asyncio.get_running_loop()
        with (self.directory / "camera.jsonl").open("x") as output:
            while not self.stopping.is_set():
                started = time.perf_counter()
                before = {state["id"]: state for state in self.fleet.snapshot()}
                samples = await loop.run_in_executor(self.executor, self._sample)
                after = {state["id"]: state for state in self.fleet.snapshot()}
                for sample in samples:
                    name = sample["droneId"]
                    sample["mavlink"] = [
                        {
                            key: state[key]
                            for key in (
                                "mode",
                                "armed",
                                "received_at_ms",
                                "north_m",
                                "east_m",
                                "heading_deg",
                                "ground_speed_mps",
                                "climb_rate_mps",
                                "mission_current",
                            )
                        }
                        for state in (before[name], after[name])
                    ]
                    output.write(json.dumps(sample, allow_nan=False) + "\n")
                output.flush()
                remaining = SAMPLE_INTERVAL_SECONDS - (time.perf_counter() - started)
                if remaining > 0:
                    await asyncio.sleep(remaining)

    def check(self):
        assert self.task is not None
        if self.task.done():
            self.task.result()
            assert self.stopping.is_set(), "Camera monitor stopped unexpectedly"

    async def close(self):
        assert self.task is not None
        self.stopping.set()
        try:
            await self.task
        finally:
            if self.rpc is not None:
                await asyncio.get_running_loop().run_in_executor(
                    self.executor, self.rpc.client.close
                )
            self.executor.shutdown(wait=True)

"""One original 4K photograph from the independent, stabilized 4x camera."""

import asyncio
import math
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy.spatial.transform import Rotation

from world.cosys import client as sim
from world.cosys.capture import rotation
from world.cosys.inspection import capture_frame, point_camera

CAMERA_ID = "evidence_zoom"
FOV_DEG = math.degrees(2 * math.atan(1 / 4))


def target_direction(metadata, box):
    assert metadata["camera_id"] == "fpv", "Evidence must be aimed from the main camera"
    width, height = metadata["width"], metadata["height"]
    assert (width, height) == (3840, 2160)
    assert len(box) == 4 and all(math.isfinite(v) for v in box)
    x1, y1, x2, y2 = box
    assert 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height
    fov = metadata["horizontal_fov_deg"]
    assert math.isfinite(fov) and 1 < fov < 179
    focal = width / (2 * math.tan(math.radians(fov) / 2))
    direction = rotation(metadata["camera_pose_local_ned"]["orientation"]).apply(
        [1, ((x1 + x2) / 2 - width / 2) / focal, ((y1 + y2) / 2 - height / 2) / focal]
    )
    return direction / np.linalg.norm(direction)


class EvidenceCapture:
    def __init__(self, drone_id, directory):
        assert drone_id in sim.NAMES
        self.drone_id, self.directory = drone_id, directory
        self.rpc = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evidence-" + drone_id)
        self.lock = asyncio.Lock()

    def _capture(self, source, detection):
        started = time.monotonic()
        assert source["drone_id"] == self.drone_id
        assert 0 <= time.time() * 1000 - source["captured_at_ms"] <= 2000, (
            "The detection is too old to aim the evidence camera"
        )
        direction = target_direction(source, detection["box_xyxy"])
        desired = Rotation.from_euler(
            "ZYX",
            [
                math.atan2(direction[1], direction[0]),
                -math.atan2(direction[2], math.hypot(direction[0], direction[1])),
                0,
            ],
        )
        if self.rpc is None:
            self.rpc = sim.connect()
        rpc, name = self.rpc, self.drone_id
        main_fov = rpc.simGetCameraInfo("fpv", vehicle_name=name).fov
        assert math.isclose(
            rpc.simGetCameraInfo(CAMERA_ID, vehicle_name=name).fov, FOV_DEG, abs_tol=0.01
        ), "The installed evidence camera is not fixed at 4x"
        point_camera(rpc, name, CAMERA_ID, desired)
        metadata, jpeg = capture_frame(rpc, name, CAMERA_ID, self.directory, 0, quality=100)
        error = math.degrees(
            (desired.inv() * rotation(metadata["camera_pose_local_ned"]["orientation"])).magnitude()
        )
        assert error <= 0.5, "Evidence camera moved off its target during capture"
        assert metadata["timestamp_ns"] > source["timestamp_ns"], "Evidence photograph is stale"
        assert math.isclose(
            rpc.simGetCameraInfo("fpv", vehicle_name=name).fov, main_fov, abs_tol=0.01
        ), "Main camera zoom changed during evidence capture"
        metadata.update(
            zoom=4,
            source_frame_id=source["frame_id"],
            source_sha256=source["sha256"],
            source_track_id=detection["track_id"],
            source_captured_at_ms=source["captured_at_ms"],
            pointing_error_deg=error,
            acquisition_s=time.monotonic() - started,
        )
        return metadata, jpeg

    async def capture(self, source, detection):
        pending = asyncio.get_running_loop().run_in_executor(
            self.executor, self._capture, source, detection
        )
        try:
            return await asyncio.shield(pending)
        finally:
            if not pending.done():
                await pending

    async def close(self):
        if self.rpc is not None:
            await asyncio.get_running_loop().run_in_executor(self.executor, self.rpc.client.close)
        self.executor.shutdown(wait=True)

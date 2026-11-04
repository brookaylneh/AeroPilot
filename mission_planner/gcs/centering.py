"""Image-based camera centering in world NED, independent of the camera actuator."""

import math

import numpy as np
from scipy.spatial.transform import Rotation


def quaternion_rotation(q):
    values = np.asarray(q, dtype=float)
    assert values.shape == (4,) and np.isfinite(values).all(), "Expected finite w,x,y,z quaternion"
    assert abs(np.linalg.norm(values) - 1) < 1e-3, "Camera quaternion is not normalized"
    return Rotation.from_quat(values[[1, 2, 3, 0]])


def visible_box(box):
    assert len(box) == 4 and all(math.isfinite(v) for v in box), "Tracker box must be finite xyxy"
    x1, y1, x2, y2 = box
    assert x1 < x2 and y1 < y2, "Tracker box must have positive width and height"
    clipped = [max(0, x1), max(0, y1), min(960, x2), min(540, y2)]
    return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None


def wrapped(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def target_error(box, fov_deg, camera_q, observed_q):
    assert visible_box(box) is not None, "Target is outside the camera image"
    assert math.isfinite(fov_deg) and 1 < fov_deg < 179, "Invalid horizontal camera FOV"
    focal = 960 / (2 * math.tan(math.radians(fov_deg) / 2))
    error = np.array([(box[0] + box[2]) / 2 - 480, (box[1] + box[3]) / 2 - 270])
    target = quaternion_rotation(observed_q).apply([1, error[0] / focal, error[1] / focal])
    present = quaternion_rotation(camera_q).inv().apply(target)
    assert present[0] > 0, "Observed target is behind the current camera"
    return target, present[1:] / present[0] * focal


class Centering:
    def __init__(self):
        self.at = None
        self.rates = np.zeros(3)
        self.orientation = None

    def step(self, box, fov_deg, camera_q, observed_q, now):
        assert math.isfinite(now), "Invalid controller timestamp"
        dt = 0.1 if self.at is None else now - self.at
        assert dt > 0, "Camera guidance timestamps must increase"
        dt = min(dt, 0.2)
        self.at = now
        current = quaternion_rotation(camera_q)
        if self.orientation is None:
            self.orientation = current.as_euler("ZYX")
        # Integrate the world target; aircraft rotation must not become target motion.
        yaw, pitch, roll = self.orientation
        reference_q = Rotation.from_euler("ZYX", self.orientation).as_quat()[[3, 0, 1, 2]]
        target, error = target_error(box, fov_deg, reference_q, observed_q)
        desired = np.array(
            [
                wrapped(math.atan2(target[1], target[0]) - yaw),
                -math.atan2(target[2], math.hypot(target[0], target[1])) - pitch,
                wrapped(-roll),
            ]
        )
        centered = bool(abs(error[0]) <= 960 * 0.01 and abs(error[1]) <= 540 * 0.01)
        wanted = np.clip(desired, -math.radians(6), math.radians(6))
        if centered:
            wanted[:2] = 0
        self.rates += (1 - math.exp(-dt / 0.3)) * (wanted - self.rates)
        if centered:
            self.rates[:2] = 0
        delta = self.rates * dt
        self.orientation = np.array(
            [wrapped(yaw + delta[0]), pitch + delta[1], wrapped(roll + delta[2])]
        )
        return {
            "yaw_rad": float(self.orientation[0]),
            "pitch_rad": float(self.orientation[1]),
            "roll_rad": float(self.orientation[2]),
            "yaw_delta_deg": math.degrees(delta[0]),
            "pitch_delta_deg": math.degrees(delta[1]),
            "roll_delta_deg": math.degrees(delta[2]),
            "error_x_px": float(error[0]),
            "error_y_px": float(error[1]),
            "centered": centered,
        }

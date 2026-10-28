"""Box-ground contact and camera-error propagation in mission north/east metres."""

import copy
import math

import numpy as np
from scipy.integrate import quad
from scipy.special import ndtr

from mission_planner.agents.perception.geometry import Camera

ELLIPSE_95 = -2 * math.log(0.05)
ELLIPSE_99 = -2 * math.log(0.01)


def covariance(value):
    value = np.asarray(value, dtype=float)
    assert value.shape == (2, 2) and np.isfinite(value).all()
    assert np.allclose(value, value.T, rtol=0, atol=1e-12)
    assert np.linalg.eigvalsh(value).min() > 0
    return value


def localize(frame, box_xyxy, uncertainty):
    camera = Camera(frame)
    box = np.asarray(box_xyxy, dtype=float)
    assert box.shape == (4,) and np.isfinite(box).all()
    assert 0 <= box[0] < box[2] <= 1920 and 0 <= box[1] < box[3] <= 1080
    scale = camera.width / 1920
    pixel = np.array([(box[0] + box[2]) / 2, box[3]]) * scale
    ground = camera.ground(pixel)
    moment = covariance(uncertainty["pixel_second_moment_1080p2"]) * scale**2
    before, after = frame["vehicleStateBefore"], frame["vehicleStateAfter"]
    assert before["id"] == after["id"] == camera.name
    position_delta = np.array(
        [after["positionWorldM"][k] - before["positionWorldM"][k] for k in "xyz"]
    )
    quaternions = np.array(
        [[state["orientationNed"][k] for k in "xyzw"] for state in (before, after)]
    )
    assert np.isfinite(position_delta).all() and np.isfinite(quaternions).all()
    norms = np.linalg.norm(quaternions, axis=1)
    assert (np.abs(norms - 1) < 1e-5).all()
    dot = abs(float((quaternions[0] / norms[0]) @ (quaternions[1] / norms[1])))
    assert dot <= 1 + 1e-12
    angle = 2 * math.acos(min(1.0, dot))
    translation = float(np.linalg.norm(position_delta))
    position_floor = uncertainty["position_floor_m"]
    rotation_floor = math.radians(uncertainty["rotation_floor_deg"])
    assert math.isfinite(position_floor) and math.isfinite(rotation_floor)
    assert min(position_floor, rotation_floor) >= 0
    pose_moment = np.diag(
        [translation**2 + position_floor**2] * 3 + [angle**2 + rotation_floor**2] * 3
    )

    def sample(axis, offset):
        changed = copy.copy(camera)
        location = pixel.copy()
        if axis < 2:
            location[axis] += offset
        elif axis < 5:
            key = "xyz"[axis - 2]
            changed.world = {**camera.world, key: camera.world[key] + offset}
            changed.position = {
                **camera.position,
                key: camera.position[key] + offset * (1 if key != "z" else -1),
            }
        else:
            unit = np.eye(3)[axis - 5]
            skew = np.cross(unit, np.eye(3)).T
            rotation = np.eye(3) + math.sin(offset) * skew + (1 - math.cos(offset)) * skew @ skew
            changed.rotation = rotation @ camera.rotation
        value = changed.ground(location)
        return np.array([value["x"], value["y"]])

    steps = [1.0, 1.0, 0.001, 0.001, 0.001, 0.0001, 0.0001, 0.0001]
    jacobian = np.column_stack(
        [(sample(i, step) - sample(i, -step)) / (2 * step) for i, step in enumerate(steps)]
    )
    assert jacobian.shape == (2, 8) and np.isfinite(jacobian).all()
    pixel_error = jacobian[:, :2] @ moment @ jacobian[:, :2].T
    pose_error = jacobian[:, 2:] @ pose_moment @ jacobian[:, 2:].T
    total = covariance(pixel_error + pose_error)
    values, vectors = np.linalg.eigh(total)
    return {
        "ground": {"north_m": ground["x"], "east_m": ground["y"], "up_m": ground["z"]},
        "covariance_m2": total.tolist(),
        "contact_pixel": pixel.tolist(),
        "ellipse95": {
            "radii_m": np.sqrt(ELLIPSE_95 * values[::-1]).tolist(),
            "angle_rad": math.atan2(vectors[1, -1], vectors[0, -1]),
        },
        "uncertainty_components_m2": {"pixel": pixel_error.tolist(), "pose": pose_error.tolist()},
        "pose_bracket": {"position_m": translation, "rotation_rad": angle},
    }


def rectangle_mass(mean, matrix, low, high):
    sigma = math.sqrt(matrix[0, 0])
    conditional = math.sqrt(matrix[1, 1] - matrix[1, 0] ** 2 / matrix[0, 0])
    a, b = max(-9.0, (low[0] - mean[0]) / sigma), min(9.0, (high[0] - mean[0]) / sigma)
    if a >= b:
        return 0.0

    def integrand(z):
        center = mean[1] + matrix[1, 0] / sigma * z
        span = ndtr((high[1] - center) / conditional) - ndtr((low[1] - center) / conditional)
        return math.exp(-z * z / 2) / math.sqrt(2 * math.pi) * span

    value, error = quad(integrand, a, b, epsabs=1e-11, epsrel=1e-8)
    assert math.isfinite(value) and 0 <= value <= 1 + 1e-10 and error < 1e-8
    return value


def gaussian_cells(mean, matrix, grid):
    mean, matrix = np.asarray(mean, dtype=float), covariance(matrix)
    assert mean.shape == (2,) and np.isfinite(mean).all()
    low, high, areas = grid["low"], grid["high"], grid["areas"]
    assert low.shape == high.shape == (len(areas), 2) and (high > low).all()
    mass = np.zeros(len(areas))
    radius = 9 * np.sqrt(np.diag(matrix))
    candidates = np.flatnonzero(
        ((high >= mean - radius) & (low <= mean + radius)).all(axis=1) & (areas > 0)
    )
    for i in candidates:
        mass[i] = (
            rectangle_mass(mean, matrix, low[i], high[i]) * areas[i] / np.prod(high[i] - low[i])
        )
    retained = float(mass.sum())
    assert 0 < retained <= 1 + 1e-8
    mass /= retained
    assert np.isfinite(mass).all() and (mass >= 0).all() and abs(float(mass.sum()) - 1) < 1e-9
    return mass, retained


def ellipse_weights(localization, grid, threshold=ELLIPSE_95):
    center = np.array([localization["ground"][k] for k in ("north_m", "east_m")])
    matrix = covariance(localization["covariance_m2"])
    assert math.isfinite(threshold) and threshold > 0
    low, high, areas = grid["low"], grid["high"], grid["areas"]
    radii = np.sqrt(threshold * np.diag(matrix))
    candidates = np.flatnonzero(
        ((high >= center - radii) & (low <= center + radii)).all(axis=1) & (areas > 0)
    )
    weights = np.zeros(len(areas))
    conditional = math.sqrt(np.linalg.det(matrix) / matrix[0, 0])
    for i in candidates:
        a, b = max(low[i, 0], center[0] - radii[0]), min(high[i, 0], center[0] + radii[0])
        if a >= b:
            continue

        def span(north):
            delta = north - center[0]
            middle = center[1] + matrix[1, 0] / matrix[0, 0] * delta
            half = conditional * math.sqrt(max(0, threshold - delta**2 / matrix[0, 0]))
            return max(0, min(high[i, 1], middle + half) - max(low[i, 1], middle - half))

        value, error = quad(
            span, a, b, epsabs=1e-8, epsrel=1e-7, points=[center[0]] if a < center[0] < b else None
        )
        assert error < 1e-5 and 0 <= value <= float(np.prod(high[i] - low[i])) + 1e-8
        weights[i] = min(1.0, value / np.prod(high[i] - low[i]))
    assert np.isfinite(weights).all() and ((weights >= 0) & (weights <= 1)).all()
    return weights

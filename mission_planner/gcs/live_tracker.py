"""CSRT on a native-detail patch stabilized with measured capture-time camera poses."""

import math

import cv2
import numpy as np

from mission_planner.gcs.centering import visible_box
from world.cosys.capture import rotation

AXES = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=float)


def optics(metadata):
    assert (metadata["width"], metadata["height"]) == (3840, 2160)
    fov = metadata["horizontal_fov_deg"]
    assert math.isfinite(fov) and 1 < fov < 179
    focal = 3840 / (2 * math.tan(math.radians(fov) / 2))
    matrix = np.array([[focal, 0, 1920], [0, focal, 1080], [0, 0, 1]])
    pose = rotation(metadata["camera_pose_local_ned"]["orientation"]).as_matrix()
    return matrix, pose


def agreement(box, reference):
    if box is None:
        return 0.0
    intersection = max(0, min(box[2], reference[2]) - max(box[0], reference[0])) * max(
        0, min(box[3], reference[3]) - max(box[1], reference[1])
    )
    area = (box[2] - box[0]) * (box[3] - box[1])
    target = (reference[2] - reference[0]) * (reference[3] - reference[1])
    assert area > 0 and target > 0
    return intersection / (area + target - intersection)


class LiveTracker:
    def __init__(self):
        cv2.setNumThreads(1)
        self.tracker = None
        self.timestamp_ns = 0

    def seed(self, image, box, metadata):
        assert (
            isinstance(image, np.ndarray)
            and image.shape == (2160, 3840, 3)
            and image.dtype == np.uint8
        ), "Tracking requires the original 4K camera pixels"
        timestamp_ns = metadata["timestamp_ns"]
        assert type(timestamp_ns) is int and timestamp_ns > 0, "Invalid camera capture timestamp"
        assert len(box) == 4 and all(math.isfinite(v) for v in box), "Invalid SAM seed box"
        x1, y1, x2, y2 = box
        assert 0 <= x1 < x2 <= 3840 and 0 <= y1 < y2 <= 2160, "SAM seed is outside the 4K image"
        # CSRT uses a local search window; retain native pixels for tiny aerial subjects.
        # Large subjects use a larger patch at the same sampling resolution.
        width = min(3840, max(960, math.ceil((x2 - x1) * 4)))
        height = min(2160, max(540, math.ceil((y2 - y1) * 4)))
        left = max(0, min(3840 - width, math.floor((x1 + x2 - width) / 2)))
        top = max(0, min(2160 - height, math.floor((y1 + y2 - height) / 2)))
        self.size = width, height
        self.transform = np.array([[1, 0, -left], [0, 1, -top], [0, 0, 1]], dtype=float)
        self.matrix, self.pose = optics(metadata)
        parameters = cv2.TrackerCSRT_Params()
        parameters.template_size = 128
        parameters.number_of_scales = 17
        self.tracker = cv2.TrackerCSRT_create(parameters)
        x, y = math.floor(x1), math.floor(y1)
        self.tracker.init(
            image[top : top + height, left : left + width],
            (x - left, y - top, math.ceil(x2) - x, math.ceil(y2) - y),
        )
        self.timestamp_ns = timestamp_ns
        return [v / 4 for v in box]

    def advance(self, image, metadata):
        assert (
            isinstance(image, np.ndarray)
            and image.shape == (2160, 3840, 3)
            and image.dtype == np.uint8
        ), "Tracking requires the original 4K camera pixels"
        assert self.tracker is not None, "CSRT has no selected SAM object"
        assert metadata["timestamp_ns"] > self.timestamp_ns, (
            "CSRT frames must have increasing capture timestamps"
        )
        matrix, pose = optics(metadata)
        homography = (
            self.transform
            @ self.matrix
            @ AXES
            @ self.pose.T
            @ pose
            @ AXES.T
            @ np.linalg.inv(matrix)
        )
        patch = cv2.warpPerspective(image, homography, self.size, flags=cv2.INTER_LINEAR)
        found, rect = self.tracker.update(patch)
        self.timestamp_ns = metadata["timestamp_ns"]
        if not found:
            self.tracker = None
            return None
        x, y, width, height = rect
        corners = np.array(
            [[[x, y], [x + width, y], [x + width, y + height], [x, y + height]]], np.float32
        )
        projected = cv2.perspectiveTransform(corners, np.linalg.inv(homography))[0] / 4
        box = [
            float(projected[:, 0].min()),
            float(projected[:, 1].min()),
            float(projected[:, 0].max()),
            float(projected[:, 1].max()),
        ]
        clipped = visible_box(box)
        if clipped is None:
            self.tracker = None
            return None
        return clipped

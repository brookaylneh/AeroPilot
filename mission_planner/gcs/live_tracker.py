"""Native CSRT tracking of the selected SAM object on the main camera frames."""

import math

import cv2
import numpy as np

from mission_planner.gcs.centering import visible_box


def pixels(jpeg):
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape == (2160, 3840, 3), (
        "Tracking requires the original 4K camera image"
    )
    return cv2.resize(image, (960, 540), interpolation=cv2.INTER_AREA)


class LiveTracker:
    def __init__(self):
        cv2.setNumThreads(1)
        self.tracker = None
        self.timestamp_ns = None

    def seed(self, jpeg, box, timestamp_ns):
        assert type(timestamp_ns) is int and timestamp_ns > 0, "Invalid camera capture timestamp"
        assert len(box) == 4 and all(math.isfinite(v) for v in box), "Invalid SAM seed box"
        x1, y1, x2, y2 = box
        assert 0 <= x1 < x2 <= 3840 and 0 <= y1 < y2 <= 2160, "SAM seed is outside the 4K image"
        image = pixels(jpeg)
        parameters = cv2.TrackerCSRT_Params()
        parameters.template_size = 128
        parameters.number_of_scales = 17
        self.tracker = cv2.TrackerCSRT_create(parameters)
        x, y = math.floor(x1 / 4), math.floor(y1 / 4)
        self.tracker.init(image, (x, y, math.ceil(x2 / 4) - x, math.ceil(y2 / 4) - y))
        self.timestamp_ns = timestamp_ns
        return [v / 4 for v in box]

    def advance(self, jpeg, timestamp_ns):
        assert self.tracker is not None, "CSRT has no selected SAM object"
        assert timestamp_ns > self.timestamp_ns, (
            "CSRT frames must have increasing capture timestamps"
        )
        self.timestamp_ns = timestamp_ns
        found, rect = self.tracker.update(pixels(jpeg))
        if not found:
            self.tracker = None
            return None
        x, y, width, height = rect
        box = [float(x), float(y), float(x + width), float(y + height)]
        if visible_box(box) is None:
            self.tracker = None
            return None
        return box

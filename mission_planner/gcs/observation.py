"""Control geometry from SAM masks, preserving the untouched sensor observation."""

import io

import cv2
import numpy as np
from PIL import Image

from mission_planner.agents.vision import decode_mask


def coherent_detection(detection, width, height):
    payload, _ = decode_mask(detection["mask"], width, height)
    with Image.open(io.BytesIO(payload)) as image:
        mask = np.asarray(image).astype(np.uint8)
    count, labels, components, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    assert count >= 2
    primary = 1 + int(np.argmax(components[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = components[primary]
    keep = np.ones(count, dtype=bool)
    keep[0] = False
    for index in range(1, count):
        left, top, cw, ch, pixels = components[index]
        gap = max(int(x - left - cw), int(left - x - w), int(y - top - ch), int(top - y - h))
        # Detached limbs stay with the subject; only tiny, remote islands are excluded.
        if pixels * 100 < area and gap > max(w, h):
            keep[index] = False
    ys, xs = np.nonzero(keep[labels])
    assert xs.size
    ox, oy = detection["mask"]["origin_xy"]
    box = [ox + int(xs.min()), oy + int(ys.min()), ox + int(xs.max()) + 1, oy + int(ys.max()) + 1]
    assert 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height
    excluded = int(components[np.flatnonzero(~keep)[1:], cv2.CC_STAT_AREA].sum())
    return {
        **detection,
        "box_xyxy": box,
        "source_box_xyxy": list(detection["box_xyxy"]),
        "mask_geometry": {
            "components": count - 1,
            "foreground_pixels": int(components[1:, cv2.CC_STAT_AREA].sum()),
            "excluded_pixels": excluded,
        },
    }


def control_detections(metadata, response):
    return [
        coherent_detection(d, metadata["width"], metadata["height"]) for d in response["detections"]
    ]

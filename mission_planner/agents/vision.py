"""Ordered detections from each drone's independent tracker, in original 4K pixels."""

import base64
import hashlib
import io
import math
import os
import time
import uuid

import httpx
import numpy as np
from PIL import Image

from mission_planner.agents.models import post

INFERENCE_HEIGHT = 1080


def check_target(target):
    assert isinstance(target, str) and 1 <= len(target) <= 100 and target.strip(), (
        "SAM requires a visible target phrase of 1–100 characters"
    )


def vision_http():
    socket = os.environ["SNR_VISION_SOCKET"]
    assert socket.startswith("/"), "Vision socket must be an absolute path"
    return httpx.Client(
        base_url="http://vision/",
        transport=httpx.HTTPTransport(uds=socket),
        timeout=5,
    )


def decode_mask(mask, width, height):
    assert set(mask) == {"origin_xy", "png_base64"}, "Invalid SAM mask fields"
    x, y = mask["origin_xy"]
    assert type(x) is int and type(y) is int and 0 <= x < width and 0 <= y < height
    payload = base64.b64decode(mask["png_base64"], validate=True)
    with Image.open(io.BytesIO(payload)) as image:
        assert image.format == "PNG" and image.mode == "L", "SAM mask must be a grayscale PNG"
        assert 0 < image.width <= width - x and 0 < image.height <= height - y, (
            "SAM mask exceeds its source frame"
        )
        pixels = np.asarray(image)
        assert ((pixels == 0) | (pixels == 255)).all() and (pixels == 255).any(), (
            "SAM mask must be nonempty and binary"
        )
        size = [image.width, image.height]
    return payload, size


def check_detection(detection, width, height):
    assert set(detection) == {
        "track_id",
        "class_name",
        "box_xyxy",
        "confidence",
        "observations",
        "mask",
    }, "SAM detection fields do not match the track contract"
    assert type(detection["track_id"]) is int and detection["track_id"] > 0, "Invalid SAM track ID"
    assert type(detection["observations"]) is int and detection["observations"] > 0, (
        "Invalid track observation count"
    )
    check_target(detection["class_name"])
    x1, y1, x2, y2 = detection["box_xyxy"]
    assert all(math.isfinite(v) for v in (x1, y1, x2, y2)), (
        "SAM box coordinates must be finite pixels"
    )
    assert 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height, "SAM box exceeds its source frame"
    assert math.isfinite(detection["confidence"]) and 0 <= detection["confidence"] <= 1, (
        "SAM confidence must be in [0, 1]"
    )
    decode_mask(detection["mask"], width, height)


class VisionTrack:
    def __init__(self, http, drone_id, target, threshold, height=INFERENCE_HEIGHT):
        check_target(target)
        assert drone_id in ("Drone1", "Drone2", "Drone3", "Drone4")
        assert math.isfinite(threshold) and 0.1 <= threshold <= 1
        assert height == 1080, "The SAM service uses the qualified 1080p preparation profile"
        self.http = http
        self.http.timeout = httpx.Timeout(5)
        self.session_id = str(uuid.uuid4())
        self.drone_id, self.target = drone_id, target
        self.threshold, self.height = threshold, height
        self.instance_id = None
        self.frame_index, self.timestamp_ns = -1, 0
        self.status = "new"

    def _post(self, action, body, timing=None, jpeg=None):
        try:
            if jpeg is None:
                return post(self.http, "vision/" + action, body, timing)
            started = time.monotonic()
            response = self.http.post(
                "vision/" + action,
                params=body,
                content=jpeg,
                headers={"Content-Type": "image/jpeg"},
            )
            if timing is not None:
                timing.update(
                    roundtrip_s=time.monotonic() - started,
                    request_bytes=len(jpeg),
                    status_code=response.status_code,
                )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            raise httpx.HTTPStatusError(
                f"SAM 3.1 {action} failed (HTTP {error.response.status_code}): "
                + error.response.text,
                request=error.request,
                response=error.response,
            ) from error

    def open(self):
        assert self.status == "new", "Vision session can only be opened once"
        self.status = "pending"
        body = {
            "session_id": self.session_id,
            "drone_id": self.drone_id,
            "target": self.target,
            "threshold": self.threshold,
            "inference_height": self.height,
        }
        result = self._post("start", body)
        for key, value in body.items():
            assert result[key] == value, f"Vision start changed {key}"
        assert result["model"] == "sam3.1" and result["tracker"] == "Object Multiplex", (
            "Unexpected vision model or tracker"
        )
        assert isinstance(result["instance_id"], str) and result["instance_id"], (
            "Vision worker identity is missing"
        )
        self.instance_id, self.status = result["instance_id"], "open"
        return result

    def advance(self, jpeg, timestamp_ns, timing=None):
        assert self.status == "open", "Vision session is not active"
        assert type(timestamp_ns) is int and timestamp_ns > self.timestamp_ns, (
            "Vision capture timestamp did not advance"
        )
        with Image.open(io.BytesIO(jpeg)) as image:
            assert image.format == "JPEG" and image.size == (3840, 2160), (
                "Vision evidence must be an original 3840x2160 JPEG"
            )
        body = {
            "session_id": self.session_id,
            "instance_id": self.instance_id,
            "frame_index": self.frame_index + 1,
            "timestamp_ns": timestamp_ns,
        }
        self.status = "pending"
        result = self._post("frame", body, timing, jpeg)
        for key in ("session_id", "instance_id", "frame_index", "timestamp_ns"):
            assert result[key] == body[key], f"Vision response changed {key}"
        assert (result["width"], result["height"]) == (3840, 2160), (
            "Vision response changed source dimensions"
        )
        assert result["inference_height"] == self.height, (
            "Vision response changed inference resolution"
        )
        assert result["sha256"] == hashlib.sha256(jpeg).hexdigest(), (
            "Vision response does not match the submitted JPEG hash"
        )
        detections = result["detections"]
        assert isinstance(detections, list), "Vision detections must be a list"
        identities = []
        for detection in detections:
            check_detection(detection, 3840, 2160)
            assert detection["class_name"] == self.target, (
                "Vision response changed the requested target"
            )
            identities.append(detection["track_id"])
        assert len(set(identities)) == len(identities), "SAM returned duplicate identities"
        self.frame_index, self.timestamp_ns = body["frame_index"], timestamp_ns
        self.status = "open"
        return result

    def close(self):
        assert self.status != "closed", "Vision session is already closed"
        self.status = "closed"
        if self.instance_id is not None:
            body = {"session_id": self.session_id, "instance_id": self.instance_id}
            assert self._post("close", body) == {**body, "status": "closed"}, (
                "Vision did not acknowledge session close"
            )

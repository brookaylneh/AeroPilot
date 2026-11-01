"""One ordered SAM video track on the configured remote provider."""

import base64
import io
import math
import uuid

import httpx
from PIL import Image

from mission_planner.agents.models import post
from mission_planner.agents.segmentation import decode_mask


class TemporalTrack:
    def __init__(self, http):
        self.http = http
        self.http.timeout = httpx.Timeout(5)
        self.session_id = str(uuid.uuid4())
        self.instance_id = None
        self.frame_index = -1
        self.timestamp_ns = 0
        self.size = None
        self.status = "new"

    def _post(self, path, body, timing):
        try:
            return post(self.http, path, body, timing)
        except httpx.HTTPStatusError as error:
            raise httpx.HTTPStatusError(
                f"SAM video request failed (HTTP {error.response.status_code}): {error.response.text}",
                request=error.request,
                response=error.response,
            ) from error

    def _frame(self, jpeg, timestamp_ns):
        assert type(timestamp_ns) is int and timestamp_ns > self.timestamp_ns, (
            "Tracking frame timestamp must increase"
        )
        with Image.open(io.BytesIO(jpeg)) as image:
            assert image.format == "JPEG" and image.size == (1920, 1080), (
                "Video tracking requires a 1920x1080 JPEG"
            )
            self.size = image.size
        return {
            "session_id": self.session_id,
            "frame_index": self.frame_index + 1,
            "timestamp_ns": timestamp_ns,
            "jpeg": base64.b64encode(jpeg).decode(),
        }

    def _response(self, result, body, timing):
        assert result["session_id"] == self.session_id, "Video tracking session changed"
        assert result["frame_index"] == body["frame_index"], "Video tracking frame changed"
        assert result["timestamp_ns"] == body["timestamp_ns"], "Video tracking timestamp changed"
        assert (result["width"], result["height"]) == self.size, "Video frame dimensions changed"
        instance = result["instance_id"]
        assert isinstance(instance, str) and instance
        if self.instance_id is not None:
            assert instance == self.instance_id, "SAM video container changed; track is unavailable"
        self.instance_id = instance
        status = result["status"]
        expected = ("seeded",) if body["frame_index"] == 0 else ("tracking", "lost")
        assert status in expected, "Video tracking returned an invalid lifecycle status"
        detection = result["detection"]
        if status == "seeded":
            assert detection is None and result["error"] is None
            assert result["raw_prediction"] is None and result["extraction"] is None
        else:
            raw, extraction = result["raw_prediction"], result["extraction"]
            area = raw["mask_area_px"]
            assert type(area) is int and area >= 0
            assert type(raw["presence_logit"]) in (int, float) and math.isfinite(
                raw["presence_logit"]
            )
            if area == 0:
                assert raw["mask"] is None
            else:
                decode_mask(raw["mask"], *self.size, area)
            areas = extraction["component_areas_px"]
            assert all(type(v) is int and v > 0 for v in areas) and sum(areas) == area
            assert all(
                type(extraction[key]) is int
                for key in ("area_threshold_px", "removed_pixels", "remaining_components")
            )
            limit = min(16, area // 2)
            removed = [v for v in areas if v <= limit]
            assert extraction["area_threshold_px"] == limit
            assert extraction["removed_component_areas_px"] == removed
            assert extraction["removed_pixels"] == sum(removed)
            assert extraction["remaining_components"] == len(areas) - len(removed)
        if status == "lost":
            assert detection is None and isinstance(result["error"], str) and result["error"]
        elif status == "tracking":
            assert result["error"] is None
            x1, y1, x2, y2 = detection["box_xyxy"]
            width, height = self.size
            assert all(math.isfinite(v) for v in (x1, y1, x2, y2))
            assert 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height
            assert math.isfinite(detection["confidence"]) and 0 <= detection["confidence"] <= 1
            x, y, pixels = decode_mask(detection["mask"], width, height, detection["mask_area_px"])
            ys, xs = pixels.nonzero()
            assert [x1, y1, x2, y2] == [
                x + int(xs.min()),
                y + int(ys.min()),
                x + int(xs.max()) + 1,
                y + int(ys.max()) + 1,
            ], "Video tracking box disagrees with its extracted mask"
            assert extraction["remaining_components"] == 1
            assert detection["mask_area_px"] == area - extraction["removed_pixels"]
        self.frame_index, self.timestamp_ns = body["frame_index"], body["timestamp_ns"]
        self.status = status
        timing["received_at_unix_s"] = (
            timing["started_at_unix_s"] + timing["prepare_s"] + timing["roundtrip_s"]
        )
        timing["completed_at_unix_s"] = timing["received_at_unix_s"] + timing["response_decode_s"]
        result["client_timing"] = timing
        return result

    def start(self, jpeg, timestamp_ns, detection, threshold, timing=None):
        assert self.status == "new", "Video tracking can only be started once"
        assert math.isfinite(threshold) and 0 <= threshold <= 1
        body = self._frame(jpeg, timestamp_ns)
        body.update(
            seed={key: detection[key] for key in ("box_xyxy", "mask_area_px", "mask")},
            threshold=threshold,
        )
        self.status = "pending"
        timing = {} if timing is None else timing
        return self._response(self._post("tracking/start", body, timing), body, timing)

    def advance(self, jpeg, timestamp_ns, timing=None):
        assert self.status in ("seeded", "tracking"), "Video track is not active"
        body = self._frame(jpeg, timestamp_ns)
        body["instance_id"] = self.instance_id
        self.status = "pending"
        timing = {} if timing is None else timing
        return self._response(self._post("tracking/frame", body, timing), body, timing)

    def close(self, timing=None):
        assert self.status != "closed", "Video track is already closed"
        self.status = "closed"
        if self.instance_id is None:
            return
        body = {"session_id": self.session_id, "instance_id": self.instance_id}
        result = self._post("tracking/close", body, timing)
        assert result == {**body, "status": "closed"}, "Video tracking close was not acknowledged"

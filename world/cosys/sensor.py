"""Capture every twelve metres of AUTO ground track and publish accepted records."""

import asyncio
import json
import math
import time

from world.cosys.capture import FrameCapture
from world.cosys.client import NAMES, write_json

SPACING_M = 12.0


def capture_ground_track(frame, trigger, total_m):
    previous = (trigger["north_m"], trigger["east_m"])
    previous_ns = int(trigger["received_at_ms"] * 1e6)
    timestamp = frame["validation"]["captureUnixNs"]
    assert previous_ns <= timestamp
    for sample in frame["mavlinkSamples"]:
        current_ns = sample["receivedAtUnixNs"]
        if current_ns <= previous_ns:
            continue
        current = tuple(sample["positionWorldM"][k] for k in "xy")
        distance = math.dist(previous, current)
        if current_ns >= timestamp:
            return total_m + distance * (timestamp - previous_ns) / (current_ns - previous_ns)
        total_m += distance
        previous, previous_ns = current, current_ns
    raise AssertionError("MAVLink does not bracket the acquired ground track")


class GroundTrack:
    def __init__(self):
        self.previous = None
        self.distance = 0.0
        self.total = 0.0

    def update(self, state):
        if state["mode"] != "AUTO" or not state["armed"]:
            self.previous = None
            return False
        current = (state["north_m"], state["east_m"])
        if self.previous is not None:
            delta = math.dist(current, self.previous)
            assert math.isfinite(delta) and delta < SPACING_M, (
                "Ground-track sample gap is too large"
            )
            self.distance += delta
            self.total += delta
        self.previous = current
        if self.distance < SPACING_M:
            return False
        self.distance -= SPACING_M
        return True


class MissionSensor:
    def __init__(self, directory, fleet, on_capture):
        self.capture = FrameCapture(directory, fleet)
        self.fleet, self.on_capture = fleet, on_capture
        self.tasks = []
        self.stopping = asyncio.Event()
        self.metrics = None

    async def start(self):
        assert not self.tasks and self.metrics is None
        self.metrics = (self.capture.directory / "sensor.jsonl").open("x")
        for name in NAMES:
            self.tasks.append(asyncio.create_task(self._run(name), name=f"sensor-{name}"))

    def check(self):
        for task in self.tasks:
            if task.done():
                task.result()
                assert self.stopping.is_set(), "Sensor task stopped unexpectedly"

    async def _capture(self, name, index, track_m, state, previous):
        start = time.perf_counter()
        frame, preview = await self.capture.capture(name, f"frame-{index:06d}")
        assert (frame["width"], frame["height"]) == (3840, 2160)
        position = frame["cameraPositionWorldM"]
        acquired_track = (
            capture_ground_track(frame, state, track_m) if frame["validation"]["accepted"] else None
        )
        spacing = (
            None
            if previous is None
            else math.dist([position[k] for k in "xy"], [previous["position"][k] for k in "xy"])
        )
        frame["captureTrigger"] = {
            "autoGroundTrackM": track_m,
            "requestedSpacingM": SPACING_M,
            "missionSequence": state["mission_current"],
            "mavlinkReceivedAtMs": state["received_at_ms"],
            "cameraSpacingM": spacing,
            "acquiredGroundTrackM": acquired_track,
            "acquiredGroundTrackSpacingM": None
            if previous is None or acquired_track is None
            else acquired_track - previous["groundTrackM"],
        }
        write_json(self.capture.directory / f"{frame['captureId']}.json", frame)
        metric = {
            "droneId": name,
            "captureId": frame["captureId"],
            "capturedAtUtc": frame["capturedAtUtc"],
            "captureLatencySeconds": frame["captureLatencySeconds"],
            "acquisitionWallSeconds": frame["acquisitionWallSeconds"],
            "writeLatencySeconds": frame["writeLatencySeconds"],
            "totalWallSeconds": time.perf_counter() - start,
            "transportBytes": frame["transportBytes"],
            "jpegBytes": frame["jpegBytes"],
            "previewBytes": frame["previewBytes"],
            "trigger": frame["captureTrigger"],
            "validation": frame["validation"],
        }
        self.metrics.write(json.dumps(metric, allow_nan=False) + "\n")
        self.metrics.flush()
        if frame["validation"]["accepted"]:
            await self.on_capture(frame, preview)
            return {"position": position, "groundTrackM": acquired_track}
        else:
            self.fleet.on_event({"kind": "capture_rejected", **metric})
            return previous

    async def _run(self, name):
        track = GroundTrack()
        index, pending, previous = 0, None, None
        try:
            while not self.stopping.is_set():
                self.fleet._check_tasks()
                if pending is not None and pending.done():
                    previous = pending.result()
                    pending = None
                state = next(s for s in self.fleet.snapshot() if s["id"] == name)
                if track.update(state):
                    assert pending is None, (
                        f"{name}: image capture cannot keep up with ground track"
                    )
                    index += 1
                    pending = asyncio.create_task(
                        self._capture(name, index, track.total, state, previous)
                    )
                await asyncio.sleep(0.05)
        finally:
            if pending is not None:
                await pending

    async def close(self):
        self.stopping.set()
        results = await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.metrics is not None:
            self.metrics.close()
        await self.capture.close()
        for result in results:
            if isinstance(result, BaseException):
                raise result

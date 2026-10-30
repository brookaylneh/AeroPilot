"""Capture geometry, timestamp and spacing rejection without simulator processes."""

import asyncio
import copy
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mission_planner.agents.perception.geometry import Camera
from world.cosys.capture import FrameCapture, capture_time, nadir_error, shadow_floor, validate
from world.cosys.sensor import GroundTrack, capture_ground_track


def frame():
    def truth(x, timestamp):
        return {
            "timestampNs": timestamp,
            "timestampBracketNs": [timestamp - 1_000_000, timestamp + 1_000_000],
            "sampleUnixNs": 1_700_000_000_000_000_000 + timestamp,
            "sampleWallSeconds": 0.002,
            "positionWorldM": {"x": x, "y": 20, "z": 30},
            "orientationNed": {"w": 1, "x": 0, "y": 0, "z": 0},
        }

    before, after = truth(10, 1_000_000_000), truth(12, 1_400_000_000)
    record = {
        "timestampNs": 1_200_000_000,
        "vehicleStateBefore": before,
        "vehicleStateAfter": after,
        "cameraPositionWorldM": {"x": 11.6, "y": 20, "z": 29.95},
        "cameraPoseLocalNed": {
            "orientation": {"w": math.sqrt(0.5), "x": 0, "y": -math.sqrt(0.5), "z": 0}
        },
    }
    mavlink = [
        {"receivedAtUnixNs": t["sampleUnixNs"], "positionWorldM": t["positionWorldM"]}
        for t in (before, after)
    ]
    return record, mavlink


class CaptureValidationTests(unittest.TestCase):
    def test_moving_body_and_mount_are_interpolated_at_image_time(self):
        record, samples = frame()
        result = validate(record, samples, None)
        self.assertTrue(result["accepted"], result)
        self.assertAlmostEqual(result["cameraGroundTruthErrorM"], 0)
        self.assertAlmostEqual(result["mavlinkGroundTruthErrorM"], 0)
        self.assertAlmostEqual(result["nadirErrorDegrees"], 0)
        self.assertEqual(result["captureUnixNs"], 1_700_000_001_200_000_000)

    def test_nadir_uses_optical_axis_and_rejects_rolled_pitch(self):
        self.assertAlmostEqual(nadir_error({"w": 1, "x": 0, "y": 0, "z": 0}), 90)
        record, samples = frame()
        angle = math.radians(-88)
        record["cameraPoseLocalNed"]["orientation"] = {
            "w": math.cos(angle / 2),
            "x": 0,
            "y": math.sin(angle / 2),
            "z": 0,
        }
        result = validate(record, samples, None)
        self.assertFalse(result["accepted"])
        self.assertAlmostEqual(result["nadirErrorDegrees"], 2)
        self.assertIn("Camera optical axis exceeds the nadir tolerance", result["reasons"])

    def test_rejects_image_and_mavlink_disagreement_independently(self):
        record, samples = frame()
        record["cameraPositionWorldM"]["x"] += 1
        result = validate(record, samples, None)
        self.assertFalse(result["accepted"])
        self.assertIn("Image camera position disagrees with ground truth", result["reasons"])
        record, samples = frame()
        samples = copy.deepcopy(samples)
        for sample in samples:
            sample["positionWorldM"]["y"] += 2.1
        result = validate(record, samples, None)
        self.assertFalse(result["accepted"])
        self.assertIn("Image-inferred body position disagrees with MAVLink", result["reasons"])

    def test_rejects_unsynchronized_timestamp_and_missing_telemetry_bracket(self):
        record, samples = frame()
        record["timestampNs"] = 1_500_000_000
        result = validate(record, samples, None)
        self.assertFalse(result["accepted"])
        self.assertIn("Image timestamp is outside the ground-truth bracket", result["reasons"])
        self.assertIn("Image time is outside the MAVLink receipt bracket", result["reasons"])

    def test_stopped_physics_and_invalid_quaternion_fail_loudly(self):
        record, _ = frame()
        before = record["vehicleStateBefore"]
        with self.assertRaisesRegex(AssertionError, "clock must advance"):
            capture_time(record["timestampNs"], before, before)
        with self.assertRaises(AssertionError):
            nadir_error({"w": 2, "x": 0, "y": 0, "z": 0})

    def test_actor_motion_rejects_without_frozen_physics(self):
        record, samples = frame()
        record["observation"] = {"groundWorldM": {"x": 0, "y": 0, "z": 0}}
        result = validate(record, samples, {"groundWorldM": {"x": 0.02, "y": 0, "z": 0}})
        self.assertFalse(result["accepted"])
        self.assertIn("Calibration actor moved during capture", result["reasons"])

    def test_shadow_floor_rejects_exact_black_shadows(self):
        lit = bytes([30, 40, 50]) * 64
        self.assertAlmostEqual(shadow_floor(lit, 8, 8), 0.2126 * 30 + 0.7152 * 40 + 0.0722 * 50, 4)
        # Rows 0-3 black: half of the stride-4 samples.
        with self.assertRaisesRegex(AssertionError, "Exact-black"):
            shadow_floor(bytes(96) + lit[96:], 8, 8)
        with self.assertRaises(AssertionError):
            shadow_floor(lit, 8, 7)


class GroundTrackTests(unittest.TestCase):
    def sample(self, x, y, mode="AUTO"):
        return {"north_m": x, "east_m": y, "mode": mode, "armed": True}

    def test_turn_distance_uses_ground_track_not_chord(self):
        track = GroundTrack()
        self.assertFalse(track.update(self.sample(0, 0)))
        self.assertFalse(track.update(self.sample(6, 0)))
        self.assertTrue(track.update(self.sample(6, 6)))
        self.assertEqual(track.auto_total, 12)
        self.assertFalse(track.update(self.sample(6, 6)))

    def test_brake_travel_is_excluded_when_auto_resumes(self):
        track = GroundTrack()
        track.update(self.sample(0, 0))
        track.update(self.sample(7, 0))
        self.assertFalse(track.update(self.sample(30, 0, "BRAKE")))
        self.assertFalse(track.update(self.sample(31, 0)))
        self.assertTrue(track.update(self.sample(36, 0)))
        self.assertEqual(track.auto_total, 12)

    def test_image_during_brake_and_resumed_image_share_physical_track(self):
        track = GroundTrack()

        def update(mode, north, ms):
            state = {**self.sample(north, 0, mode), "received_at_ms": ms}
            return state, track.update(state)

        def record(timestamp, rows):
            return {
                "validation": {"captureUnixNs": timestamp},
                "mavlinkSamples": [
                    {
                        "receivedAtUnixNs": ms * 1_000_000,
                        "positionWorldM": {"x": north, "y": 0},
                        "mode": mode,
                    }
                    for ms, north, mode in rows
                ],
            }

        update("AUTO", 0, 0)
        update("AUTO", 6, 500)
        trigger, fired = update("AUTO", 12, 1000)
        self.assertTrue(fired)
        at_trigger = track.physical_total
        self.assertFalse(update("BRAKE", 15, 2000)[1])
        first = capture_ground_track(
            record(2_000_000_000, [(1000, 12, "AUTO"), (2000, 15, "BRAKE")]),
            trigger,
            at_trigger,
        )
        self.assertEqual(first, 15)
        self.assertFalse(update("BRAKE", 17, 2500)[1])
        self.assertFalse(update("AUTO", 17, 3000)[1])
        self.assertFalse(update("AUTO", 23, 3500)[1])
        trigger, fired = update("AUTO", 29, 4000)
        self.assertTrue(fired)
        self.assertEqual(track.auto_total, 24)
        self.assertEqual(track.physical_total, 29)
        second = capture_ground_track(
            record(4_001_000_000, [(4010, 29, "AUTO")]), trigger, track.physical_total
        )
        self.assertEqual(second - first, 14)

    def test_large_sample_gap_fails_instead_of_skipping_frames(self):
        track = GroundTrack()
        track.update(self.sample(0, 0))
        with self.assertRaisesRegex(AssertionError, "sample gap"):
            track.update(self.sample(13, 0))

    def test_acquired_spacing_includes_motion_between_trigger_and_image(self):
        record = {
            "validation": {"captureUnixNs": 1_500_000_000},
            "mavlinkSamples": [
                {"receivedAtUnixNs": 1_000_000_000, "positionWorldM": {"x": 2, "y": 0}},
                {"receivedAtUnixNs": 2_000_000_000, "positionWorldM": {"x": 2, "y": 4}},
            ],
        }
        trigger = {"north_m": 0, "east_m": 0, "received_at_ms": 0}
        self.assertEqual(capture_ground_track(record, trigger, 12), 16)


class AsyncCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_image_io_keeps_telemetry_loop_running_and_writes_camera_contract(self):
        ticks = []
        stopping = asyncio.Event()

        async def telemetry():
            while not stopping.is_set():
                ticks.append(time.monotonic())
                await asyncio.sleep(0.005)

        def acquire(name, observe):
            record, _ = frame()
            record["vehicleStateBefore"]["sampleUnixNs"] = time.time_ns()
            time.sleep(0.12)
            record["vehicleStateAfter"]["sampleUnixNs"] = time.time_ns()
            record.update(
                schemaVersion=1,
                vehicleId=name,
                droneId=name,
                width=160,
                height=90,
                horizontalFovDegrees=70,
                localNedOriginUeCm={"x": 0, "y": 0, "z": 4000},
                coordinateSystem="UE_LH_Z_UP_CENTIMETERS",
                physicsPausedForCapture=False,
            )
            record["cameraPoseLocalNed"]["position"] = {"x": 11.6, "y": 20, "z": 10.05}
            return record, bytes(160 * 90 * 3), None

        def mavlink(fleet, name):
            return {
                "receivedAtUnixNs": time.time_ns(),
                "positionWorldM": {"x": 11, "y": 20, "z": 30},
            }

        with tempfile.TemporaryDirectory() as directory:
            capture = FrameCapture(directory, SimpleNamespace(_check_tasks=lambda: None))
            task = asyncio.create_task(telemetry())
            try:
                with (
                    mock.patch.object(capture, "_acquire", side_effect=acquire),
                    mock.patch("world.cosys.capture.mavlink_sample", side_effect=mavlink),
                ):
                    record, preview = await capture.capture("Drone1", "test")
                self.assertGreater(len(ticks), 2)
                self.assertTrue(record["validation"]["accepted"], record["validation"])
                self.assertEqual(Camera(record).width, 160)
                self.assertEqual((preview["width"], preview["height"]), (960, 540))
                path = Path(record["imagePath"])
                self.assertTrue(path.exists())
                self.assertEqual(json.loads(path.with_suffix(".json").read_text()), record)
            finally:
                stopping.set()
                await task
                await capture.close()


if __name__ == "__main__":
    unittest.main()

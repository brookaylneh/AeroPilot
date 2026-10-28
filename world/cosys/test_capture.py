"""Capture geometry, timestamp and spacing rejection without simulator processes."""

import copy
import math
import unittest

from world.cosys.capture import capture_time, nadir_error, validate
from world.cosys.sensor import GroundTrack


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


class GroundTrackTests(unittest.TestCase):
    def sample(self, x, y, mode="AUTO"):
        return {"north_m": x, "east_m": y, "mode": mode, "armed": True}

    def test_turn_distance_uses_ground_track_not_chord(self):
        track = GroundTrack()
        self.assertFalse(track.update(self.sample(0, 0)))
        self.assertFalse(track.update(self.sample(6, 0)))
        self.assertTrue(track.update(self.sample(6, 6)))
        self.assertEqual(track.total, 12)
        self.assertFalse(track.update(self.sample(6, 6)))

    def test_brake_travel_is_excluded_when_auto_resumes(self):
        track = GroundTrack()
        track.update(self.sample(0, 0))
        track.update(self.sample(7, 0))
        self.assertFalse(track.update(self.sample(30, 0, "BRAKE")))
        self.assertFalse(track.update(self.sample(31, 0)))
        self.assertTrue(track.update(self.sample(36, 0)))
        self.assertEqual(track.total, 12)

    def test_large_sample_gap_fails_instead_of_skipping_frames(self):
        track = GroundTrack()
        track.update(self.sample(0, 0))
        with self.assertRaisesRegex(AssertionError, "sample gap"):
            track.update(self.sample(13, 0))


if __name__ == "__main__":
    unittest.main()

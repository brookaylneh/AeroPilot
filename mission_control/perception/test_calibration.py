"""Synthetic numerical checks; fixtures are never reported as calibration measurements."""

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from mission_control.perception.analyze_calibration import analyze, operating
from mission_control.perception.geometry import Camera
from mission_control.perception.glimmer_calibration import annotate, glimmer, linked_crops
from world.simulator import client as sim


def frame():
    q = sim.airsim.euler_to_quaternion(0, -math.pi / 2, 0)
    return dict(
        schemaVersion=1,
        coordinateSystem="UE_LH_Z_UP_CENTIMETERS",
        vehicleId="Drone2",
        localNedOriginUeCm=dict(x=1000, y=2000, z=3000),
        width=1920,
        height=1080,
        horizontalFovDegrees=70,
        cameraPoseLocalNed=dict(position=dict(x=0, y=0, z=0), orientation=sim.quat(q)),
        cameraPositionWorldM=dict(x=10, y=20, z=30),
    )


class GeometryTests(unittest.TestCase):
    def test_cardinal_axes_and_terrain(self):
        camera = Camera(frame())
        np.testing.assert_allclose(camera.project(dict(x=10, y=20, z=0)), [960, 540])
        self.assertLess(camera.project(dict(x=11, y=20, z=0))[1], 540)
        self.assertGreater(camera.project(dict(x=10, y=21, z=0))[0], 960)
        with patch.object(sim, "terrain_height", lambda x, y: 0.02 * x + 0.03 * y):
            self.assertLess(camera.check(), 0.5)
            ground = camera.ground([100, 900])
            self.assertLess(abs(ground["z"] - sim.terrain_height(ground["x"], ground["y"])), 0.005)

    def test_recorded_origin_and_horizon_fail(self):
        metadata = frame()
        metadata["localNedOriginUeCm"]["x"] += 100
        with self.assertRaises(AssertionError):
            Camera(metadata)
        metadata = frame()
        metadata["cameraPoseLocalNed"]["orientation"] = dict(x=0, y=0, z=0, w=1)
        with self.assertRaises(AssertionError):
            Camera(metadata).ground([960, 540])


class AnalysisTests(unittest.TestCase):
    def test_counts_threshold_sweep_and_determinism(self):
        frames = []
        for offset in range(0, 25, 4):
            for along in range(-36, 37, 12):
                contains = along == 0 and offset <= 20
                detections = []
                if contains and offset <= 16:
                    detections.append(dict(true=True, confidence=0.8, undilated=True, errorM=0.2))
                if offset == 0 and along == 0:
                    detections.append(dict(true=True, confidence=0.1, undilated=False, errorM=0.5))
                if offset == 0 and along == 12:
                    detections.append(dict(true=False, confidence=0.4, undilated=False, errorM=12))
                frames.append(
                    dict(
                        file=f"f-{offset}-{along}.jpg",
                        offsetM=offset,
                        alongM=along,
                        contains=contains,
                        empty=not contains,
                        detections=detections,
                        roundTripErrorPx=0.001,
                    )
                )
        crops = []
        for f in frames:
            f["personBoxPx"] = [5, 5, 10, 10] if f["contains"] else [-100, -100, -95, -95]
            for index, detection in enumerate(f["detections"]):
                score = 0.8 if f["contains"] else 0.1
                crops.append(
                    dict(
                        file=f["file"],
                        box=[0, 0, 20, 20],
                        detectionIndex=index,
                        kind="detection",
                        label=f["contains"],
                        yes=score,
                        no=1 - score,
                        outside_mass=0.01,
                    )
                )
        crops += [
            dict(
                file="f-0--36.jpg",
                box=[0, 0, 20, 20],
                kind="grass",
                label=False,
                yes=0.1,
                no=0.9,
                outside_mass=0.01,
            )
            for _ in range(len(crops))
        ]
        spec = ["adult-kelvin", "adult-kelvin", "rust", "waiting", "none", 2160]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / spec[0]).mkdir()
            (root / "manifest.json").write_text(
                json.dumps(dict(schemaVersion=1, conditions=[spec], exclusions={}, provenance={}))
            )
            (root / spec[0] / "inference.json").write_text(
                json.dumps(
                    dict(
                        schemaVersion=2,
                        samThreshold=0.05,
                        condition=spec,
                        frames=frames,
                        crops=crops,
                    )
                )
            )
            a, b = analyze(root), analyze(root)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        result = a["results"]
        self.assertEqual(result["samOperating"]["threshold"], 0.4)
        self.assertEqual(result["samOperating"]["falseAlarmsPerFrame"], 0)
        self.assertEqual(result["undilated"], dict(successes=5, trials=6, p=5 / 6))
        self.assertEqual(result["conditions"][spec[0]]["sweepWidthM"], 36)
        self.assertEqual(result["conditions"][spec[0]]["offsets"][-1]["frame"]["p"], None)
        self.assertEqual(result["glimmer"]["auc"], 1)
        self.assertEqual(result["glimmer"]["decision"], "keep")
        self.assertEqual(operating(frames, 0.8)["detection"]["successes"], 0)

    def test_crop_label_uses_extent_not_sam_match(self):
        data = dict(
            schemaVersion=2,
            frames=[
                dict(
                    file="frame.jpg",
                    empty=False,
                    personBoxPx=[100, 100, 120, 140],
                    detections=[dict(true=False, confidence=0.2, errorM=2)],
                )
            ],
            crops=[dict(file="frame.jpg", kind="detection", box=[0, 0, 512, 512], label=False)],
        )
        annotate(data)
        crop = linked_crops(data)[0]
        self.assertTrue(crop["label"])
        self.assertFalse(crop["detection"]["true"])
        data["crops"][0]["box"] = [110, 110, 512, 512]
        self.assertFalse(annotate(data)["crops"][0]["label"])

    def test_absent_class_is_explicit_blocker(self):
        self.assertEqual(glimmer([], 0.05)["status"], "blocked")

    def test_grass_cannot_hide_failed_background_rejection(self):
        crops = [
            dict(
                kind=kind,
                label=label,
                yes=score,
                no=1 - score,
                outside_mass=0,
                detection=dict(confidence=0.9),
            )
            for kind, label, score, count in [
                ("detection", True, 0.8, 10),
                ("detection", False, 0.9, 1),
                ("grass", False, 0.1, 99),
            ]
            for _ in range(count)
        ]
        result = glimmer(crops, 0.95)
        self.assertEqual(result["negativeRejection"]["samBackground"]["rate"], 0)
        self.assertEqual(result["negativeRejection"]["grass"]["rate"], 1)
        self.assertEqual(result["decision"], "delete")
        self.assertIsNone(result["operatingSamBackground"]["rate"])

    def test_malformed_probability_fails(self):
        with self.assertRaises(AssertionError):
            glimmer(
                [
                    dict(kind="detection", label=True, yes=0.8, no=0.3, outside_mass=0),
                    dict(kind="detection", label=False, yes=0.1, no=0.9, outside_mass=0),
                ],
                0.05,
            )


if __name__ == "__main__":
    unittest.main()

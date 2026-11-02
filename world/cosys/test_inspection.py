"""Forward captures use each vehicle's camera and the pose attached to its pixels."""

import asyncio
import copy
import json
import math
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cosysairsim as airsim
import numpy as np
from scipy.spatial.transform import Rotation

from world.cosys.inspection import ForwardCapture, route_look_ahead


class CameraGeometryTests(unittest.TestCase):
    def test_look_ahead_crosses_terrain_waypoints_and_turns(self):
        origin = {"north_m": 0, "east_m": 0}
        points = [
            {"north_m": 0, "east_m": 0},
            {"north_m": 4, "east_m": 0},
            {"north_m": 8, "east_m": 0},
            {"north_m": 10, "east_m": 0},
            {"north_m": 10, "east_m": 30},
        ]
        self.assertEqual(route_look_ahead(points, origin, 20), {"north_m": 10, "east_m": 10})
        self.assertEqual(route_look_ahead(points, origin, 8), points[2])

    def test_short_remaining_route_stops_look_ahead_at_endpoint(self):
        points = [
            {"north_m": 0, "east_m": 0, "height_m": 35},
            {"north_m": 10, "east_m": 0, "height_m": 35},
        ]
        original = copy.deepcopy(points)
        for north in (9.9, 10, 10.1):
            position = {"north_m": north, "east_m": 3}
            target = route_look_ahead(points, position, 20)
            self.assertEqual(target, {"north_m": 10, "east_m": 0})
            self.assertEqual(position, {"north_m": north, "east_m": 3})
        self.assertEqual(points, original)
        self.assertEqual(route_look_ahead([position], position, 20), position)
        with self.assertRaisesRegex(AssertionError, "positive"):
            route_look_ahead([position], position, 0)

    def test_projection_does_not_skip_the_next_leg_at_a_turn(self):
        points = [
            {"north_m": 0, "east_m": 0},
            {"north_m": 10, "east_m": 0},
            {"north_m": 10, "east_m": 30},
        ]
        for north, east in ((10, 0), (12, 2), (11, -3)):
            self.assertEqual(
                route_look_ahead(points, {"north_m": north, "east_m": east}, 20),
                {"north_m": 10, "east_m": 20},
            )
        self.assertEqual(
            route_look_ahead(points, {"north_m": -5, "east_m": 2}, 20),
            {"north_m": 10, "east_m": 10},
        )

    def test_adjacent_duplicate_waypoints_preserve_route_direction(self):
        origin, end = {"north_m": 0, "east_m": 0}, {"north_m": 10, "east_m": 0}
        points = [origin, origin, end, end]
        original = copy.deepcopy(points)
        self.assertEqual(
            route_look_ahead(points, {"north_m": 5, "east_m": 2}, 20),
            end,
        )
        self.assertEqual(points, original)
        self.assertEqual(route_look_ahead([origin, origin], origin, 20), origin)

    def test_vertical_route_aims_at_its_single_horizontal_endpoint(self):
        points = [{"north_m": 10, "east_m": 20, "height_m": height} for height in (30, 40, 50)]
        self.assertEqual(
            route_look_ahead(points, {"north_m": 12, "east_m": 22}, 20),
            {"north_m": 10, "east_m": 20},
        )


class ForwardCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def capture(
        self, name, camera_name="fpv", width=3840, timestamp=150, pixels=None, fov_after=89.9
    ):
        response = SimpleNamespace(
            message="",
            camera_name=camera_name,
            width=width,
            height=2160,
            image_data_uint8=bytes([50, 70, 90]) * width * 2160 if pixels is None else pixels,
            time_stamp=timestamp,
            camera_position=SimpleNamespace(x_val=12, y_val=23, z_val=-30),
            camera_orientation=SimpleNamespace(w_val=1, x_val=0, y_val=0, z_val=0),
        )
        rpc = Mock()
        rpc.simIsPause.return_value = False
        rpc.simGetImages.return_value = [response]
        rpc.simGetCameraInfo.side_effect = [
            SimpleNamespace(fov=89.9),
            SimpleNamespace(fov=fov_after),
        ]
        native = {
            "mode": "mission",
            "vehicles": [
                {"id": f"Drone{i}", "localNedOriginUeCm": {"x": i * 100, "y": 0, "z": 4000}}
                for i in range(1, 5)
            ],
        }
        bracket = [
            {"timestampNs": t, "sampleUnixNs": 1_700_000_000_000_000_000 + t} for t in (100, 200)
        ]
        capture = ForwardCapture(name, self.directory)
        try:
            with (
                patch("world.cosys.inspection.sim.connect", return_value=rpc),
                patch("world.cosys.inspection.sim.native", return_value=native),
                patch("world.cosys.inspection.truth_sample", side_effect=bracket),
            ):
                record, jpeg = capture._capture()
                request = rpc.simGetImages.call_args.args[0][0]
                self.assertEqual(request.camera_name, "fpv")
                self.assertEqual(rpc.simGetImages.call_args.kwargs, {"vehicle_name": name})
                self.assertFalse(request.pixels_as_float)
                self.assertFalse(request.compress)
                return record, jpeg
        finally:
            asyncio.run(capture.close())

    def test_all_four_forward_cameras_keep_distinct_origins_and_frame_poses(self):
        for index in range(1, 5):
            with self.subTest(drone=index):
                record, jpeg = self.capture(f"Drone{index}")
                self.assertEqual(record["drone_id"], f"Drone{index}")
                self.assertEqual(record["camera_id"], "fpv")
                self.assertEqual(
                    record["camera_position_world_m"], {"x": 12 + index, "y": 23, "z": 70}
                )
                self.assertEqual(record["horizontal_fov_deg"], 89.9)
                self.assertEqual(record["camera_view_revision"], 0)
                self.assertEqual(record["timestamp_ns"], 150)
                self.assertTrue(jpeg.startswith(b"\xff\xd8"))

    def test_wrong_camera_low_resolution_and_unbracketed_time_fail(self):
        for arguments, message in (
            ({"camera_name": "front_center"}, "wrong camera"),
            ({"width": 256}, "3840x2160"),
            ({"timestamp": 201}, "capture bracket"),
            ({"fov_after": 53}, "field of view changed"),
        ):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(AssertionError, message):
                self.capture("Drone1", **arguments)

    def test_dark_scene_regions_do_not_invalidate_a_forward_frame(self):
        pixels = np.full((2160, 3840, 3), 80, np.uint8)
        pixels[:540] = 0
        record, jpeg = self.capture("Drone1", pixels=pixels.tobytes())
        self.assertEqual(record["shadow_floor_luminance_p1"], 0)
        self.assertEqual(record["pixel_max"], 80)
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_unusable_black_frame_preserves_pixels_and_pose_before_raising(self):
        with self.assertRaisesRegex(AssertionError, "entirely black frame; evidence:"):
            self.capture("Drone2", pixels=bytes(2160 * 3840 * 3))
        from PIL import Image

        path = self.directory / "150-rejected.png"
        with Image.open(path) as image:
            self.assertEqual(image.size, (3840, 2160))
            self.assertEqual(image.getextrema(), ((0, 0), (0, 0), (0, 0)))
        metadata = json.loads(path.with_suffix(".json").read_text())
        self.assertEqual(metadata["drone_id"], "Drone2")
        self.assertEqual(metadata["timestamp_ns"], 150)
        self.assertEqual(metadata["camera_position_world_m"], {"x": 14, "y": 23, "z": 70})

    def test_aim_preserves_mount_and_uses_world_target_with_rotated_aircraft(self):
        capture = ForwardCapture("Drone2", self.directory)
        body_rotation = Rotation.from_euler("z", 90, degrees=True)
        body_position = np.array([10, 20, -30])
        mount = np.array([0.6, 0, 0.05])
        camera_position = body_position + body_rotation.apply(mount)
        body = airsim.Pose(
            airsim.Vector3r(*body_position), airsim.Quaternionr(*body_rotation.as_quat())
        )
        camera = airsim.Pose(airsim.Vector3r(*camera_position), body.orientation)
        rpc = Mock()
        rpc.simGetVehiclePose.return_value = body
        rpc.simGetCameraInfo.return_value = SimpleNamespace(pose=camera)
        capture.rpc = rpc
        origin = {"x": 1000, "y": 2000, "z": 4000}
        target = {"x": 50, "y": 50, "z": 20}
        try:
            with patch(
                "world.cosys.inspection.sim.native",
                return_value={
                    "mode": "mission",
                    "vehicles": [{"id": "Drone2", "localNedOriginUeCm": origin}],
                },
            ):
                capture._aim(target)
            args = rpc.simSetCameraPose.call_args
            self.assertEqual(args.args[0], "fpv")
            self.assertEqual(args.kwargs, {"vehicle_name": "Drone2"})
            pose = args.args[1]
            np.testing.assert_allclose(
                [getattr(pose.position, key + "_val") for key in "xyz"], mount, atol=1e-12
            )
            relative = Rotation.from_quat(
                [getattr(pose.orientation, key + "_val") for key in "xyzw"]
            )
            aimed = body_rotation.apply(relative.apply([1, 0, 0]))
            wanted = (
                np.array(
                    [(target[key] - origin[key] / 100) * (1 if key != "z" else -1) for key in "xyz"]
                )
                - camera_position
            )
            np.testing.assert_allclose(aimed, wanted / np.linalg.norm(wanted), atol=1e-12)
            capture._reset()
            restored = rpc.simSetCameraPose.call_args.args[1]
            np.testing.assert_allclose(
                [getattr(restored.orientation, key + "_val") for key in "xyzw"], [0, 0, 0, 1]
            )
            count = rpc.simSetCameraPose.call_count
            capture._reset()
            self.assertEqual(rpc.simSetCameraPose.call_count, count)
        finally:
            asyncio.run(capture.close())

    def test_camera_aim_failure_is_not_retried(self):
        capture = ForwardCapture("Drone1", self.directory)
        capture.rpc = Mock()
        capture.rpc.simGetVehiclePose.side_effect = TimeoutError("camera pose timed out")
        try:
            with patch(
                "world.cosys.inspection.sim.native",
                return_value={
                    "mode": "mission",
                    "vehicles": [
                        {"id": "Drone1", "localNedOriginUeCm": {"x": 0, "y": 0, "z": 4000}}
                    ],
                },
            ):
                with self.assertRaisesRegex(TimeoutError, "camera pose timed out"):
                    capture._aim({"x": 10, "y": 10, "z": 0})
            capture.rpc.simGetVehiclePose.assert_called_once()
            capture.rpc.simSetCameraPose.assert_not_called()
        finally:
            asyncio.run(capture.close())

    def camera(self):
        capture = ForwardCapture("Drone1", self.directory)
        rpc = Mock()
        pose = airsim.Pose(airsim.Vector3r(0, 0, -30), airsim.Quaternionr(0, 0, 0, 1))
        rpc.simGetVehiclePose.return_value = pose
        info = SimpleNamespace(pose=pose, fov=89.90362548828125)
        rpc.simGetCameraInfo.return_value = info
        rpc.simSetCameraFov.side_effect = lambda _, fov, **kwargs: setattr(info, "fov", fov)
        capture.rpc = rpc
        self.addCleanup(lambda: asyncio.run(capture.close()))
        native = patch(
            "world.cosys.inspection.sim.native",
            return_value={
                "mode": "mission",
                "vehicles": [{"id": "Drone1", "localNedOriginUeCm": {"x": 0, "y": 0, "z": 0}}],
            },
        )
        native.start()
        self.addCleanup(native.stop)
        return capture, rpc, info

    def test_zoom_is_relative_to_original_lens_and_reset_restores_it(self):
        capture, rpc, info = self.camera()
        target = {"x": 20, "y": 0, "z": 0}
        base = info.fov
        for revision, zoom in enumerate((1, 2, 2, 1, 2), 1):
            capture.begin_follow()
            result = asyncio.run(capture.view(target, zoom))
            self.assertFalse(capture.visual_controlled)
            self.assertEqual(result["revision"], revision)
            self.assertEqual(result["target"], target)
            self.assertEqual(result["zoom"], zoom)
            self.assertAlmostEqual(
                math.tan(math.radians(base) / 2)
                / math.tan(math.radians(result["horizontal_fov_deg"]) / 2),
                zoom,
            )
        selected = info.fov
        capture._aim(target)
        self.assertEqual(info.fov, selected)
        self.assertEqual(capture.view_revision, 5)
        capture._reset()
        self.assertAlmostEqual(info.fov, base)
        self.assertIsNone(capture.original_pose)
        self.assertIsNone(capture.original_fov)
        self.assertIsNone(capture.target)
        rpc.simSetCameraFov.assert_called_with("fpv", base, vehicle_name="Drone1")

    def test_capture_reaims_latest_world_target_after_aircraft_attitude_changes(self):
        capture, rpc, info = self.camera()
        rpc.simIsPause.return_value = False
        body = rpc.simGetVehiclePose.return_value

        def moved_camera(_, relative, **kwargs):
            body_rotation = Rotation.from_quat(
                [getattr(body.orientation, key + "_val") for key in "xyzw"]
            )
            relative_rotation = Rotation.from_quat(
                [getattr(relative.orientation, key + "_val") for key in "xyzw"]
            )
            info.pose = airsim.Pose(
                body.position, airsim.Quaternionr(*(body_rotation * relative_rotation).as_quat())
            )

        rpc.simSetCameraPose.side_effect = moved_camera
        wanted = None

        def image(*args, **kwargs):
            orientation = info.pose.orientation
            rotation = Rotation.from_quat([getattr(orientation, key + "_val") for key in "xyzw"])
            np.testing.assert_allclose(rotation.apply([1, 0, 0]), wanted, atol=1e-12)
            return [
                SimpleNamespace(
                    message="",
                    camera_name="fpv",
                    width=3840,
                    height=2160,
                    image_data_uint8=bytes([50, 70, 90]) * 3840 * 2160,
                    time_stamp=150,
                    camera_position=info.pose.position,
                    camera_orientation=orientation,
                )
            ]

        rpc.simGetImages.side_effect = image
        for revision, target in enumerate(
            ({"x": 20, "y": 0, "z": 0}, {"x": 40, "y": 10, "z": 0}), 1
        ):
            with self.subTest(target=target):
                capture._view(target)
                wanted = np.array([target["x"], target["y"], 30.0])
                wanted /= np.linalg.norm(wanted)
                body.orientation = airsim.Quaternionr(
                    *Rotation.from_euler("ZYX", [10, -20, 7], degrees=True).as_quat()
                )
                moved_camera("fpv", rpc.simSetCameraPose.call_args.args[1])
                bracket = [
                    {"timestampNs": t, "sampleUnixNs": 1_700_000_000_000_000_000 + t}
                    for t in (100, 200)
                ]
                with patch("world.cosys.inspection.truth_sample", side_effect=bracket):
                    metadata, _ = capture._capture()
                self.assertEqual(metadata["camera_view_revision"], revision)
                self.assertEqual(capture.target, target)

    def test_capture_stops_if_world_target_stabilization_fails(self):
        capture, rpc, _ = self.camera()
        capture._aim({"x": 20, "y": 0, "z": 0})
        rpc.simGetVehiclePose.side_effect = TimeoutError("camera attitude unavailable")
        with self.assertRaisesRegex(TimeoutError, "camera attitude unavailable"):
            capture._capture()
        rpc.simGetImages.assert_not_called()

    def test_invalid_optics_do_not_publish_a_revision(self):
        capture, rpc, info = self.camera()
        info.fov = float("nan")
        with self.assertRaisesRegex(AssertionError, "field of view is invalid"):
            asyncio.run(capture.view({"x": 20, "y": 0, "z": 0}))
        rpc.simSetCameraFov.assert_not_called()
        self.assertEqual(capture.view_revision, 0)

    def test_invalid_zoom_has_no_camera_or_tracking_side_effect(self):
        capture, rpc, _ = self.camera()
        generation = capture.begin_follow()
        for zoom in (0, 1.5, 3, 4, float("nan"), True, "2"):
            with self.subTest(zoom=zoom), self.assertRaisesRegex(AssertionError, "1x or 2x"):
                asyncio.run(capture.view({"x": 20, "y": 0, "z": 0}, zoom))
        self.assertEqual(rpc.mock_calls, [])
        self.assertEqual(capture.view_revision, 0)
        self.assertEqual(capture.follow_generation, generation)
        self.assertTrue(capture.visual_controlled)

    def test_unapplied_zoom_fails_without_publishing_a_revision(self):
        capture, rpc, _ = self.camera()
        rpc.simSetCameraFov.side_effect = None
        with self.assertRaisesRegex(AssertionError, "zoom readback differs"):
            asyncio.run(capture.view({"x": 20, "y": 0, "z": 0}, 2))
        self.assertEqual(capture.view_revision, 0)

    def test_capture_waits_for_an_in_flight_camera_change(self):
        capture, rpc, info = self.camera()
        entered, finish = threading.Event(), threading.Event()
        rpc.simIsPause.return_value = False
        response = SimpleNamespace(
            message="",
            camera_name="fpv",
            width=3840,
            height=2160,
            image_data_uint8=bytes([50, 70, 90]) * 3840 * 2160,
            time_stamp=150,
            camera_position=airsim.Vector3r(0, 0, -30),
            camera_orientation=airsim.Quaternionr(0, 0, 0, 1),
        )
        rpc.simGetImages.return_value = [response]

        def change(_, pose, **kwargs):
            entered.set()
            assert finish.wait(2), "Test did not release camera change"
            info.pose = pose

        rpc.simSetCameraPose.side_effect = change

        async def exercise():
            view = asyncio.create_task(capture.view({"x": 20, "y": 0, "z": 0}, 2))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            frame = asyncio.create_task(capture.capture())
            await asyncio.sleep(0)
            rpc.simGetImages.assert_not_called()
            finish.set()
            changed, (metadata, _) = await asyncio.gather(view, frame)
            self.assertEqual(metadata["horizontal_fov_deg"], changed["horizontal_fov_deg"])
            self.assertEqual(metadata["camera_view_revision"], changed["revision"])

        bracket = [
            {"timestampNs": t, "sampleUnixNs": 1_700_000_000_000_000_000 + t} for t in (100, 200)
        ]
        try:
            with patch("world.cosys.inspection.truth_sample", side_effect=bracket):
                asyncio.run(exercise())
        finally:
            finish.set()


if __name__ == "__main__":
    unittest.main()

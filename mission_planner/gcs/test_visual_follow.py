"""Held-camera guidance survives only the inspection budget, never a flight release."""

import asyncio
import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from mission_planner.gcs import test_live_tracker
from mission_planner.gcs.visual_follow import VisualFollow


class VisualFollowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.vehicle = {
            "id": "Drone1",
            "mode": "BRAKE",
            "armed": True,
            "landed_state": 2,
            "link_lost": False,
            "ekf_healthy": True,
            "camera_available": True,
            "received_at_ms": time.time() * 1000,
        }
        self.row = {
            "id": "inspection",
            "drone_id": "Drone1",
            "plan_version": 1,
            "status": "candidate",
            "camera_changing": False,
            "camera_view": {"revision": 3},
            "rescue": {
                "review_id": "review",
                "phase": "held",
                "selected_track_id": 7,
                "visual_hold": False,
            },
        }
        self.camera = Mock()
        self.camera.begin_follow.return_value = 1
        self.camera.follow = AsyncMock(return_value={"status": "moved"})
        self.camera.follow_diagnostic = None
        self.camera.end_follow = AsyncMock()
        self.mission = SimpleNamespace(
            vehicles=lambda: [self.vehicle],
            plan_active={"plan": {"version": 1}},
            fleet=Mock(),
            directory=Path(directory.name),
            launch=asyncio.create_task,
        )
        self.inspections = SimpleNamespace(
            rows={"inspection": self.row}, cameras={"Drone1": self.camera}, mission=self.mission
        )
        self.control = VisualFollow(self.inspections)
        self.socket = object()
        self.message = {
            "type": "camera_follow",
            "inspection_id": "inspection",
            "review_id": "review",
            "track_id": 7,
            "camera_revision": 3,
            "sequence": 1,
            "frame_age_ms": 30,
            "frame_timestamp_ns": 1,
            "box": [670, 200, 740, 300],
            "observed_q": [1, 0, 0, 0],
        }

    async def asyncTearDown(self):
        await self.control.close()

    async def test_budget_expiry_keeps_the_camera_owner_without_changing_the_plan(self):
        before = copy.deepcopy(self.mission.plan_active)
        self.assertEqual((await self.control.update(self.message, self.socket))["status"], "moved")
        self.row["status"] = self.row["rescue"]["phase"] = "failed"
        self.row["rescue"]["visual_hold"] = True
        self.message["sequence"] = 2
        self.assertEqual((await self.control.update(self.message, self.socket))["status"], "moved")
        self.camera.begin_follow.assert_called_once()
        self.assertEqual(self.camera.follow.await_count, 2)
        self.assertEqual(self.mission.plan_active, before)
        self.assertEqual(self.mission.fleet.mock_calls, [])

    async def test_other_failures_return_departure_and_stale_telemetry_stop_steering(self):
        for change in ("failed", "return", "landed", "link", "stale", "view", "identity", "plan"):
            self.setUp()
            await self.control.update(self.message, self.socket)
            if change == "failed":
                self.row["status"] = "failed"
            if change == "return":
                self.vehicle["mode"] = "RTL"
            if change == "landed":
                self.vehicle["landed_state"] = 1
            if change == "link":
                self.vehicle["link_lost"] = True
            if change == "stale":
                self.vehicle["received_at_ms"] -= 3000
            if change == "view":
                self.row["camera_view"]["revision"] = 4
            if change == "identity":
                self.row["rescue"]["selected_track_id"] = 8
            if change == "plan":
                self.mission.plan_active["plan"]["version"] = 2
            self.message["sequence"] = 2
            with self.subTest(change=change):
                self.assertEqual(
                    (await self.control.update(self.message, self.socket))["status"], "stopped"
                )
                self.camera.follow.assert_awaited_once()
                self.assertEqual(self.control.owners, {})

    async def test_old_frames_and_invalid_boxes_never_move_the_camera(self):
        self.message["frame_age_ms"] = 1001
        self.assertEqual((await self.control.update(self.message, self.socket))["status"], "stale")
        self.camera.follow.assert_not_awaited()
        for box in ([1, 1, 0, 100], [1, 1, 1, 100], [0, 0, float("nan"), 100]):
            with self.subTest(box=box), self.assertRaises(AssertionError):
                await self.control.update({**self.message, "box": box}, self.socket)
        self.camera.follow.assert_not_awaited()

    async def test_loss_disconnect_and_wrong_viewer_do_not_steer(self):
        await self.control.update(self.message, self.socket)
        self.message["sequence"] = 2
        with self.assertRaisesRegex(AssertionError, "Another tracking session"):
            await self.control.update(self.message, object())
        await self.control.update({**self.message, "box": None}, object())
        self.assertIn("Drone1", self.control.owners)
        await self.control.update({**self.message, "box": None}, self.socket)
        self.assertEqual(self.control.owners, {})
        await self.control.update(self.message, self.socket)
        self.control.cancel("Drone1")
        self.assertEqual(self.control.owners, {})

    async def test_partially_visible_target_is_followed_without_an_assertion(self):
        self.message["box"] = [930, 150, 980, 220]
        result = await self.control.update(self.message, self.socket)
        self.assertEqual(result["status"], "moved")
        self.assertEqual(self.camera.follow.await_args.args[0], self.message["box"])

    async def test_guided_following_keeps_camera_control_but_unrelated_guided_flight_does_not(self):
        self.vehicle["mode"] = "GUIDED"
        self.row["rescue"]["phase"] = "reviewing"
        self.assertEqual((await self.control.update(self.message, self.socket))["status"], "moved")
        self.row["rescue"]["phase"] = "tracking"
        self.message["sequence"] = 2
        self.assertEqual((await self.control.update(self.message, self.socket))["status"], "moved")
        self.row["rescue"]["phase"] = "searching"
        self.message["sequence"] = 3
        self.assertEqual(
            (await self.control.update(self.message, self.socket))["status"], "stopped"
        )

    async def native_seed(self, jpeg):
        self.inspections.directory = self.mission.directory
        self.inspections.camera_locks = {"Drone1": asyncio.Lock()}
        self.camera.capture_inflight = 1
        metadata = {
            "frame_id": "seed",
            "camera_view_revision": 3,
            "timestamp_ns": 1,
            "captured_at_ms": time.time() * 1000,
            "camera_pose_local_ned": {"orientation": dict(zip("wxyz", [1, 0, 0, 0]))},
        }
        (self.inspections.directory / "seed-frame.jpg").write_bytes(jpeg)
        self.camera.video_sample = (metadata, jpeg)
        await self.control.observe(
            self.row, metadata, [{"track_id": 7, "box_xyxy": [1600, 900, 1700, 1060]}]
        )
        return metadata

    async def until(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            task = self.control.sessions["Drone1"]["task"]
            if task.done():
                task.result()
            self.assertLess(time.monotonic(), deadline, "Native control did not advance")
            await asyncio.sleep(0.01)

    async def test_native_tracking_uses_image_pixels_and_survives_sam_budget_without_browser(self):
        fixture = test_live_tracker.NativeTrackerTests()
        fixture.setUp()
        metadata = await self.native_seed(fixture.jpeg(1600, 900))
        await self.until(lambda: self.camera.follow.await_count >= 1)
        first = self.camera.follow.await_args.args[0]
        self.row["status"] = self.row["rescue"]["phase"] = "failed"
        self.row["rescue"]["visual_hold"] = True
        self.camera.video_sample = (
            {**metadata, "timestamp_ns": 2, "captured_at_ms": time.time() * 1000},
            fixture.jpeg(1640, 920),
        )
        await self.until(lambda: self.row["camera_tracking"]["box"][0] > first[0] + 3)
        self.assertEqual(self.row["camera_tracking"]["source"], "onboard_csrt")
        self.assertEqual(self.mission.fleet.mock_calls, [])
        await self.control.close()
        self.assertEqual(self.control.sessions, {})
        self.camera.end_follow.assert_awaited()
        self.assertIsNone(self.row["camera_tracking"])

    async def test_native_failure_is_reported_and_not_restarted_by_subsequent_sam_frames(self):
        metadata = await self.native_seed(b"invalid JPEG")
        task = self.control.sessions["Drone1"]["task"]
        with self.assertRaisesRegex(RuntimeError, "native main-camera tracking failed"):
            await task
        self.assertEqual(self.row["camera_tracking"]["status"], "failed")
        self.assertIn("original 4K camera image", self.row["camera_tracking"]["error"])
        await self.control.observe(
            self.row, metadata, [{"track_id": 7, "box_xyxy": [1600, 900, 1700, 1060]}]
        )
        self.assertIs(self.control.sessions["Drone1"]["task"], task)
        self.camera.follow.assert_not_awaited()

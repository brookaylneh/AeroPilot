"""Prompted observations preserve evidence and never outlive their flight context."""

import asyncio
import copy
import io
import json
import math
import tempfile
import threading
import time
import unittest
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from mission_planner.agents.test_vision import mask_box
from mission_planner.gcs.inspection import (
    AIM_STEP_M,
    Inspections,
    aim_toward,
    flight_identity,
    sampling_next,
    sampling_start,
)
from world.cosys.terrain import NORTH_MAX_M, terrain_height


@lru_cache(maxsize=1)
def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (3840, 2160), (80, 80, 80)).save(output, format="JPEG")
    return output.getvalue()


def response(prompt="person", positive=True, observations=2):
    return {
        "width": 3840,
        "height": 2160,
        "detections": [
            {
                "track_id": 1,
                "class_name": prompt,
                "box_xyxy": [960, 900, 1800, 1800],
                "confidence": 0.8,
                "observations": observations,
                "mask": mask_box([960, 900, 1800, 1800]),
            }
        ]
        if positive
        else [],
    }


class LockGeometryTests(unittest.TestCase):
    def test_aim_moves_at_most_one_step_toward_the_target(self):
        current = {"x": 0.0, "y": 0.0, "z": 10.0}
        moved = aim_toward(current, {"x": 30.0, "y": 40.0, "z": 10.0})
        self.assertAlmostEqual(math.dist(moved.values(), current.values()), AIM_STEP_M)
        self.assertAlmostEqual(moved["y"] / moved["x"], 4 / 3)
        near = {"x": 1.0, "y": 1.0, "z": 10.5}
        self.assertEqual(aim_toward(current, near), near)
        self.assertEqual(current, {"x": 0.0, "y": 0.0, "z": 10.0})


class SamplingTests(unittest.TestCase):
    def test_scheduling_skips_expired_slots_and_keeps_the_drone_phase(self):
        for name in ("Drone1", "Drone2", "Drone3", "Drone4"):
            slot = sampling_start(name, 100)
            for duration, missed in ((0.1, 0), (0.3, 1), (1.05, 5)):
                following, count = sampling_next(slot, slot + duration)
                self.assertEqual(count, missed)
                self.assertGreater(following, slot + duration)
                self.assertAlmostEqual(following - slot, (missed + 1) / 5)

    def test_all_four_phases_are_distinct_and_no_capture_is_scheduled_in_the_past(self):
        starts = [sampling_start(f"Drone{i}", 100.001) for i in range(1, 5)]
        self.assertEqual(len(set(starts)), 4)
        for i, slot in enumerate(starts):
            self.assertGreaterEqual(slot, 100.001)
            self.assertLess(slot, 100.201)
            self.assertAlmostEqual(slot * 5 - i / 4, round(slot * 5 - i / 4))

    def test_invalid_schedule_is_rejected(self):
        for slot, completed in ((1, 0.9), (1, math.inf), (math.nan, 1)):
            with self.subTest(slot=slot, completed=completed), self.assertRaises(AssertionError):
                sampling_next(slot, completed)
        for name, now in (("Drone5", 0), ("Drone1", -1), ("Drone1", math.inf)):
            with self.subTest(name=name, now=now), self.assertRaises(AssertionError):
                sampling_start(name, now)


class InspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_registry_write_preserves_published_assets(self):
        assets = self.inspections
        assets.directory.mkdir()
        self.inspections._register(("previous-frame.jpg",))
        registry = assets.directory / "assets.json"
        published = registry.read_bytes()
        with (
            patch.object(Path, "replace", side_effect=OSError("Registry is not writable")),
            self.assertRaisesRegex(OSError, "Registry is not writable"),
        ):
            self.inspections._register(("new-frame.jpg",))
        self.assertEqual(assets.assets, {"previous-frame.jpg"})
        self.assertEqual(registry.read_bytes(), published)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.states = {
            f"Drone{i}": {
                "id": f"Drone{i}",
                "armed": True,
                "landed_state": 2,
                "camera_available": True,
                "link_lost": False,
                "ekf_healthy": True,
                "mode": "AUTO",
                "ground_speed_mps": 0,
                "climb_rate_mps": 0,
                "heading_deg": 0,
                "north_m": 0,
                "east_m": 0,
            }
            for i in range(1, 5)
        }
        self.events = []

        def event(kind, text, **fields):
            row = {"id": str(len(self.events)), "kind": kind, "text": text, **fields}
            self.events.append(row)
            return row

        self.mission = SimpleNamespace(
            id="test",
            case=None,
            directory=self.directory,
            fleet=SimpleNamespace(face_heading=AsyncMock(), execute=AsyncMock()),
            events=self.events,
            event=Mock(side_effect=event),
            launch=asyncio.create_task,
            plan_active={
                "plan": {
                    "version": 1,
                    "drones": [
                        {"drone_id": name, "steps": [{"kind": "hover", "seconds": None}]}
                        for name in self.states
                    ],
                }
            },
        )
        self.mission.vehicles = lambda: [
            {**v, "received_at_ms": time.time() * 1000} for v in self.states.values()
        ]
        self.mission.planner_state = lambda: {
            "progress": {
                name: {"version": self.mission.plan_active["plan"]["version"], "current_step": 0}
                for name in self.states
            }
        }
        self.captures = {}

        def capture(name, directory):
            self.assertTrue(directory.is_dir())
            count = 0
            revision, horizontal_fov_deg = 0, 90

            async def view(target, zoom=1):
                nonlocal revision, horizontal_fov_deg
                revision += 1
                horizontal_fov_deg = math.degrees(2 * math.atan(1 / zoom))
                return {
                    "revision": revision,
                    "zoom": zoom,
                    "target": target,
                    "horizontal_fov_deg": horizontal_fov_deg,
                }

            async def acquire():
                nonlocal count
                count += 1
                return {
                    "drone_id": name,
                    "camera_id": "fpv",
                    "horizontal_fov_deg": horizontal_fov_deg,
                    "camera_view_revision": revision,
                    "timestamp_ns": count,
                    "captured_at_ms": time.time() * 1000,
                    "width": 3840,
                    "height": 2160,
                    "camera_position_world_m": {
                        "x": self.states[name]["north_m"],
                        "y": self.states[name]["east_m"],
                        "z": 30,
                    },
                    "camera_pose_local_ned": {
                        "orientation": {
                            "w": math.cos(math.radians(self.states[name]["heading_deg"]) / 2),
                            "x": 0,
                            "y": 0,
                            "z": math.sin(math.radians(self.states[name]["heading_deg"]) / 2),
                        }
                    },
                }, image_bytes()

            value = SimpleNamespace(
                capture=AsyncMock(side_effect=acquire),
                view=AsyncMock(side_effect=view),
                close=AsyncMock(),
            )
            self.captures[name] = value
            return value

        self.detect = Mock(side_effect=lambda *args: response())
        self.clients = []

        def vision(http, name, prompt, threshold):
            client = SimpleNamespace(http=http, status="new")
            client.open = Mock(side_effect=lambda: setattr(client, "status", "open"))
            client.advance = Mock(side_effect=lambda *args: self.detect(*args))
            client.close = Mock(side_effect=lambda: setattr(client, "status", "closed"))
            self.clients.append(client)
            return client

        for name, replacement in (
            ("ForwardCapture", capture),
            ("vision_http", Mock(return_value=Mock())),
            ("VisionTrack", vision),
        ):
            patcher = patch("mission_planner.gcs.inspection." + name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.inspections = Inspections(self.mission)
        self.inspections.visual.observe = AsyncMock()

    async def asyncTearDown(self):
        await self.inspections.close()

    async def start(self, name="Drone1", duration=5):
        row = await self.inspections.start(name, "person", 0.3, duration)
        return row, self.inspections.tasks[name]

    async def test_camera_view_persists_across_inspections_without_commanding_flight(self):
        view = await self.inspections.set_camera_view("Drone1", 30, 10, 2, zoom=2)
        camera = self.captures["Drone1"]
        camera.view.assert_awaited_once_with({"x": 30, "y": 10, "z": terrain_height(30, 10) + 2}, 2)
        self.assertEqual(view["zoom"], 2)
        self.assertAlmostEqual(view["horizontal_fov_deg"], math.degrees(2 * math.atan(0.5)))
        self.detect.assert_not_called()
        camera.capture.assert_not_awaited()
        first, task = await self.start()
        await task
        camera.close.assert_not_awaited()
        self.assertEqual(first["camera_view"], self.inspections.camera_views["Drone1"])
        await self.inspections.set_camera_view("Drone1", 40, 15, 0)
        second, task = await self.start()
        await task
        self.assertIs(self.captures["Drone1"], camera)
        self.assertEqual(camera.directory, self.directory / "inspections" / second["id"])
        latest = self.inspections.rows[second["id"]]["latest"]
        self.assertEqual(latest["camera_view_revision"], 2)
        self.assertAlmostEqual(latest["horizontal_fov_deg"], 90)
        camera.close.assert_not_awaited()
        self.mission.fleet.execute.assert_not_awaited()
        self.mission.fleet.face_heading.assert_not_awaited()
        await self.inspections.close()
        camera.close.assert_awaited_once()
        self.assertEqual(self.inspections.cameras, {})
        self.assertEqual(self.inspections.camera_views, {})

    async def test_camera_view_rejects_unlaunched_flight_and_changed_plan(self):
        self.states["Drone1"]["armed"] = False
        with self.assertRaisesRegex(AssertionError, "airborne"):
            await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        self.assertEqual(self.captures, {})
        self.states["Drone1"]["armed"] = True
        await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        camera = self.captures["Drone1"]
        set_view = camera.view.side_effect

        async def changed(target, zoom):
            result = await set_view(target, zoom)
            self.mission.plan_active["plan"]["version"] = 2
            return result

        camera.view.side_effect = changed
        with self.assertRaisesRegex(AssertionError, "flight changed"):
            await self.inspections.set_camera_view("Drone1", 40, 0, 0)
        self.assertIsNone(self.inspections.camera_views["Drone1"])
        self.assertEqual(camera.view.await_count, 2)
        self.mission.fleet.execute.assert_not_awaited()

    async def test_failed_camera_readback_invalidates_old_view_until_explicit_new_command(self):
        await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        row, task = await self.start()
        await task
        candidate = self.inspections.rows[row["id"]]
        camera = self.captures["Drone1"]
        original = camera.view.side_effect
        entered, finish = asyncio.Event(), asyncio.Event()

        async def failed_view(*args):
            entered.set()
            await finish.wait()
            raise TimeoutError("Camera readback timed out")

        camera.view.side_effect = failed_view
        changing = asyncio.create_task(self.inspections.set_camera_view("Drone1", 30, 0, 0))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            self.assertTrue(candidate["camera_changing"])
            self.assertIsNone(self.inspections.camera_views["Drone1"])
            with self.assertRaisesRegex(AssertionError, "Camera view is unverified"):
                await self.start()
        finally:
            finish.set()
            with self.assertRaisesRegex(TimeoutError, "Camera readback timed out"):
                await changing
        self.assertTrue(candidate["camera_changing"])
        self.assertIsNone(self.inspections.camera_views["Drone1"])
        with self.assertRaisesRegex(AssertionError, "Camera view is unverified"):
            await self.start()
        self.assertEqual(camera.view.await_count, 2)
        self.assertEqual(self.detect.call_count, 1)
        camera.view.side_effect = original
        await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        self.assertFalse(candidate["camera_changing"])
        self.assertEqual(self.inspections.camera_views["Drone1"]["horizontal_fov_deg"], 90)
        _, task = await self.start()
        await task
        self.assertIs(self.inspections.cameras["Drone1"], camera)
        self.mission.fleet.execute.assert_not_awaited()

    async def test_running_inspection_owns_camera_until_its_request_finishes(self):
        entered, finish = threading.Event(), threading.Event()

        def detect(*args):
            entered.set()
            assert finish.wait(2), "Test did not release its delayed detector"
            return response()

        self.detect.side_effect = detect
        _, task = await self.start()
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        camera = self.captures["Drone1"]
        try:
            with self.assertRaisesRegex(AssertionError, "Stop the current inspection"):
                await self.inspections.set_camera_view("Drone1", 30, 0, 0)
            self.inspections.stop("Drone1", "Change inspection view")
            with self.assertRaisesRegex(AssertionError, "still finishing"):
                await self.inspections.set_camera_view("Drone1", 30, 0, 0)
            camera.view.assert_not_awaited()
        finally:
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        camera.view.assert_awaited_once()

    async def test_wonder_publishes_only_the_exact_candidate_once_as_immutable_evidence(self):
        row, task = await self.start()
        await task
        candidate = self.inspections.rows[row["id"]]
        identity = candidate["latest"]["frame_id"]
        self.assertFalse(any(event["kind"] == "evidence" for event in self.events))
        with self.assertRaisesRegex(AssertionError, "no longer available"):
            self.inspections.publish_evidence(
                row["id"], "other-frame", 0, "observed", "Person spotted", "Person near the lake"
            )
        with self.assertRaisesRegex(AssertionError, "observation summary"):
            self.inspections.publish_evidence(
                row["id"], identity, 0, "observed", "Person spotted", " "
            )
        for index in (-1, 1, True, 0.5):
            with (
                self.subTest(index=index),
                self.assertRaisesRegex(AssertionError, "Selected candidate"),
            ):
                self.inspections.publish_evidence(
                    row["id"], identity, index, "observed", "Person spotted", "Person near the lake"
                )
        with self.assertRaisesRegex(AssertionError, "confirmed identity"):
            self.inspections.publish_evidence(
                row["id"], identity, 0, "confirmed", "Person spotted", "Person near the lake"
            )
        with self.assertRaisesRegex(AssertionError, "headline"):
            self.inspections.publish_evidence(
                row["id"], identity, 0, "observed", " ", "Person near the lake"
            )
        self.assertFalse(any(event["kind"] == "evidence" for event in self.events))
        result = self.inspections.publish_evidence(
            row["id"], identity, 0, "observed", "Person spotted", "Person near the lake"
        )
        published = self.events[-1]
        self.assertEqual(result["event_id"], published["id"])
        self.assertEqual(result["inspection_id"], row["id"])
        self.assertEqual(result["frame_id"], identity)
        self.assertEqual((published["kind"], published["source"]), ("evidence", "agent"))
        self.assertEqual(published["text"], "Person near the lake")
        self.assertEqual(
            published["arguments"],
            {
                "inspection_id": row["id"],
                "frame_id": identity,
                "candidate_index": 0,
                "assessment": "observed",
                "headline": "Person spotted",
                "summary": "Person near the lake",
            },
        )
        self.assertEqual(published["result"], candidate)
        with self.assertRaisesRegex(AssertionError, "already been published"):
            self.inspections.publish_evidence(
                row["id"], identity, 0, "matching", "Clothing matches", "Another summary"
            )
        candidate["latest"]["detections"].clear()
        self.assertEqual(len(published["result"]["latest"]["detections"]), 1)

    async def test_four_drone_candidates_preserve_exact_evidence_and_stop_sampling(self):
        self.states["Drone1"].update(ground_speed_mps=8, climb_rate_mps=1.5)
        self.states["Drone2"].update(landed_state=3, climb_rate_mps=2)
        self.states["Drone3"].update(landed_state=4, climb_rate_mps=-1, mode="LAND")
        self.states["Drone4"].update(ground_speed_mps=0.3, climb_rate_mps=0.21)
        jobs = [await self.start(name) for name in self.states]
        await asyncio.gather(*(task for _, task in jobs))
        self.assertEqual(self.detect.call_count, 4)
        for row, _ in jobs:
            actual = self.inspections.rows[row["id"]]
            self.assertEqual((actual["status"], actual["frames"]), ("candidate", 1))
            evidence = self.inspections.evidence(row["id"])
            self.assertEqual(evidence["metadata"]["drone_id"], row["drone_id"])
            self.assertEqual(evidence["images"][0], image_bytes())
            self.assertEqual(len(evidence["images"]), 1)
            self.assertEqual(row["status"], "running")
            self.assertNotIn("heading_deg", row)
        snapshot = self.inspections.snapshot()
        snapshot[0]["status"] = "broken"
        self.assertEqual(self.inspections.snapshot()[0]["status"], "candidate")
        assets = json.loads((self.directory / "evidence/assets.json").read_text())
        self.assertEqual(len(assets), 8)
        masks = [name for name in assets if name.endswith("-mask.png")]
        self.assertEqual(len(masks), 4)
        for name in masks:
            with Image.open(self.directory / "evidence" / name) as mask:
                self.assertEqual((mask.format, mask.mode, mask.size), ("PNG", "L", (840, 900)))
        self.mission.fleet.face_heading.assert_not_awaited()
        self.mission.fleet.execute.assert_not_awaited()

    async def test_saved_source_and_metadata_cannot_be_replaced(self):
        row, task = await self.start()
        await task
        evidence = self.inspections.evidence(row["id"])
        metadata, jpeg = evidence["metadata"], evidence["images"][0]
        with self.assertRaises(FileExistsError):
            await self.inspections._save_frame({**metadata, "captured_at_ms": 0}, jpeg)
        self.assertEqual(self.inspections.evidence(row["id"]), evidence)
        path = self.inspections.directory / (metadata["frame_id"] + "-frame.jpg")
        path.write_bytes(jpeg + b"modified")
        with self.assertRaisesRegex(AssertionError, "changed on disk"):
            self.inspections.evidence(row["id"])

    async def test_a_single_detection_does_not_trigger_a_confirmed_candidate(self):
        self.detect.side_effect = [response(observations=1), response(observations=2)]
        row, task = await self.start()
        await task
        actual = self.inspections.rows[row["id"]]
        self.assertEqual((actual["status"], actual["frames"]), ("candidate", 2))
        self.assertEqual(len(self.clients), 1)
        self.clients[0].open.assert_called_once()
        self.clients[0].close.assert_called_once()

    async def test_negative_deadline_publishes_final_frame_without_claiming_absence(self):
        self.detect.side_effect = lambda *args: response(positive=False)
        row, task = await self.start(duration=1)
        await task
        actual = self.inspections.rows[row["id"]]
        self.assertEqual((actual["status"], actual["frames"]), ("complete", 5))
        self.assertEqual(actual["latest"]["detections"], [])
        with self.assertRaisesRegex(AssertionError, "no candidate"):
            self.inspections.publish_evidence(
                row["id"],
                actual["latest"]["frame_id"],
                0,
                "observed",
                "Person",
                "Unsubstantiated report",
            )

    async def test_image_evidence_survives_ground_ray_outside_terrain_domain(self):
        for name, north, error in (
            ("Drone1", NORTH_MAX_M + 1, "Tracking camera is outside the terrain domain"),
            ("Drone2", NORTH_MAX_M, "Candidate ray leaves the terrain domain"),
        ):
            with self.subTest(name=name):
                self.states[name]["north_m"] = north
                row, task = await self.start(name)
                await task
                candidate = self.inspections.rows[row["id"]]
                self.assertEqual(candidate["status"], "candidate")
                (detection,) = candidate["latest"]["detections"]
                self.assertIsNone(detection["ground"])
                self.assertEqual(detection["location_error"], error)
                self.assertEqual(self.inspections.evidence(row["id"])["images"][0], image_bytes())

    async def test_negative_observations_are_sampled_at_five_hz(self):
        self.detect.side_effect = lambda *args: response(positive=False)
        row, task = await self.start(duration=2)
        await task
        records = [
            json.loads(p.read_text())
            for p in (self.directory / "inspections" / row["id"]).glob("*.json")
        ]
        times = sorted(r["captured_at_ms"] for r in records)
        self.assertEqual(len(times), 10)
        self.assertTrue(all(b - a >= 180 for a, b in zip(times, times[1:])))
        self.assertEqual(self.inspections.rows[row["id"]]["frames"], 10)

    async def test_preparation_error_leaves_terminal_failed_state(self):
        with patch(
            "mission_planner.gcs.inspection.vision_http", side_effect=ValueError("Endpoint missing")
        ):
            row, task = await self.start()
            with self.assertRaisesRegex(RuntimeError, "Endpoint missing"):
                await task
        actual = self.inspections.rows[row["id"]]
        self.assertEqual(
            (actual["status"], actual["error"]), ("failed", "ValueError: Endpoint missing")
        )
        await self.inspections.close()
        self.captures["Drone1"].close.assert_awaited_once()

    async def test_step_heading_and_position_changes_keep_capturing_the_actual_view(self):
        self.states["Drone1"].update(ground_speed_mps=8, climb_rate_mps=1)
        step = 0

        def moving(*args):
            nonlocal step
            step += 1
            self.states["Drone1"].update(heading_deg=90, north_m=200, east_m=100)
            return response(positive=step == 2)

        self.mission.planner_state = lambda: {
            "progress": {"Drone1": {"version": 1, "current_step": step}}
        }
        self.detect.side_effect = moving
        row, task = await self.start()
        await task
        self.assertEqual(self.inspections.rows[row["id"]]["status"], "candidate")
        self.assertEqual(self.detect.call_count, 2)
        records = sorted(
            (
                json.loads(path.read_text())
                for path in (self.directory / "inspections" / row["id"]).glob("*.json")
            ),
            key=lambda record: record["timestamp_ns"],
        )
        self.assertEqual(records[0]["camera_position_world_m"], {"x": 0, "y": 0, "z": 30})
        self.assertEqual(records[1]["camera_position_world_m"], {"x": 200, "y": 100, "z": 30})
        self.assertEqual(records[0]["camera_pose_local_ned"]["orientation"]["z"], 0)
        self.assertAlmostEqual(
            records[1]["camera_pose_local_ned"]["orientation"]["z"], math.sqrt(0.5)
        )
        self.mission.fleet.face_heading.assert_not_awaited()
        self.mission.fleet.execute.assert_not_awaited()

    async def test_transport_failure_is_recorded_once_and_not_retried(self):
        self.detect.side_effect = TimeoutError("SAM 3.1 timed out")
        row, task = await self.start()
        with self.assertRaisesRegex(RuntimeError, "SAM 3.1 timed out"):
            await task
        self.assertEqual(self.detect.call_count, 1)
        self.assertEqual(self.inspections.rows[row["id"]]["status"], "failed")
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertEqual(record["error"], "TimeoutError: SAM 3.1 timed out")

    async def test_cancellation_is_immediate_but_blocks_reuse_until_request_finishes(self):
        entered, finish = threading.Event(), threading.Event()

        def detect(*args):
            entered.set()
            assert finish.wait(2)
            return response()

        self.detect.side_effect = detect
        row, task = await self.start()
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        stopped = self.inspections.stop("Drone1", "Operator return")
        self.assertEqual(stopped["status"], "stopped")
        with self.assertRaisesRegex(AssertionError, "in-flight"):
            await self.start()
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.inspections.rows[row["id"]]["status"], "stopped")
        self.assertIsNone(self.inspections.rows[row["id"]]["latest"])
        self.assertEqual(self.detect.call_count, 1)

    async def test_cancellation_while_waiting_for_phase_starts_no_capture_or_request(self):
        entered = asyncio.Event()

        def scheduled(name, now):
            entered.set()
            return now + 60

        with patch("mission_planner.gcs.inspection.sampling_start", side_effect=scheduled):
            starting = asyncio.create_task(self.start())
            await asyncio.wait_for(entered.wait(), 1)
            self.inspections.stop("Drone1", "Operator return")
            with self.assertRaises(asyncio.CancelledError):
                await starting
        self.assertEqual(self.inspections.snapshot()[0]["status"], "stopped")
        self.assertEqual(self.captures, {})
        self.detect.assert_not_called()

    async def test_changed_plan_rejects_capture_before_vision(self):
        row, task = await self.start()
        self.mission.plan_active["plan"]["version"] = 2
        with self.assertRaisesRegex(RuntimeError, "flight plan changed"):
            await task
        self.detect.assert_not_called()
        self.assertEqual(self.inspections.rows[row["id"]]["status"], "failed")

    async def test_slow_success_continues_on_future_slot_without_backlog(self):
        starts = []

        def detect(*args):
            starts.append(time.monotonic())
            time.sleep(1.05)
            return response(positive=False)

        self.detect.side_effect = detect
        row, task = await self.start(duration=3)
        await task
        actual = self.inspections.rows[row["id"]]
        self.assertEqual((self.detect.call_count, actual["status"]), (3, "complete"))
        self.assertGreaterEqual(starts[1] - starts[0], 1.19)
        self.assertEqual(actual["sampling"]["missed_slots"], 15)
        self.assertGreaterEqual(actual["sampling"]["last_interval_s"], 1.19)
        self.assertGreaterEqual(actual["sampling"]["last_cycle_s"], 1.05)
        records = sorted(
            (
                json.loads(p.read_text())
                for p in (self.directory / "inspections" / row["id"]).glob("*.json")
            ),
            key=lambda item: item["timestamp_ns"],
        )
        self.assertIsNone(records[0]["cycle"]["interval_s"])
        for record in records:
            cycle = record["cycle"]
            self.assertGreaterEqual(cycle["request_s"], 1.05)
            for stage in (
                "aim_s",
                "capture_s",
                "request_s",
                "lateness_s",
            ):
                self.assertGreaterEqual(cycle[stage], 0)
            self.assertGreaterEqual(
                cycle["observation_s"],
                sum(
                    cycle[stage]
                    for stage in (
                        "aim_s",
                        "capture_s",
                        "request_s",
                    )
                ),
            )
            self.assertIsNone(record["error"])
        cycles = [
            json.loads(line)
            for line in (self.directory / "inspections" / row["id"] / "sampling.jsonl")
            .read_text()
            .splitlines()
        ]
        for cycle in cycles:
            self.assertGreaterEqual(cycle["evidence_write_s"], 0)
            self.assertGreaterEqual(cycle["processing_s"], 0)
            self.assertAlmostEqual(
                cycle["work_s"],
                cycle["observation_s"] + cycle["evidence_write_s"] + cycle["processing_s"],
            )

    async def test_successful_request_can_finish_after_duration_without_another_capture(self):
        def detect(*args):
            time.sleep(1.05)
            return response(positive=False)

        self.detect.side_effect = detect
        row, task = await self.start(duration=1)
        await task
        actual = self.inspections.rows[row["id"]]
        self.assertEqual((actual["status"], actual["frames"]), ("complete", 1))
        self.assertEqual(self.detect.call_count, 1)

    async def test_four_slow_streams_keep_their_phases_without_overlapping_visione_drone(self):
        def detect(*args):
            time.sleep(1.05)
            return response(positive=False)

        self.detect.side_effect = detect
        jobs = await asyncio.gather(*(self.start(name, duration=3) for name in self.states))
        await asyncio.gather(*(task for _, task in jobs))
        for row, _ in jobs:
            records = sorted(
                (
                    json.loads(p.read_text())
                    for p in (self.directory / "inspections" / row["id"]).glob("*.json")
                ),
                key=lambda record: record["timestamp_ns"],
            )
            self.assertEqual(len(records), 3)
            phase = (int(row["drone_id"][-1]) - 1) / 4
            for record in records:
                self.assertAlmostEqual(
                    (record["cycle"]["scheduled_monotonic_s"] * 5 - phase) % 1, 0, places=6
                )
            first, second = (record["cycle"] for record in records[:2])
            self.assertGreater(
                second["started_monotonic_s"], first["started_monotonic_s"] + first["observation_s"]
            )
            self.assertAlmostEqual(
                second["scheduled_monotonic_s"] - first["scheduled_monotonic_s"], 1.2
            )
        self.assertEqual(self.detect.call_count, 12)

    async def test_slow_evidence_write_is_included_before_the_next_slot_is_chosen(self):
        write_text = Path.write_text

        def write(path, *args, **kwargs):
            if path.parent.parent.name == "inspections":
                time.sleep(1.05)
            return write_text(path, *args, **kwargs)

        self.detect.side_effect = lambda *args: response(positive=False)
        with patch.object(Path, "write_text", autospec=True, side_effect=write):
            row, task = await self.start(duration=3)
            await task
        actual = self.inspections.rows[row["id"]]
        self.assertEqual((actual["status"], actual["frames"]), ("complete", 3))
        self.assertEqual(actual["sampling"]["missed_slots"], 15)
        self.assertGreaterEqual(actual["sampling"]["last_interval_s"], 1.19)
        cycles = [
            json.loads(line)
            for line in (self.directory / "inspections" / row["id"] / "sampling.jsonl")
            .read_text()
            .splitlines()
        ]
        self.assertEqual(len(cycles), 3)
        for cycle in cycles:
            self.assertGreaterEqual(cycle["evidence_write_s"], 1.05)
            self.assertGreaterEqual(cycle["work_s"], cycle["evidence_write_s"])

    async def test_late_failed_request_preserves_cancelled_ownership(self):
        entered, finish = threading.Event(), threading.Event()

        def detect(*args):
            entered.set()
            assert finish.wait(2)
            raise TimeoutError("SAM 3.1 timed out after cancellation")

        self.detect.side_effect = detect
        row, task = await self.start()
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        self.inspections.invalidate("Operator return", "Drone1")
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.inspections.rows[row["id"]]["status"], "stopped")
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertTrue(record["cancelled"])
        self.assertEqual(record["error"], "TimeoutError: SAM 3.1 timed out after cancellation")
        self.mission.fleet.execute.assert_not_awaited()

    async def test_no_capture_without_request_and_unhealthy_flight_is_rejected(self):
        self.assertEqual(self.inspections.snapshot(), [])
        self.detect.assert_not_called()
        for changes in (
            {"armed": False},
            {"landed_state": 1},
            {"landed_state": 0},
            {"camera_available": False},
            {"link_lost": True},
            {"ekf_healthy": False},
        ):
            before = copy.deepcopy(self.states["Drone1"])
            self.states["Drone1"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                flight_identity(self.mission, "Drone1")
            self.states["Drone1"] = before
        self.assertEqual(self.captures, {})

    async def test_stale_telemetry_and_missing_plan_participation_are_rejected(self):
        with patch.object(
            self.mission,
            "vehicles",
            return_value=[{**self.states["Drone1"], "received_at_ms": (time.time() - 3) * 1000}],
        ):
            with self.assertRaisesRegex(AssertionError, "stale"):
                flight_identity(self.mission, "Drone1")
        self.mission.plan_active["plan"]["drones"] = []
        with self.assertRaisesRegex(AssertionError, "participating"):
            flight_identity(self.mission, "Drone1")

    async def test_wrong_drone_frame_fails_before_any_vision_request(self):
        capture = SimpleNamespace(
            capture=AsyncMock(
                return_value=({"drone_id": "Drone2", "camera_id": "fpv"}, image_bytes())
            ),
            close=AsyncMock(),
        )
        with patch("mission_planner.gcs.inspection.ForwardCapture", return_value=capture):
            row, task = await self.start()
            with self.assertRaisesRegex(RuntimeError, "wrong drone or camera"):
                await task
        self.assertEqual(self.inspections.rows[row["id"]]["status"], "failed")
        self.detect.assert_not_called()
        await self.inspections.close()
        capture.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

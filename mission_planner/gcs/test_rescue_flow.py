"""Image-driven search interruptions through review, continuation and operator cancellation."""

import asyncio
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import cv2
import numpy as np

from mission_planner.agents.test_vision import mask_box
from mission_planner.environment import ROOT
from mission_planner.gcs import tracking
from mission_planner.gcs.geodesy import NAMES, ORIGIN, point_in_polygon
from mission_planner.gcs.inspection import Inspections, close_track
from mission_planner.gcs.planner import compile_plan, mission_item
from mission_planner.gcs.test_planner import airborne, search_plan
from mission_planner.gcs.test_validation import readback
from world.cosys.landmarks import load
from world.cosys.terrain import terrain_height


class RescueFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.review_ready = threading.Event()
        self.decisions = ["discard"]
        self.review_count = 0
        self.frame = np.full((2160, 3840, 3), 45, np.uint8)
        texture = np.random.default_rng(92).integers(30, 240, (220, 120, 3), np.uint8)
        self.frame[800:1020, 1800:1920] = texture
        self.frame[830:1000, 2200:2280] = texture[:170, :80]
        ok, encoded = cv2.imencode(".jpg", self.frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        assert ok
        jpeg = encoded.tobytes()
        self.states = {
            name: {
                "id": name,
                "armed": True,
                "landed_state": 2,
                "camera_available": True,
                "link_lost": False,
                "ekf_healthy": True,
                "battery_percent": 100,
                "mission_current": 1,
                "mode": "AUTO",
                "heading_deg": 0,
                "north_m": 0,
                "east_m": 0 if name == "Drone1" else 80,
                "altitude_above_home_m": terrain_height(0, 0) + 35 - 11.45,
                "home": {"altitude_msl_m": ORIGIN["altitude_msl_m"] + 11.45},
            }
            for name in ("Drone1", "Drone2")
        }
        polygon = [
            {"north_m": n, "east_m": e}
            for n, e in ((-100, -100), (100, -100), (100, 100), (-100, 100))
        ]
        survey = {
            "kind": "survey",
            "observe": True,
            "polygon": polygon,
            "height_m": 35,
            "speed_mps": 5,
            "spacing_m": 10,
            "bearing_deg": 0,
        }
        self.receipt = {
            "plan": {
                "version": 1,
                "search": {
                    "perimeter": polygon,
                    "detector_prompt": "person",
                    "threshold": 0.3,
                    "look_for": {
                        "objective": "Find the missing hiker",
                        "target": "person",
                        "attributes": [{"id": "jacket", "text": "red jacket"}],
                        "ignore": [],
                    },
                    "stand_off_m": 15,
                    "speed_mps": 3,
                    "max_lock_seconds": 10,
                    "look_ahead_m": 20,
                },
                "drones": [{"drone_id": "Drone1", "steps": [survey]}],
            },
            "missions": {
                "Drone1": {
                    "step_ranges": [{"first_seq": 1, "last_seq": 2, "duration_s": 100}],
                    "items": [
                        mission_item({"north_m": n, "east_m": 0}, 35, 16) for n in (0, 40, 80)
                    ],
                }
            },
        }
        self.checkpoint = {
            "point": {
                key: self.states["Drone1"][key]
                for key in ("north_m", "east_m", "altitude_above_home_m")
            },
        }

        async def start(name, sequences):
            self.assertEqual((name, sequences), ("Drone1", (1, 2)))
            self.states[name]["mode"] = "BRAKE"
            return copy.deepcopy(self.checkpoint)

        async def follow(name, checkpoint, speed):
            self.assertEqual((name, checkpoint, speed), ("Drone1", self.checkpoint, 3))
            self.states[name]["mode"] = "GUIDED"

        async def execute(command, arguments, **kwargs):
            self.assertEqual(command, "hold")
            self.states[arguments["drone_id"]]["mode"] = "BRAKE"

        async def resume(name, checkpoint, breadcrumbs, speed):
            self.assertEqual(checkpoint, self.checkpoint)
            self.assertEqual(breadcrumbs[0], self.checkpoint["point"])
            self.states[name].update(mode="AUTO", mission_current=3)

        self.fleet = SimpleNamespace(
            tracking_start=AsyncMock(side_effect=start),
            tracking_follow=AsyncMock(side_effect=follow),
            tracking_target=AsyncMock(),
            tracking_resume=AsyncMock(side_effect=resume),
            execute=AsyncMock(side_effect=execute),
        )
        self.events = []

        def event(kind, text, **fields):
            row = {"id": str(len(self.events)), "kind": kind, "text": text, **fields}
            self.events.append(row)
            return row

        self.mission = SimpleNamespace(
            id="00000000-0000-4000-8000-000000000001",
            directory=self.directory,
            fleet=self.fleet,
            events=self.events,
            event=Mock(side_effect=event),
            launch=asyncio.create_task,
            plan_active=self.receipt,
            vehicles=lambda: [
                {**state, "received_at_ms": time.time() * 1000} for state in self.states.values()
            ],
        )
        self.capture_count = 0

        async def capture():
            self.capture_count += 1
            return {
                "drone_id": "Drone1",
                "camera_id": "fpv",
                "horizontal_fov_deg": 90,
                "camera_view_revision": 1,
                "camera_position_world_m": {
                    "x": self.states["Drone1"]["north_m"],
                    "y": self.states["Drone1"]["east_m"],
                    "z": terrain_height(0, 0) + 35,
                },
                "camera_pose_local_ned": {
                    "orientation": {"x": 0, "y": -0.5, "z": 0, "w": 3**0.5 / 2}
                },
                "width": 3840,
                "height": 2160,
                "timestamp_ns": self.capture_count * 200_000_000,
                "captured_at_ms": time.time() * 1000,
            }, jpeg

        self.capture = SimpleNamespace(
            capture=AsyncMock(side_effect=capture),
            aim=AsyncMock(),
            visual_controlled=False,
            target=None,
            reset=AsyncMock(),
            close=AsyncMock(),
        )

        def view(target):
            self.capture.target = target
            self.capture.visual_controlled = False
            return {
                "target": target,
                "horizontal_fov_deg": 90,
                "revision": 1,
            }

        self.capture.view = AsyncMock(side_effect=view)

        def detect(*args):
            if self.track_error is not None and self.capture_count > 1:
                return {"width": 3840, "height": 2160, "detections": []}
            shift = (self.capture_count - 1) * 2
            return {
                "width": 3840,
                "height": 2160,
                "detections": [
                    {
                        "track_id": i + 1,
                        "class_name": "person",
                        "confidence": 0.8 - i / 10,
                        "box_xyxy": [x1 + shift, y1, x2 + shift, y2],
                        "observations": 2,
                        "mask": mask_box([x1 + shift, y1, x2 + shift, y2]),
                    }
                    for i, (x1, y1, x2, y2) in enumerate(
                        [[1800, 800, 1920, 1020], [2200, 830, 2280, 1000]][: len(self.decisions)]
                    )
                ],
            }

        def report(*args):
            assert self.review_ready.wait(5), "Test did not release its delayed reviewer"
            decision = self.decisions[self.review_count]
            self.review_count += 1
            return {
                "reasoning_tokens": 12,
                "report": {
                    "observations": [
                        {
                            "attribute_id": "jacket",
                            "visible": "cannot_tell",
                            "note": "Torso obscured",
                        }
                    ],
                    "reasoning": "Only the visible evidence was considered.",
                    "decision": decision,
                    "action": "none",
                    "reason": "Test reviewer decision: " + decision,
                },
            }

        self.detect = Mock(side_effect=detect)
        self.glimmer = Mock(return_value=SimpleNamespace(http=Mock(), report=report))
        self.track_error = None
        self.tracks = []

        def make_track(http, name, target, threshold):
            track = SimpleNamespace(http=http, timestamp_ns=0, status="new")

            def opened():
                track.status = "open"

            def advance(jpeg, timestamp_ns, timing=None):
                assert timestamp_ns > track.timestamp_ns
                track.timestamp_ns = timestamp_ns
                return self.detect(jpeg, timestamp_ns, timing)

            def close():
                assert track.status != "closed", "Test client was closed twice"
                track.status = "closed"

            track.open, track.advance, track.close = (
                Mock(side_effect=opened),
                Mock(side_effect=advance),
                Mock(side_effect=close),
            )
            self.tracks.append(track)
            return track

        self.vision = Mock(side_effect=make_track)
        for name, replacement in (
            ("mission_planner.gcs.inspection.ForwardCapture", Mock(return_value=self.capture)),
            ("mission_planner.gcs.inspection.vision_http", Mock(return_value=Mock())),
            ("mission_planner.gcs.inspection.Glimmer", self.glimmer),
            ("mission_planner.gcs.inspection.VisionTrack", self.vision),
            ("mission_planner.gcs.inspection.sampling_start", lambda name, now: now),
            (
                "mission_planner.gcs.tracking.ground_observation",
                lambda metadata, box: {
                    "ground": {
                        "north_m": 30 + (box[0] - 1800) / 10,
                        "east_m": 0,
                        "ground_m": terrain_height(30, 0),
                    },
                    "location_error": None,
                },
            ),
        ):
            patcher = patch(name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.inspections = Inspections(self.mission)

        async def centered(row, metadata, detections):
            if "rescue" in row and row["rescue"]["phase"] in ("reviewing", "tracking", "held"):
                row["camera_tracking"] = {
                    "status": "moved",
                    "centering": {"error_x_px": 0, "error_y_px": 0, "centered": True},
                    "frame_age_ms": 0,
                    "error": None,
                }

        self.inspections.visual.observe = AsyncMock(side_effect=centered)

    async def asyncTearDown(self):
        self.review_ready.set()
        await self.inspections.close()
        if self.inspections.reviews:
            await asyncio.gather(*self.inspections.reviews, return_exceptions=True)

    async def until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "Expected search event did not arrive")
            task = self.inspections.tasks["Drone1"]
            if task.done():
                task.result()
                self.fail("Search ended before the expected event")
            await asyncio.sleep(0.02)

    async def begin(self):
        await self.inspections.start_search(self.receipt)
        await self.until(lambda: self.fleet.tracking_target.await_count >= 2)
        self.assertEqual(self.review_count, 0)
        points = [call.args[1]["north_m"] for call in self.fleet.tracking_target.await_args_list]
        self.assertNotEqual(points[-1], points[0])
        self.assertEqual(self.states["Drone2"]["mode"], "AUTO")
        self.review_ready.set()
        return next(iter(self.inspections.rows.values()))

    async def test_new_search_hands_candidate_to_native_camera_and_review(self):
        del self.inspections.visual.observe
        self.capture.capture_inflight = 1
        self.capture.video_sample = None
        self.capture.follow_generation = 1
        self.capture.follow_diagnostic = {"error_x_px": 0, "error_y_px": 0, "centered": True}
        self.capture.begin_follow = Mock(return_value=1)
        self.capture.follow = AsyncMock(return_value={"status": "moved"})
        self.capture.cancel_follow = Mock()
        self.capture.end_follow = AsyncMock()
        await self.inspections.start_search(self.receipt)
        await self.until(
            lambda: (
                self.capture.follow.await_count > 0
                and self.glimmer.call_count == 1
                and self.fleet.tracking_target.await_count > 0
            )
        )
        row = next(iter(self.inspections.rows.values()))
        self.assertFalse(row["camera_changing"])
        self.assertEqual(row["camera_tracking"]["source"], "onboard_csrt")
        self.assertEqual(row["camera_tracking"]["track_id"], row["rescue"]["selected_track_id"])
        self.assertEqual(row["camera_tracking"]["status"], "moved")
        self.fleet.tracking_start.assert_awaited_once()
        self.assertIsNone(row["error"])

    async def test_aircraft_waits_for_camera_centering_before_following(self):
        centered = self.inspections.visual.observe.side_effect

        async def off_center(row, metadata, detections):
            await centered(row, metadata, detections)
            if row["camera_tracking"] is not None:
                row["camera_tracking"]["centering"]["error_x_px"] = 240

        self.inspections.visual.observe.side_effect = off_center
        await self.inspections.start_search(self.receipt)
        await self.until(
            lambda: next(iter(self.inspections.rows.values()))["camera_tracking"] is not None
        )
        await asyncio.sleep(0.3)
        self.fleet.tracking_follow.assert_not_awaited()
        self.fleet.tracking_target.assert_not_awaited()
        self.inspections.visual.observe.side_effect = centered
        await self.until(lambda: self.fleet.tracking_target.await_count > 0)

    async def test_active_follow_holds_position_while_recentering_without_latching_tracking_loss(
        self,
    ):
        self.decisions = ["recheck"]
        row = await self.begin()
        centered = self.inspections.visual.observe.side_effect

        async def off_center(row, metadata, detections):
            await centered(row, metadata, detections)
            if row["camera_tracking"] is not None:
                row["camera_tracking"]["centering"]["error_x_px"] = 240

        self.inspections.visual.observe.side_effect = off_center
        state = self.states["Drone1"]
        position = {k: state[k] for k in ("north_m", "east_m", "altitude_above_home_m")}
        await self.until(lambda: self.fleet.tracking_target.await_args.args[1] == position)
        await asyncio.sleep(2.2)
        self.assertIsNone(row["rescue"]["tracking_error"])
        self.assertEqual(state["mode"], "GUIDED")
        self.assertEqual(
            self.fleet.tracking_target.await_args.args[1:3], (position, state["heading_deg"])
        )
        self.fleet.execute.assert_not_awaited()
        self.inspections.visual.observe.side_effect = centered
        await self.until(lambda: self.fleet.tracking_target.await_args.args[1] != position)
        self.fleet.tracking_follow.assert_awaited_once()

    async def test_unobserved_pattern_starts_no_capture_or_models(self):
        del self.receipt["plan"]["drones"][0]["steps"][0]["observe"]
        with patch("mission_planner.gcs.inspection.vision_http") as client:
            self.assertEqual(await self.inspections.start_search(self.receipt), [])
            client.assert_not_called()
        self.assertFalse(self.inspections.tasks)
        self.assertFalse(self.inspections.cameras)
        self.capture.capture.assert_not_awaited()
        self.detect.assert_not_called()
        self.glimmer.assert_not_called()

    async def test_observed_follow_reviews_and_restores_its_waypoint_route(self):
        self.receipt["plan"]["drones"][0]["steps"] = [
            {
                "kind": "follow",
                "observe": True,
                "points": [{"north_m": 40, "east_m": 0}, {"north_m": 80, "east_m": 0}],
                "height_m": 35,
                "speed_mps": 5,
            }
        ]
        row = await self.begin()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["rescue"]["completed_steps"], [{"step_index": 0, "frames": 1}])
        self.assertGreaterEqual(self.detect.call_count, 3)
        self.assertEqual(self.review_count, 1)
        self.fleet.tracking_resume.assert_awaited_once()
        (target,) = self.capture.view.await_args_list[0].args
        self.assertAlmostEqual(target["x"], 20)
        self.assertAlmostEqual(target["y"], 0)
        self.assertAlmostEqual(target["z"], terrain_height(20, 0))

    async def test_observed_circle_aims_at_center_and_restores_its_waypoints(self):
        self.receipt["plan"]["drones"][0]["steps"] = [
            {
                "kind": "circle",
                "observe": True,
                "north_m": 20,
                "east_m": 40,
                "radius_m": 40,
                "turns": 1,
                "clockwise": True,
                "height_m": 35,
                "speed_mps": 5,
            }
        ]
        row = await self.begin()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["rescue"]["completed_steps"], [{"step_index": 0, "frames": 1}])
        self.assertGreaterEqual(self.detect.call_count, 3)
        self.assertEqual(self.review_count, 1)
        self.fleet.tracking_resume.assert_awaited_once()
        (target,) = self.capture.view.await_args_list[0].args
        self.assertEqual(target, {"x": 20, "y": 40, "z": terrain_height(20, 40)})

    async def test_vertical_observed_fly_aims_at_its_ground_endpoint(self):
        self.receipt["plan"]["drones"][0]["steps"] = [
            {
                "kind": "fly",
                "observe": True,
                "north_m": 0,
                "east_m": 0,
                "height_m": 45,
                "speed_mps": 5,
            }
        ]
        self.receipt["missions"]["Drone1"]["items"] = [
            mission_item({"north_m": 0, "east_m": 0}, height, 16) for height in (35, 40, 45)
        ]

        def sam(*args):
            self.states["Drone1"]["mission_current"] = 3
            return {"width": 3840, "height": 2160, "detections": []}

        self.detect.side_effect = sam
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 1))
        (target,) = self.capture.view.await_args_list[0].args
        self.assertEqual(target, {"x": 0, "y": 0, "z": terrain_height(0, 0)})
        self.fleet.execute.assert_not_awaited()

    async def test_first_airborne_observed_step_aims_from_compiled_home_and_anchor(self):
        fleet = airborne(-740, 0, 40)
        plan = search_plan()
        plan["drones"] = [
            {
                "drone_id": "Drone1",
                "steps": [
                    {
                        "kind": "fly",
                        "observe": True,
                        "north_m": -720,
                        "east_m": 0,
                        "height_m": 40,
                        "speed_mps": 5,
                    },
                    {"kind": "hover", "seconds": None},
                ],
            }
        ]
        self.receipt = compile_plan(plan, fleet, {name: readback() for name in NAMES}, load())
        self.mission.plan_active = self.receipt
        self.states["Drone1"].update(fleet[0], mission_current=1)
        mission = self.receipt["missions"]["Drone1"]
        self.assertEqual(mission["items"][0]["frame"], 0)
        self.assertEqual(mission["items"][1]["command"], 16)

        def sam(*args):
            self.states["Drone1"]["mission_current"] = mission["step_ranges"][0]["last_seq"] + 1
            return {"width": 3840, "height": 2160, "detections": []}

        self.detect.side_effect = sam
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 1))
        (target,) = self.capture.view.await_args_list[0].args
        self.assertAlmostEqual(target["x"], -720, delta=0.02)
        self.assertAlmostEqual(target["y"], 0, delta=0.02)
        self.assertEqual(target["z"], terrain_height(target["x"], target["y"]))
        self.fleet.execute.assert_not_awaited()

    async def test_step_transition_during_aim_skips_capture_and_inference(self):
        set_view = self.capture.view.side_effect

        async def view(*args):
            result = set_view(*args)
            self.states["Drone1"]["mission_current"] = 3
            return result

        self.capture.view.side_effect = view
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 0))
        self.capture.capture.assert_not_awaited()
        self.detect.assert_not_called()
        self.fleet.tracking_start.assert_not_awaited()
        self.fleet.execute.assert_not_awaited()
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertEqual(record["skipped"], "selected observation waypoint is not current")
        self.assertIsNone(record["response"])
        self.assertIsNone(record["error"])

    async def test_step_transition_during_capture_skips_inference_and_observation_count(self):
        capture = self.capture.capture.side_effect

        async def finish_step():
            result = await capture()
            self.states["Drone1"]["mission_current"] = 3
            return result

        self.capture.capture.side_effect = finish_step
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 0))
        self.assertEqual(row["rescue"]["completed_steps"], [{"step_index": 0, "frames": 0}])
        self.capture.capture.assert_awaited_once()
        self.detect.assert_not_called()
        self.fleet.tracking_start.assert_not_awaited()
        self.fleet.execute.assert_not_awaited()
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertEqual(record["skipped"], "selected observation waypoint is not current")
        self.assertEqual(record["step_index"], 0)
        self.assertIn("camera_position_world_m", record)
        self.assertIsNone(record["response"])

    async def test_camera_mount_across_sector_edge_does_not_drop_an_aircrafts_observation(self):
        capture = self.capture.capture.side_effect
        self.states["Drone1"]["north_m"] = 99.5

        async def outside_capture():
            metadata, jpeg = await capture()
            metadata["camera_position_world_m"]["x"] = 100.5
            return metadata, jpeg

        def detect(*args):
            self.states["Drone1"]["mission_current"] = 3
            return {"width": 3840, "height": 2160, "detections": []}

        self.detect.side_effect = detect
        self.capture.capture.side_effect = outside_capture
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 1))
        self.detect.assert_called_once()
        self.fleet.execute.assert_not_awaited()
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertIsNone(record["skipped"])
        self.assertEqual(record["camera_position_world_m"]["x"], 100.5)
        self.assertIsNotNone(record["response"])

    async def test_aircraft_leaving_sector_during_capture_still_skips_inference(self):
        capture = self.capture.capture.side_effect

        async def outside_capture():
            result = await capture()
            self.states["Drone1"]["north_m"] = 101
            return result

        self.capture.capture.side_effect = outside_capture
        await self.inspections.start_search(self.receipt)
        await self.until(lambda: bool(list(self.directory.glob("inspections/*/*.json"))))
        self.states["Drone1"]["mission_current"] = 3
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 0))
        self.detect.assert_not_called()
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertEqual(record["skipped"], "aircraft is outside its observation area")
        self.assertIsNone(record["response"])

    async def test_unobserved_transit_waits_for_selected_follow_step(self):
        self.receipt["plan"]["drones"][0]["steps"] = [
            {"kind": "fly", "north_m": 20, "east_m": 0, "height_m": 35, "speed_mps": 5},
            {
                "kind": "follow",
                "observe": True,
                "points": [{"north_m": 40, "east_m": 0}, {"north_m": 80, "east_m": 0}],
                "height_m": 35,
                "speed_mps": 5,
            },
        ]
        self.receipt["missions"]["Drone1"] = {
            "step_ranges": [
                {"first_seq": first, "last_seq": last, "duration_s": 20}
                for first, last in ((1, 1), (2, 3))
            ],
            "items": [mission_item({"north_m": n, "east_m": 0}, 35, 16) for n in (0, 20, 40, 80)],
        }

        def sam(*args):
            self.states["Drone1"]["mission_current"] = 4
            return {"width": 3840, "height": 2160, "detections": []}

        self.detect.side_effect = sam
        with patch.object(
            self.inspections, "_search_state", wraps=self.inspections._search_state
        ) as state:
            await self.inspections.start_search(self.receipt)
            await self.until(lambda: state.called)
            row = next(iter(self.inspections.rows.values()))
            self.assertEqual(row["rescue"]["step_index"], 1)
            self.assertEqual(row["rescue"]["phase"], "waiting")
            self.capture.capture.assert_not_awaited()
            self.detect.assert_not_called()
            self.states["Drone1"].update(mission_current=2, north_m=20)
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["rescue"]["completed_steps"], [{"step_index": 1, "frames": 1}])
        self.assertEqual(self.detect.call_count, 1)
        self.fleet.tracking_start.assert_not_awaited()
        self.fleet.execute.assert_not_awaited()

    async def test_concave_observation_area_does_not_sample_its_bounding_box_gap(self):
        polygon = [
            {"north_m": north, "east_m": east}
            for north, east in (
                (-100, -100),
                (100, -100),
                (100, -20),
                (-20, -20),
                (-20, 20),
                (100, 20),
                (100, 100),
                (-100, 100),
            )
        ]
        self.receipt["plan"]["drones"][0]["steps"][0]["polygon"] = polygon

        def sam(*args):
            self.states["Drone1"]["mission_current"] = 3
            return {"width": 3840, "height": 2160, "detections": []}

        self.detect.side_effect = sam
        with patch(
            "mission_planner.gcs.inspection.point_in_polygon", wraps=point_in_polygon
        ) as inside:
            await self.inspections.start_search(self.receipt)
            await self.until(lambda: inside.called)
            self.capture.capture.assert_not_awaited()
            self.detect.assert_not_called()
            self.states["Drone1"]["north_m"] = -50
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(self.detect.call_count, 1)
        self.fleet.execute.assert_not_awaited()

    async def test_explicit_follow_cannot_bind_a_future_selected_step(self):
        await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        started = await self.inspections.start("Drone1", "person", 0.3, 2)
        await self.inspections.tasks["Drone1"]
        row = self.inspections.rows[started["id"]]
        selected = self.receipt["plan"]["drones"][0]["steps"][0]
        self.receipt["plan"]["drones"][0]["steps"] = [
            {"kind": "fly", "north_m": 40, "east_m": 0, "height_m": 35, "speed_mps": 5},
            selected,
        ]
        self.receipt["missions"]["Drone1"]["step_ranges"] = [
            {"first_seq": first, "last_seq": last, "duration_s": 20}
            for first, last in ((1, 1), (2, 2))
        ]
        for sequence in (None, 0, 1, 3):
            with self.subTest(sequence=sequence):
                self.states["Drone1"]["mission_current"] = sequence
                with self.assertRaisesRegex(AssertionError, "active observed plan step"):
                    await self.inspections.follow_candidate(
                        row["id"], row["latest"]["frame_id"], 0, 6
                    )
                self.assertNotIn("rescue", row)
        self.states["Drone1"]["mission_current"] = 2
        self.receipt["missions"]["Drone1"]["items"][2]["command"] = 19
        with self.assertRaisesRegex(AssertionError, "active observed plan step"):
            await self.inspections.follow_candidate(row["id"], row["latest"]["frame_id"], 0, 6)
        self.assertNotIn("rescue", row)
        self.fleet.tracking_start.assert_not_awaited()
        self.fleet.execute.assert_not_awaited()

    async def test_candidate_hold_does_not_wait_for_optional_ground_projection(self):
        entered, release = threading.Event(), threading.Event()

        def project(metadata, box):
            entered.set()
            assert release.wait(5), "Test did not release its location calculation"
            return {"ground": None, "location_error": "Ground contact is obscured"}

        with patch("mission_planner.gcs.tracking.ground_observation", side_effect=project):
            try:
                await self.inspections.start_search(self.receipt)
                await self.until(lambda: entered.is_set())
                await self.until(lambda: self.fleet.tracking_start.await_count == 1)
                self.assertFalse(release.is_set())
                self.assertEqual(self.review_count, 0)
            finally:
                release.set()
                self.review_ready.set()

    async def test_negative_resumes_original_survey_without_candidate_wake(self):
        row = await self.begin()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.fleet.tracking_resume.assert_awaited_once()
        self.assertFalse(self.fleet.execute.called)
        self.assertGreaterEqual(self.detect.call_count, 3)
        self.assertFalse(
            any(
                "requires Wonder review" in call.args[1]
                for call in self.mission.event.call_args_list
            )
        )
        self.assertEqual(self.states["Drone1"]["mode"], "AUTO")
        (review,) = [event for event in self.events if event["kind"] == "review"]
        self.assertEqual(review["source"], "glimmer")
        evidence = review["result"]
        self.assertEqual(evidence["latest"]["frame_id"], row["latest"]["frame_id"])
        (report,) = evidence["rescue"]["reports"]
        self.assertEqual(report["report"]["decision"], "discard")
        self.assertEqual(report["images"]["frame_id"], row["latest"]["frame_id"])
        self.assertEqual(report["candidate_index"], 0)
        name = report["images"]["frame_ref"].rsplit("/", 1)[1]
        self.assertIn(name, self.inspections.assets)
        original = (self.directory / "evidence" / name).read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), report["images"]["sha256"])
        (call,) = [event for event in self.events if event["kind"] == "tool_call"]
        (result,) = [event for event in self.events if event["kind"] == "tool_result"]
        self.assertEqual(call["source"], "glimmer")
        self.assertEqual(result["source"], "glimmer")
        self.assertEqual(call["call_id"], "review-" + row["rescue"]["review_id"])
        self.assertEqual(call["call_id"], result["call_id"])
        self.assertTrue(result["result"]["ok"])
        self.assertEqual(result["result"]["status"], "continued")
        self.assertFalse(any(event["kind"] == "evidence" for event in self.events))
        row["rescue"]["reports"].clear()
        self.assertEqual(len(evidence["rescue"]["reports"]), 1)

    async def test_transit_outside_sector_waits_for_entry_before_searching(self):
        self.states["Drone1"]["north_m"] = -200
        with patch(
            "mission_planner.gcs.inspection.point_in_polygon", wraps=point_in_polygon
        ) as inside:
            await self.inspections.start_search(self.receipt)
            await self.until(lambda: inside.called)
            self.capture.capture.assert_not_awaited()
            self.detect.assert_not_called()
            self.fleet.tracking_start.assert_not_awaited()
            self.fleet.execute.assert_not_awaited()
            row = next(iter(self.inspections.rows.values()))
            self.assertEqual(row["rescue"]["phase"], "waiting")
            with self.assertRaisesRegex(AssertionError, "observation step owns its camera"):
                await self.inspections.set_camera_view("Drone1", 30, 0, 0)
            self.capture.view.assert_not_awaited()
            self.states["Drone1"]["north_m"] = 0
            await self.until(lambda: self.fleet.tracking_target.await_count >= 2)
            self.review_ready.set()
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.assertGreaterEqual(self.detect.call_count, 3)
        self.assertEqual(self.states["Drone1"]["mission_current"], 3)
        self.fleet.execute.assert_not_awaited()

    async def test_uncertain_review_preserves_lock_and_evidence_until_wonder_releases(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["status"] == "candidate")
        self.assertTrue(row["rescue"]["pending"])
        self.assertEqual(row["rescue"]["track"]["track_id"], 1)
        self.assertFalse(self.fleet.tracking_resume.called)
        target = row["rescue"]["track"]["ground"]
        with self.assertRaisesRegex(AssertionError, "Automatic candidate tracking owns the camera"):
            await self.inspections.set_camera_view("Drone1", target["north_m"], target["east_m"], 0)
        self.assertEqual(self.vision.call_count, 1)
        evidence = self.inspections.evidence(row["id"])
        self.assertEqual(evidence["metadata"]["timestamp_ns"], 200_000_000)
        self.assertEqual(len(evidence["images"]), 1)
        images = row["rescue"]["reports"][0]["images"]
        registry = json.loads((self.directory / "evidence/assets.json").read_text())
        name = images["frame_ref"].rsplit("/", 1)[1]
        self.assertIn(name, registry)
        original = (self.directory / "evidence" / name).read_bytes()
        self.assertEqual(evidence["images"], [original])
        self.assertEqual(hashlib.sha256(original).hexdigest(), images["sha256"])
        self.assertEqual(
            evidence["image_order"], [{"kind": "original 4K frame", "frame_id": images["frame_id"]}]
        )
        self.inspections.disregard_and_continue(
            row["rescue"]["review_id"], "Operator clarified clothing", call_id="wonder-release-1"
        )
        with self.assertRaisesRegex(AssertionError, "candidate has been released"):
            await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.fleet.tracking_resume.assert_awaited_once()
        (result,) = [event for event in self.events if event["kind"] == "tool_result"]
        self.assertEqual(result["call_id"], "wonder-release-1")
        self.assertEqual(result["source"], "agent")
        self.assertTrue(result["result"]["ok"])
        self.assertFalse(any(event["kind"] == "tool_call" for event in self.events))

    async def test_operator_override_revokes_late_release_and_stops_target_updates(self):
        self.decisions = ["escalate"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        review_id = row["rescue"]["review_id"]
        self.inspections.invalidate("Operator return_home", "Drone1")
        await self.inspections.flight_stopped("Drone1")
        self.states["Drone1"]["mode"] = "RTL"
        commands = self.fleet.tracking_target.await_count
        await self.inspections.close()
        self.assertEqual(self.fleet.tracking_target.await_count, commands)
        self.assertEqual(self.states["Drone2"]["mode"], "AUTO")
        with self.assertRaisesRegex(AssertionError, "no longer owns"):
            self.inspections.disregard_and_continue(review_id, "Late rejection", "glimmer")
        self.assertFalse(self.fleet.tracking_resume.called)

    async def test_negative_returning_after_operator_override_cannot_resume(self):
        await self.inspections.start_search(self.receipt)
        await self.until(lambda: self.fleet.tracking_target.await_count >= 2)
        self.assertEqual(self.review_count, 0)
        self.inspections.invalidate("Operator hold", "Drone1")
        await self.inspections.flight_stopped("Drone1")
        self.states["Drone1"]["mode"] = "BRAKE"
        self.review_ready.set()
        await self.inspections.close()
        if self.inspections.reviews:
            await asyncio.gather(*self.inspections.reviews)
        self.assertFalse(self.fleet.tracking_resume.called)
        self.assertFalse(
            any(
                call.kwargs.get("name") == "disregard_and_continue"
                for call in self.mission.event.call_args_list
            )
        )

    async def test_close_drains_review_after_operator_releases_flight_control(self):
        await self.inspections.start_search(self.receipt)
        await self.until(lambda: self.fleet.tracking_target.await_count >= 2)
        self.inspections.invalidate("Operator return_home", "Drone1")
        await asyncio.wait_for(self.inspections.flight_stopped("Drone1"), 1)
        self.states["Drone1"]["mode"] = "RTL"
        closing = asyncio.create_task(self.inspections.close())
        await asyncio.sleep(0.02)
        self.assertFalse(closing.done())
        self.glimmer.return_value.http.close.assert_not_called()
        self.review_ready.set()
        await asyncio.wait_for(closing, 2)
        self.glimmer.return_value.http.close.assert_called_once()
        self.assertEqual(self.review_count, 1)
        self.assertFalse(self.inspections.reviews)
        self.fleet.tracking_resume.assert_not_awaited()

    async def test_cancelled_review_finishes_logging_before_closing_its_http_client(self):
        entered = threading.Event()
        original = self.glimmer.return_value.report

        def report(*args):
            entered.set()
            return original(*args)

        self.glimmer.return_value.report = report
        metadata, jpeg = await self.capture.capture()
        metadata["frame_id"] = "00000000-0000-4000-8000-000000000001"
        metadata["sha256"] = hashlib.sha256(jpeg).hexdigest()
        detection = {"track_id": 1, "box_xyxy": [1800, 800, 1920, 1020]}
        row = {
            "id": "cancelled-inspection",
            "drone_id": "Drone1",
            "status": "stopped",
            "prompt": "person",
            "rescue": {"review_id": "cancelled-review"},
            "latest": {"frame_id": metadata["frame_id"], "detections": [dict(detection)]},
        }
        self.inspections.rows[row["id"]] = row
        task = asyncio.create_task(
            self.inspections._review_all(
                row,
                [(0, detection)],
                metadata,
                jpeg,
                self.receipt["plan"]["search"],
                self.directory,
                threading.Event(),
            )
        )
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        task.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        self.glimmer.return_value.http.close.assert_not_called()
        self.review_ready.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.glimmer.return_value.http.close.assert_called_once()
        self.assertEqual(self.review_count, 1)
        log = json.loads((self.directory / "cancelled-review-0.json").read_text())
        self.assertEqual(log["result"]["report"]["decision"], "discard")
        self.assertIsNone(log["error"])

    async def test_untrackable_candidate_is_reviewed_while_held_and_negative_resumes(self):
        self.track_error = "Temporal tracker could not locate the selected object"
        await self.inspections.start_search(self.receipt)
        await self.until(
            lambda: (
                self.glimmer.called
                and next(iter(self.inspections.rows.values()))["rescue"]["tracking_error"]
                is not None
            )
        )
        row = next(iter(self.inspections.rows.values()))
        self.assertIn(
            "track 1 has not been visible for two seconds", row["rescue"]["tracking_error"]
        )
        latest = row["latest"]
        metadata = self.inspections.metadata[latest["frame_id"]]
        (detection,) = latest["detections"]
        observation = tracking.ground_observation(metadata, detection["box_xyxy"])
        self.assertIsNotNone(observation["ground"])
        self.assertEqual(detection["ground"], observation["ground"])
        self.assertIsNone(detection["location_error"])
        self.review_ready.set()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.fleet.tracking_resume.assert_awaited_once()
        self.fleet.tracking_target.assert_not_awaited()
        self.assertEqual(self.review_count, 1)
        (review,) = [event for event in self.events if event["kind"] == "review"]
        self.assertEqual(
            review["result"]["latest"]["detections"][0]["ground"], observation["ground"]
        )

    async def test_lost_correspondence_preserves_late_negative_review_and_saved_resume(self):
        with patch.object(
            self,
            "track_error",
            "Candidate track lost consistency",
        ):
            await self.inspections.start_search(self.receipt)
            row = next(iter(self.inspections.rows.values()))
            await self.until(lambda: row["rescue"]["tracking_error"] is not None)
            self.assertEqual(row["status"], "running")
            self.assertIsNone(row["error"])
            self.assertIsNone(row["rescue"]["track"])
            self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
            self.fleet.execute.assert_not_awaited()
            self.fleet.tracking_follow.assert_not_awaited()
            self.review_ready.set()
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual((row["status"], self.review_count), ("complete", 1))
        self.fleet.tracking_resume.assert_awaited_once()
        self.assertGreaterEqual(self.detect.call_count, 3)
        self.fleet.tracking_target.assert_not_awaited()

    async def test_untrackable_uncertain_candidate_stays_held_and_notifies_wonder(self):
        self.track_error = "Temporal tracker could not locate the selected object"
        self.decisions = ["recheck"]
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: row["rescue"]["tracking_error"] is not None)
        self.review_ready.set()
        await self.until(lambda: row["status"] == "candidate")
        self.assertTrue(row["rescue"]["pending"])
        self.assertEqual(row["rescue"]["reports"][0]["report"]["decision"], "recheck")
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.fleet.tracking_follow.assert_not_awaited()
        self.fleet.tracking_resume.assert_not_awaited()
        view = await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        self.assertEqual(view["horizontal_fov_deg"], 90)
        self.assertEqual(row["camera_view"], self.inspections.camera_views["Drone1"])
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.fleet.execute.assert_not_awaited()
        self.fleet.tracking_target.assert_not_awaited()

    async def held_lock(self, frames):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        self.track_error = "Candidate left the expected region"
        await self.until(lambda: row["rescue"]["held_frame"] is not None)
        held = self.inspections.held_buffers[row["rescue"]["review_id"]]
        await self.until(lambda: len(held) >= frames)
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        return row, held

    async def test_relock_rejects_unknown_index_and_frames_beyond_the_hold_buffer(self):
        with (
            patch("mission_planner.gcs.inspection.CAPTURE_PERIOD_S", 0.05),
            patch("mission_planner.gcs.inspection.HOLD_FRAMES", 3),
        ):
            row, held = await self.held_lock(1)
            oldest = held[0]["metadata"]["frame_id"]
            with self.assertRaisesRegex(AssertionError, "outside this frame"):
                await self.inspections.relock_candidate(row["id"], oldest, 1)
            await self.until(lambda: all(e["metadata"]["frame_id"] != oldest for e in held))
            self.assertEqual(len(held), 3)
            with self.assertRaisesRegex(AssertionError, "no longer buffered"):
                await self.inspections.relock_candidate(row["id"], oldest, 0)
            with self.assertRaisesRegex(AssertionError, "no longer buffered"):
                await self.inspections.relock_candidate(row["id"], "unknown-frame", 0)
            self.assertIsNotNone(row["rescue"]["tracking_error"])
            self.assertNotIn(row["rescue"]["review_id"], self.inspections.relocks)

    async def test_healthy_following_does_not_consume_the_interruption_allowance(self):
        self.receipt["plan"]["search"]["max_lock_seconds"] = 2
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["healthy_s"] > 2.5)
        self.assertEqual(row["status"], "candidate")
        self.assertIsNone(row["rescue"]["tracking_error"])
        self.track_error = "Candidate left the expected region"
        lost = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "allowance exhausted"):
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertLess(time.monotonic() - lost, 2)
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["rescue"]["visual_hold"])
        self.assertEqual(row["rescue"]["selected_track_id"], 1)
        self.assertIsNone(row["rescue"]["held_frame"])
        self.assertEqual(self.receipt["plan"]["search"]["max_lock_seconds"], 2)
        self.fleet.tracking_resume.assert_not_awaited()
        self.inspections.invalidate("Return home", "Drone1")
        self.assertFalse(row["rescue"]["visual_hold"])

    async def test_reobserved_track_stays_held_until_wonder_explicitly_relocks(self):
        row, _ = await self.held_lock(1)
        commands = self.fleet.tracking_target.await_count
        self.track_error = None
        await self.until(lambda: bool(row["rescue"]["held_frame"]["detections"]))
        observed = await self.inspections.get_candidate(row["id"])
        self.assertEqual(self.fleet.tracking_target.await_count, commands)
        result = await self.inspections.relock_candidate(
            row["id"], observed["frame"]["frame_id"], 0
        )
        self.assertEqual(result["relock"], {"status": "tracking", "track_id": 1})
        self.assertEqual(row["rescue"]["selected_track_id"], 1)
        self.assertIsNone(row["rescue"]["tracking_error"])
        self.assertEqual(self.states["Drone1"]["mode"], "GUIDED")
        self.assertGreater(self.fleet.tracking_target.await_count, commands)
        self.assertEqual(self.vision.call_count, 1)

    async def test_requested_follow_deadline_holds_the_existing_track(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        observed = await self.inspections.get_candidate(row["id"])
        await self.inspections.follow_candidate(row["id"], observed["frame"]["frame_id"], 0, 1)
        await self.until(lambda: row["rescue"]["tracking_error"] is not None)
        self.assertEqual(row["rescue"]["tracking_error"], "Requested follow duration elapsed")
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.fleet.tracking_start.assert_awaited_once()
        self.fleet.tracking_resume.assert_not_awaited()

    async def test_failed_camera_view_requires_explicit_verification_before_release(self):
        self.track_error = "Temporal tracker could not locate the selected object"
        self.decisions = ["recheck"]
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: row["rescue"]["tracking_error"] is not None)
        self.review_ready.set()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        view = self.capture.view.side_effect
        self.capture.view.side_effect = TimeoutError("Camera pose readback failed")
        with self.assertRaisesRegex(TimeoutError, "Camera pose readback failed"):
            await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        self.assertIsNone(self.inspections.camera_views["Drone1"])
        self.assertTrue(row["camera_changing"])
        self.assertEqual(row["camera_view"]["horizontal_fov_deg"], 90)
        review_id = row["rescue"]["review_id"]
        with self.assertRaisesRegex(AssertionError, "explicitly set and verify a camera view"):
            self.inspections.disregard_and_continue(review_id, "Candidate excluded")
        self.assertIsNone(self.inspections.releases[review_id]["reason"])
        self.fleet.tracking_resume.assert_not_awaited()
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.capture.view.side_effect = view
        await self.inspections.set_camera_view("Drone1", 30, 0, 0)
        self.assertFalse(row["camera_changing"])
        self.inspections.disregard_and_continue(review_id, "Candidate excluded", call_id="release")
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.fleet.tracking_resume.assert_awaited_once()
        self.assertEqual(self.states["Drone1"]["mode"], "AUTO")

    async def test_override_revokes_untrackable_candidate_review_release(self):
        self.track_error = "Temporal tracker could not locate the selected object"
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(
            lambda: row["rescue"]["tracking_error"] is not None and self.glimmer.called
        )
        self.inspections.invalidate("Operator return_home", "Drone1")
        await self.inspections.flight_stopped("Drone1")
        self.states["Drone1"]["mode"] = "RTL"
        self.review_ready.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.inspections.tasks["Drone1"]
        if self.inspections.reviews:
            await asyncio.gather(*self.inspections.reviews)
        self.assertEqual(row["status"], "stopped")
        self.assertFalse(row["rescue"]["pending"])
        self.fleet.tracking_resume.assert_not_awaited()
        self.fleet.execute.assert_not_awaited()

    async def test_capture_failure_is_terminal_but_completed_review_remains_available(self):
        acquire = self.capture.capture.side_effect

        async def capture():
            if self.fleet.tracking_start.called:
                raise TimeoutError("Tracking camera RPC timed out")
            return await acquire()

        self.capture.capture.side_effect = capture
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(RuntimeError, "Tracking camera RPC timed out"):
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "failed")
        self.assertFalse(row["rescue"]["reports"])
        self.assertGreater(self.inspections.lock_seconds["Drone1"], 0)
        self.review_ready.set()
        if self.inspections.reviews:
            await asyncio.gather(*self.inspections.reviews)
        self.assertEqual(row["rescue"]["reports"][0]["report"]["decision"], "discard")
        self.assertTrue(row["rescue"]["pending"])
        self.fleet.tracking_resume.assert_not_awaited()

    async def test_negative_review_waits_for_verified_pause_checkpoint_before_resume(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        pause = self.fleet.tracking_start.side_effect

        async def delayed_pause(*args):
            entered.set()
            await finish.wait()
            return await pause(*args)

        self.track_error = "Temporal tracker could not locate the selected object"
        self.fleet.tracking_start.side_effect = delayed_pause
        self.review_ready.set()
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(entered.wait(), 1)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: bool(row["rescue"]["reports"]))
        self.fleet.tracking_resume.assert_not_awaited()
        finish.set()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.fleet.tracking_resume.assert_awaited_once()

    async def test_review_failure_remains_terminal_and_is_not_retried(self):
        def report(*args):
            raise TimeoutError("Glimmer request timed out")

        self.glimmer.return_value.report = report
        self.track_error = "Temporal tracker could not locate the selected object"
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(RuntimeError, "Glimmer request timed out"):
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["rescue"]["pending"])
        self.assertEqual(self.glimmer.call_count, 1)
        self.fleet.tracking_resume.assert_not_awaited()

    async def test_failed_image_registration_does_not_publish_an_unavailable_review(self):
        register = self.inspections._register

        def failed(names):
            if any(name.endswith("-frame.jpg") for name in names):
                raise OSError("Evidence registry is not writable")
            register(names)

        self.review_ready.set()
        self.track_error = "Temporal tracker could not locate the selected object"
        with patch.object(self.inspections, "_register", side_effect=failed):
            await self.inspections.start_search(self.receipt)
            with self.assertRaisesRegex(RuntimeError, "Evidence registry is not writable"):
                await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "failed")
        self.assertFalse(row["rescue"]["reports"])
        self.assertEqual(self.review_count, 0)
        self.fleet.tracking_resume.assert_not_awaited()

    async def test_stale_detector_evidence_cannot_start_following(self):
        acquire = self.capture.capture.side_effect

        async def stale_capture():
            metadata, image = await acquire()
            metadata["captured_at_ms"] -= 2000
            return metadata, image

        self.capture.capture.side_effect = stale_capture
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: row["rescue"]["tracking_error"] is not None)
        self.assertIn("too old", row["rescue"]["tracking_error"])
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.fleet.tracking_follow.assert_not_awaited()
        self.review_ready.set()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.fleet.tracking_resume.assert_awaited_once()

    async def test_slow_empty_results_continue_search_without_a_lock_or_failure(self):
        def sam(*args):
            time.sleep(1.05)
            if self.detect.call_count == 2:
                self.states["Drone1"]["mission_current"] = 3
            return {"width": 3840, "height": 2160, "detections": []}

        self.detect.side_effect = sam
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 6)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 2))
        self.assertGreaterEqual(row["sampling"]["missed_slots"], 10)
        self.assertTrue(row["rescue"]["pending"])
        self.assertIsNone(row["error"])
        self.fleet.execute.assert_not_awaited()
        self.fleet.tracking_start.assert_not_awaited()
        self.glimmer.assert_not_called()

    async def test_multiple_remaining_surveys_sample_only_their_own_steps_and_keep_coverage(
        self,
    ):
        first = self.receipt["plan"]["drones"][0]["steps"][0]
        second = copy.deepcopy(first)
        for point in second["polygon"]:
            point["north_m"] += 300
        self.receipt["plan"]["drones"][0]["steps"] = [
            first,
            {"kind": "fly", "north_m": 250, "east_m": 0, "height_m": 35, "speed_mps": 5},
            second,
            {"kind": "return_home", "height_m": 35, "speed_mps": 5},
        ]
        self.receipt["plan"]["drones"].append(
            {
                "drone_id": "Drone2",
                "steps": [{"kind": "return_home", "height_m": 35, "speed_mps": 5}],
            }
        )
        self.receipt["missions"]["Drone1"] = {
            "step_ranges": [
                {"first_seq": first, "last_seq": last, "duration_s": 20}
                for first, last in ((1, 2), (3, 3), (4, 5), (6, 7))
            ],
            "items": [
                mission_item({"north_m": n, "east_m": 0}, 35, 16)
                for n in (0, 40, 80, 250, 260, 320, 100, 0)
            ],
        }

        def sam(*args):
            self.states["Drone1"].update(
                mission_current=3 if self.detect.call_count == 1 else 6, north_m=150
            )
            return {"width": 3840, "height": 2160, "detections": []}

        self.detect.side_effect = sam
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: row["rescue"]["step_index"] == 2)
        self.assertEqual(row["rescue"]["phase"], "waiting")
        self.assertEqual(row["frames"], 1)
        self.assertNotIn("Drone2", self.inspections.tasks)
        self.states["Drone1"].update(mission_current=4, north_m=250)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual((row["status"], row["frames"]), ("complete", 2))
        self.assertEqual(
            row["rescue"]["completed_steps"],
            [{"step_index": 0, "frames": 1}, {"step_index": 2, "frames": 1}],
        )
        directory = self.directory / "inspections" / row["id"]
        records = [json.loads(path.read_text()) for path in directory.glob("*.json")]
        self.assertEqual(sorted(record["step_index"] for record in records), [0, 2])
        before = copy.deepcopy(row)
        self.receipt["plan"]["version"] = 2
        await self.inspections.start_search(self.receipt)
        self.assertEqual(self.inspections.rows[row["id"]], before)
        self.assertEqual(len(self.inspections.rows), 2)
        self.fleet.execute.assert_not_awaited()

    async def test_slow_camera_aim_is_measured_without_stopping_discovery(self):
        set_view = self.capture.view.side_effect

        async def view(*args):
            await asyncio.sleep(1.05)
            return set_view(*args)

        def sam(*args):
            self.states["Drone1"]["mission_current"] = 3
            return {"width": 3840, "height": 2160, "detections": []}

        self.capture.view.side_effect = view
        self.detect.side_effect = sam
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 1))
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertGreaterEqual(record["cycle"]["aim_s"], 1.05)
        self.assertGreaterEqual(
            record["cycle"]["observation_s"],
            record["cycle"]["aim_s"] + record["cycle"]["capture_s"],
        )
        self.assertGreaterEqual(row["sampling"]["missed_slots"], 5)
        self.fleet.execute.assert_not_awaited()

    async def test_real_detector_failure_holds_only_affected_drone_and_publishes_pending_error(
        self,
    ):
        self.detect.side_effect = TimeoutError("SAM 3.1 request timed out")
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(RuntimeError, "SAM 3.1 request timed out"):
            await self.inspections.tasks["Drone1"]
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(
            (row["status"], row["error"]), ("failed", "TimeoutError: SAM 3.1 request timed out")
        )
        self.assertTrue(row["rescue"]["pending"])
        self.fleet.execute.assert_awaited_once_with(
            "hold",
            {"drone_id": "Drone1"},
            expected_mode="AUTO",
            expected_step=(self.receipt["missions"]["Drone1"]["items"], 1, 2),
        )
        self.assertEqual(self.states["Drone2"]["mode"], "AUTO")
        self.assertEqual(self.detect.call_count, 1)
        self.fleet.tracking_start.assert_not_awaited()

    async def test_positive_after_step_ends_preserves_evidence_without_holding_later_flight(self):
        self.decisions = ["escalate"]
        self.receipt["plan"]["drones"][0]["steps"].append(
            {"kind": "fly", "north_m": 150, "east_m": 0, "height_m": 35, "speed_mps": 5}
        )
        self.receipt["missions"]["Drone1"]["step_ranges"].append(
            {"first_seq": 3, "last_seq": 3, "duration_s": 20}
        )
        self.receipt["missions"]["Drone1"]["items"].append(
            mission_item({"north_m": 150, "east_m": 0}, 35, 16)
        )
        infer, report = self.detect.side_effect, self.glimmer.return_value.report
        entered = threading.Event()

        def sam(*args):
            result = infer(*args)
            self.states["Drone1"]["mission_current"] = 3
            return result

        def delayed_report(*args):
            entered.set()
            return report(*args)

        async def reject_expired_step(name, sequences):
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            self.assertEqual((name, sequences), ("Drone1", (1, 2)))
            raise AssertionError("Drone1: candidate arrived outside its observation step")

        self.detect.side_effect = sam
        self.glimmer.return_value.report = delayed_report
        self.fleet.tracking_start.side_effect = reject_expired_step
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(
            RuntimeError,
            "observation step ended:.*hold not sent: flight control is no longer owned",
        ):
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("failed", 1))
        self.assertTrue(row["rescue"]["pending"])
        self.assertEqual(self.states["Drone1"]["mode"], "AUTO")
        self.assertEqual(
            self.inspections.evidence(row["id"])["metadata"]["camera_position_world_m"]["x"], 0
        )
        self.fleet.execute.assert_not_awaited()
        self.fleet.tracking_follow.assert_not_awaited()
        self.fleet.tracking_resume.assert_not_awaited()
        self.review_ready.set()
        await asyncio.wait_for(asyncio.gather(*self.inspections.reviews), 5)
        self.assertEqual(row["rescue"]["reports"][0]["report"]["decision"], "escalate")
        self.assertEqual(len(self.inspections.evidence(row["id"])["images"]), 1)
        self.assertEqual(self.inspections.pending_events()[0]["id"], row["id"])
        with self.assertRaisesRegex(AssertionError, "no longer available"):
            self.inspections.disregard_and_continue(row["rescue"]["review_id"], "Do not resume")

    async def test_autopilot_return_land_or_disarm_revokes_failure_hold_authority(self):
        for mode, armed in (("RTL", True), ("LAND", True), ("STABILIZE", False), ("GUIDED", True)):
            with self.subTest(mode=mode, armed=armed):
                self.states["Drone1"].update(mode="AUTO", armed=True)

                def sam(*args):
                    self.states["Drone1"].update(mode=mode, armed=armed)
                    raise TimeoutError("SAM 3.1 timed out after autopilot change")

                self.detect.side_effect = sam
                await self.inspections.start_search(self.receipt)
                with self.assertRaisesRegex(
                    RuntimeError, "hold not sent: flight control is no longer owned"
                ):
                    await self.inspections.tasks["Drone1"]
                row = self.inspections.snapshot()[-1]
                self.assertEqual(row["status"], "failed")
                self.assertTrue(row["rescue"]["pending"])
                self.assertIn("mode=" + mode, row["error"])
                self.fleet.execute.assert_not_awaited()

    async def test_capture_failure_is_terminal_and_logged_before_any_model_request(self):
        self.capture.capture.side_effect = TimeoutError("Camera RPC timed out")
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(RuntimeError, "Camera RPC timed out"):
            await self.inspections.tasks["Drone1"]
        row = next(iter(self.inspections.rows.values()))
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertEqual(record["error"], "TimeoutError: Camera RPC timed out")
        self.assertGreaterEqual(record["cycle"]["capture_s"], 0)
        self.assertIsNone(record["cycle"]["request_s"])
        self.assertTrue(row["rescue"]["pending"])
        self.fleet.execute.assert_awaited_once_with(
            "hold",
            {"drone_id": "Drone1"},
            expected_mode="AUTO",
            expected_step=(self.receipt["missions"]["Drone1"]["items"], 1, 2),
        )
        self.detect.assert_not_called()

    async def test_failure_to_hold_reports_both_errors_without_retry(self):
        self.detect.side_effect = TimeoutError("SAM 3.1 request timed out")
        self.fleet.execute.side_effect = TimeoutError("BRAKE acknowledgement timed out")
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(
            RuntimeError,
            "SAM 3.1 request timed out; hold failed: TimeoutError: BRAKE acknowledgement timed out",
        ):
            await self.inspections.tasks["Drone1"]
        row = next(iter(self.inspections.rows.values()))
        self.assertIn("hold failed", row["error"])
        self.assertTrue(row["rescue"]["pending"])
        self.assertTrue(self.inspections.control_idle["Drone1"].is_set())
        self.fleet.execute.assert_awaited_once()

    async def test_override_during_failed_request_drain_cannot_issue_a_late_hold(self):
        entered, finish = threading.Event(), threading.Event()

        def sam(*args):
            entered.set()
            assert finish.wait(2)
            raise TimeoutError("Late SAM 3.1 timeout")

        self.detect.side_effect = sam
        await self.inspections.start_search(self.receipt)
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        self.inspections.invalidate("Operator return_home", "Drone1")
        await self.inspections.flight_stopped("Drone1")
        self.states["Drone1"]["mode"] = "RTL"
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.inspections.tasks["Drone1"]
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "stopped")
        self.assertFalse(row["rescue"]["pending"])
        self.fleet.execute.assert_not_awaited()

    async def test_override_during_failure_hold_cancels_cleanup_before_return(self):
        entered = asyncio.Event()

        async def hold(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        self.detect.side_effect = TimeoutError("SAM 3.1 request timed out")
        self.fleet.execute.side_effect = hold
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(entered.wait(), 1)
        self.assertFalse(self.inspections.control_idle["Drone1"].is_set())
        self.inspections.invalidate("Operator return_home", "Drone1")
        await asyncio.wait_for(self.inspections.flight_stopped("Drone1"), 1)
        self.states["Drone1"]["mode"] = "RTL"
        with self.assertRaises(asyncio.CancelledError):
            await self.inspections.tasks["Drone1"]
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "stopped")
        self.assertFalse(row["rescue"]["pending"])

    async def test_pending_detector_request_holds_before_request_finishes(self):
        self.decisions = ["recheck"]
        commands, holds = [], []
        hold = self.fleet.execute.side_effect

        async def dispatch(*args, **kwargs):
            commands.append(time.monotonic())

        async def stopped(*args, **kwargs):
            holds.append(time.monotonic())
            return await hold(*args, **kwargs)

        self.fleet.tracking_target.side_effect = dispatch
        self.fleet.execute.side_effect = stopped
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        entered, finish = threading.Event(), threading.Event()
        track = self.tracks[0]
        advance = track.advance.side_effect

        def delayed(*args):
            entered.set()
            assert finish.wait(5), "Test did not complete pending detector request"
            return advance(*args)

        track.advance.side_effect = delayed
        try:
            await self.until(entered.is_set)
            await asyncio.wait_for(self.until(lambda: self.fleet.execute.await_count == 1), 2.4)
            self.assertFalse(finish.is_set())
            track.close.assert_not_called()
            self.assertEqual(row["rescue"]["phase"], "held")
            self.assertIsNone(row["rescue"]["live_frame"])
            self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
            self.assertGreaterEqual(holds[0] - commands[-1], 2)
            self.assertLess(holds[0] - commands[-1], 2.3)
            self.assertIn(
                "No fresh tracking command for two seconds", row["rescue"]["tracking_error"]
            )
            command_count = self.fleet.tracking_target.await_count
        finally:
            finish.set()
        await asyncio.sleep(0.25)
        self.assertEqual(self.fleet.tracking_target.await_count, command_count)
        self.assertEqual(self.vision.call_count, 1)
        track.close.assert_not_called()
        record = next((self.directory / "inspections" / row["id"]).glob("*-tracking.jsonl"))
        records = [json.loads(line) for line in record.read_text().splitlines()]
        holds = [r for r in records if r["phase"] == "held"]
        self.assertEqual(len(holds), 1)
        self.assertIn("two seconds", holds[0]["reason"])

    async def test_valid_delayed_observations_keep_checked_position_following_active(self):
        self.decisions = ["recheck"]
        acquire, create = self.capture.capture.side_effect, self.vision.side_effect
        self.dispatch_timings = []

        async def capture():
            await asyncio.sleep(0.25)
            return await acquire()

        def create_delayed(*args):
            track = create(*args)
            advance = track.advance.side_effect

            def delayed(*args):
                time.sleep(0.55)
                return advance(*args)

            track.advance.side_effect = delayed
            return track

        async def dispatch(*args, captured_at_ms):
            self.dispatch_timings.append(
                {
                    "monotonic_s": time.monotonic(),
                    "source_age_s": (time.time() * 1000 - captured_at_ms) / 1000,
                }
            )

        self.capture.capture.side_effect = capture
        self.vision.side_effect = create_delayed
        self.fleet.tracking_target.side_effect = dispatch
        await self.inspections.start_search(self.receipt)
        await self.until(lambda: len(self.dispatch_timings) >= 3)
        row = next(iter(self.inspections.rows.values()))
        self.assertIsNone(row["rescue"]["tracking_error"])
        self.assertEqual(self.states["Drone1"]["mode"], "GUIDED")
        self.assertTrue(all(0.55 <= row["source_age_s"] <= 1 for row in self.dispatch_timings))
        intervals = [
            b["monotonic_s"] - a["monotonic_s"]
            for a, b in zip(self.dispatch_timings, self.dispatch_timings[1:])
        ]
        self.assertTrue(all(0.5 <= interval < 0.8 for interval in intervals), intervals)
        self.fleet.execute.assert_not_awaited()
        self.fleet.tracking_follow.assert_awaited_once()
        self.assertEqual(self.vision.call_count, 1)

    async def test_late_dispatch_completion_cannot_restore_a_track_after_watchdog_hold(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        entered, finish = asyncio.Event(), asyncio.Event()

        async def delayed(*args, **kwargs):
            entered.set()
            await finish.wait()

        self.fleet.tracking_target.side_effect = delayed
        try:
            await self.until(entered.is_set)
            await self.until(lambda: row["rescue"]["tracking_error"] is not None)
            finish.set()
            await asyncio.sleep(0.1)
            self.assertIsNone(row["rescue"]["track"])
            self.assertIsNone(row["rescue"]["live_frame"])
            self.assertEqual(row["rescue"]["phase"], "held")
            self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
            self.fleet.execute.assert_awaited_once()
        finally:
            finish.set()

    async def test_review_boxes_refresh_before_the_aircraft_pause_completes(self):
        self.decisions = ["recheck"]
        self.review_ready.set()
        entered, finish = asyncio.Event(), asyncio.Event()
        start = self.fleet.tracking_start.side_effect

        async def pause(*args):
            checkpoint = await start(*args)
            entered.set()
            await finish.wait()
            return checkpoint

        self.fleet.tracking_start.side_effect = pause
        try:
            await self.inspections.start_search(self.receipt)
            await asyncio.wait_for(entered.wait(), 1)
            row = next(iter(self.inspections.rows.values()))
            seed = row["latest"]["frame_id"]
            announcement = next(e for e in self.events if "pausing on visual track" in e["text"])
            self.assertEqual(announcement["result"]["rescue"]["live_frame"]["frame_id"], seed)
            self.assertEqual(announcement["result"]["rescue"]["selected_track_id"], 1)
            await self.until(
                lambda: (
                    row["status"] == "candidate" and row["rescue"]["live_frame"]["frame_id"] != seed
                )
            )
            self.assertEqual(row["rescue"]["phase"], "reviewing")
            frame = row["rescue"]["live_frame"]
            self.assertLess(time.time() * 1000 - frame["captured_at_ms"], 1000)
            self.assertTrue(frame["detections"][0]["mask"]["ref"].endswith("-mask.png"))
            self.assertEqual(row["latest"]["frame_id"], seed)
            self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
            self.fleet.tracking_follow.assert_not_awaited()
            self.fleet.tracking_target.assert_not_awaited()
        finally:
            finish.set()

    async def test_outside_subject_keeps_fresh_masks_and_rejects_unsafe_relock(self):
        self.decisions = ["recheck"]
        original = tracking.ground_observation
        patcher = patch.object(
            tracking,
            "ground_observation",
            side_effect=lambda *args: {
                **original(*args),
                "ground": {"north_m": 120, "east_m": 0, "ground_m": terrain_height(120, 0)},
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: row["rescue"]["held_frame"] is not None)
        reason = "Candidate left the authorized search perimeter"
        self.assertEqual(row["rescue"]["tracking_error"], reason)
        self.assertEqual(row["rescue"]["selected_track_id"], 1)
        first = row["rescue"]["held_frame"]["frame_id"]
        await self.until(lambda: row["rescue"]["held_frame"]["frame_id"] != first)
        frame = row["rescue"]["held_frame"]
        self.assertLess(time.time() * 1000 - frame["captured_at_ms"], 1000)
        mask = self.inspections.directory / Path(frame["detections"][0]["mask"]["ref"]).name
        self.assertGreater(np.count_nonzero(cv2.imread(str(mask), cv2.IMREAD_GRAYSCALE)), 0)
        self.review_ready.set()
        await self.until(lambda: row["status"] == "candidate")
        self.assertEqual(row["rescue"]["phase"], "held")
        self.assertEqual(row["rescue"]["reports"][0]["report"]["decision"], "recheck")
        observed = await self.inspections.get_candidate(row["id"])
        with self.assertRaisesRegex(AssertionError, "Re-lock failed: " + reason):
            await self.inspections.relock_candidate(row["id"], observed["frame"]["frame_id"], 0)
        self.assertIsNone(row["error"])
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.fleet.tracking_follow.assert_not_awaited()
        self.fleet.tracking_target.assert_not_awaited()
        self.fleet.tracking_resume.assert_not_awaited()
        self.tracks[0].close.assert_not_called()

        self.inspections.invalidate("Operator hold", "Drone1")
        with self.assertRaises(asyncio.CancelledError):
            await self.inspections.tasks["Drone1"]
        self.assertIsNone(row["rescue"]["held_frame"])

    async def test_negative_review_releases_an_outside_subject_through_its_checkpoint(self):
        original = tracking.ground_observation
        patcher = patch.object(
            tracking,
            "ground_observation",
            side_effect=lambda *args: {
                **original(*args),
                "ground": {"north_m": 120, "east_m": 0, "ground_m": terrain_height(120, 0)},
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: row["rescue"]["held_frame"] is not None)
        self.assertEqual(
            row["rescue"]["tracking_error"], "Candidate left the authorized search perimeter"
        )
        self.review_ready.set()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.fleet.tracking_resume.assert_awaited_once()
        self.fleet.tracking_follow.assert_not_awaited()
        self.fleet.tracking_target.assert_not_awaited()

    async def test_slow_live_publication_does_not_delay_fresh_flight_commands(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        publish = self.inspections._publish
        commands = []

        async def delayed(*args, live=False, held=False):
            if live:
                entered.set()
                await finish.wait()
            return await publish(*args, live=live, held=held)

        async def dispatch(*args, captured_at_ms):
            commands.append(time.monotonic())
            self.assertLessEqual(time.time() * 1000 - captured_at_ms, 1000)

        self.fleet.tracking_target.side_effect = dispatch
        with patch.object(self.inspections, "_publish", side_effect=delayed):
            try:
                await self.inspections.start_search(self.receipt)
                await self.until(entered.is_set)
                count = len(commands)
                await asyncio.sleep(1.1)
                self.assertGreaterEqual(len(commands), count + 2)
                row = next(iter(self.inspections.rows.values()))
                self.assertIsNone(row["rescue"]["tracking_error"])
                self.assertLess(time.time() * 1000 - row["rescue"]["track"]["captured_at_ms"], 1000)
                self.fleet.execute.assert_not_awaited()
                record = next((self.directory / "inspections" / row["id"]).glob("*-tracking.jsonl"))
                dispatches = [
                    r
                    for r in (json.loads(line) for line in record.read_text().splitlines())
                    if "command_at_ms" in r
                ]
                self.assertEqual(row["rescue"]["live_frame"]["frame_id"], row["latest"]["frame_id"])
                self.assertGreater(
                    time.time() * 1000 - row["rescue"]["live_frame"]["captured_at_ms"], 1000
                )
                self.assertTrue(all(0 <= r["source_age_s"] <= 1 for r in dispatches))
                self.assertTrue(all(0 < r["command_interval_s"] < 1 for r in dispatches[1:]))
                self.inspections.invalidate("Operator hold", "Drone1")
                with self.assertRaises(asyncio.CancelledError):
                    await self.inspections.tasks["Drone1"]
                finish.set()
                await asyncio.sleep(0)
                self.assertIsNone(row["rescue"]["live_frame"])
            finally:
                finish.set()
                self.review_ready.set()

    async def test_failed_live_publication_stops_tracking_and_reports_failure(self):
        publish = self.inspections._publish

        async def failed(*args, live=False, held=False):
            if live:
                raise OSError("Evidence disk unavailable")
            return await publish(*args, live=live, held=held)

        with patch.object(self.inspections, "_publish", side_effect=failed):
            try:
                await self.inspections.start_search(self.receipt)
                with self.assertRaisesRegex(RuntimeError, "Evidence disk unavailable"):
                    await asyncio.wait_for(self.inspections.tasks["Drone1"], 3)
            finally:
                self.review_ready.set()
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "failed")
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.assertIsNone(row["rescue"]["live_frame"])
        self.fleet.execute.assert_awaited_once()
        self.assertEqual(self.vision.call_count, 1)
        self.tracks[0].close.assert_called_once()

    async def test_operator_override_releases_authority_before_pending_remote_cleanup(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        entered, finish = threading.Event(), threading.Event()
        track = self.tracks[0]
        advance = track.advance.side_effect

        def delayed(*args):
            entered.set()
            assert finish.wait(3), "Test did not release cancelled detector request"
            return advance(*args)

        track.advance.side_effect = delayed
        try:
            await self.until(entered.is_set)
            self.inspections.invalidate("Operator return_home", "Drone1")
            await asyncio.wait_for(self.inspections.flight_stopped("Drone1"), 0.2)
            self.states["Drone1"]["mode"] = "RTL"
            commands = self.fleet.tracking_target.await_count
            track.close.assert_not_called()
            self.assertFalse(self.inspections.tasks["Drone1"].done())
        finally:
            finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.inspections.tasks["Drone1"]
        track.close.assert_called_once()
        self.assertEqual(self.fleet.tracking_target.await_count, commands)
        self.assertEqual(self.states["Drone1"]["mode"], "RTL")

    async def test_requested_live_evidence_remains_exact_after_new_frames_and_camera_change(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        original = copy.deepcopy(row["latest"])
        result = await self.inspections.get_candidate(row["id"])
        frame = result["frame"]
        evidence = self.inspections.evidence(row["id"], frame["frame_id"])
        await self.until(lambda: row["rescue"]["live_frame"]["frame_id"] != frame["frame_id"])
        self.inspections.camera_views["Drone1"]["revision"] += 1
        self.inspections.publish_evidence(
            row["id"], frame["frame_id"], 0, "observed", "Person spotted", "Person beside the trail"
        )
        published = self.events[-1]["result"]
        self.assertEqual(published["latest"], frame)
        self.assertEqual(published["rescue"]["reports"], [])
        self.assertIsNone(published["rescue"]["track"])
        self.assertEqual(row["latest"], original)
        self.assertEqual(self.inspections.evidence(row["id"], frame["frame_id"]), evidence)

    async def test_manual_view_cannot_interrupt_healthy_candidate_tracking(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        target = copy.deepcopy(row["rescue"]["track"]["ground"])
        view = copy.deepcopy(self.inspections.camera_views["Drone1"])
        frame = copy.deepcopy(row["rescue"]["live_frame"])
        commands = self.fleet.tracking_target.await_count
        views = self.capture.view.await_count
        with patch.object(
            self.inspections.visual, "cancel", wraps=self.inspections.visual.cancel
        ) as cancel:
            with self.assertRaisesRegex(
                AssertionError, "Automatic candidate tracking owns the camera"
            ):
                await self.inspections.set_camera_view(
                    "Drone1", target["north_m"], target["east_m"], 0
                )
            cancel.assert_not_called()
        self.assertEqual(self.inspections.camera_views["Drone1"], view)
        self.assertEqual(row["rescue"]["live_frame"], frame)
        self.assertEqual(self.capture.view.await_count, views)
        self.assertEqual(self.vision.call_count, 1)
        self.tracks[0].close.assert_not_called()
        await self.until(lambda: self.fleet.tracking_target.await_count > commands)
        observed = await self.inspections.get_candidate(row["id"])
        self.assertEqual(observed["frame"]["camera_view_revision"], view["revision"])
        self.assertIsNone(row["rescue"]["tracking_error"])

    async def test_candidate_release_returns_camera_ownership_to_route_search(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        resume = self.fleet.tracking_resume.side_effect

        async def continue_step(*args):
            await resume(*args)
            self.states["Drone1"]["mission_current"] = 1

        self.fleet.tracking_resume.side_effect = continue_step
        self.capture.visual_controlled = True
        views = self.capture.view.await_count
        self.inspections.disregard_and_continue(row["rescue"]["review_id"], "Candidate excluded")
        await self.until(lambda: self.capture.view.await_count > views)
        self.assertFalse(self.capture.visual_controlled)
        self.assertEqual(row["camera_view"]["horizontal_fov_deg"], 90)
        self.assertEqual(row["rescue"]["phase"], "searching")
        self.assertIsNone(row["rescue"]["selected_track_id"])
        self.states["Drone1"]["mission_current"] = 3
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")

    async def test_hold_immediately_revokes_live_evidence_and_camera_authority(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        selected = (await self.inspections.get_candidate(row["id"]))["frame"]
        original = copy.deepcopy(row["latest"])
        views = self.capture.view.await_count
        self.inspections.invalidate("Operator hold", "Drone1")
        with self.assertRaisesRegex(AssertionError, "no longer owns"):
            await self.inspections.get_candidate(row["id"])
        self.assertEqual(self.capture.view.await_count, views)
        self.assertIsNone(row["rescue"]["live_frame"])
        self.assertIsNone(row["rescue"]["track"])
        self.assertEqual(row["latest"], original)
        self.inspections.publish_evidence(
            row["id"],
            selected["frame_id"],
            0,
            "observed",
            "Person spotted",
            "Previously observed person",
        )
        self.assertEqual(self.events[-1]["result"]["latest"], selected)

    async def test_candidate_read_waits_for_existing_stream_to_refresh_age_and_view(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        old_identity = row["rescue"]["live_frame"]["frame_id"]
        row["rescue"]["live_frame"]["captured_at_ms"] -= 2000
        self.inspections.camera_views["Drone1"]["revision"] = 2
        acquire = self.capture.capture.side_effect

        async def capture():
            metadata, jpeg = await acquire()
            metadata["camera_view_revision"] = 2
            return metadata, jpeg

        self.capture.capture.side_effect = capture
        reading = asyncio.create_task(self.inspections.get_candidate(row["id"]))
        await asyncio.sleep(0)
        self.assertFalse(reading.done())
        result = await asyncio.wait_for(reading, 1)
        self.assertNotEqual(result["frame"]["frame_id"], old_identity)
        self.assertEqual(result["frame"]["camera_view_revision"], 2)
        self.assertLess(time.time() * 1000 - result["frame"]["captured_at_ms"], 1000)
        self.assertGreaterEqual(self.detect.call_count, 3)
        self.assertEqual(self.vision.call_count, 1)

    async def test_candidate_read_wait_stops_immediately_when_hold_revokes_owner(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        row["rescue"]["live_frame"]["captured_at_ms"] -= 2000
        reading = asyncio.create_task(self.inspections.get_candidate(row["id"]))
        await asyncio.sleep(0)
        self.assertFalse(reading.done())
        self.inspections.invalidate("Operator hold", "Drone1")
        with self.assertRaisesRegex(AssertionError, "no longer owns"):
            await asyncio.wait_for(reading, 0.2)
        self.assertGreaterEqual(self.detect.call_count, 3)
        self.assertEqual(self.vision.call_count, 1)

    async def test_delayed_publication_cannot_restore_invalidated_live_observation(self):
        self.inspections.directory.mkdir()
        for invalidation in ("lost", "revision", "stopped", "stale", "released"):
            with self.subTest(invalidation=invalidation):
                metadata, image = await self.capture.capture()
                metadata.update(
                    frame_id="interleaving-" + invalidation,
                    sha256=hashlib.sha256(image).hexdigest(),
                )
                await self.inspections._save_frame(metadata, image)
                row = {
                    "drone_id": "Drone1",
                    "plan_version": 1,
                    "status": "candidate",
                    "rescue": {
                        "phase": "tracking",
                        "live_frame": None,
                        "tracking_error": None,
                        "review_id": "publication-test",
                    },
                }
                self.inspections.releases["publication-test"] = {"reason": None}
                self.inspections.camera_views["Drone1"] = {"revision": 1}
                if invalidation == "lost":
                    row["rescue"]["tracking_error"] = "Candidate observation became stale"
                elif invalidation == "revision":
                    self.inspections.camera_views["Drone1"] = {"revision": 2}
                elif invalidation == "stopped":
                    row["status"] = "stopped"
                elif invalidation == "released":
                    self.inspections.releases["publication-test"]["reason"] = "Discarded"
                else:
                    metadata["captured_at_ms"] -= 2000
                if invalidation == "stopped":
                    with self.assertRaisesRegex(AssertionError, "no longer owns"):
                        await self.inspections._publish(row, metadata, [], live=True)
                else:
                    await self.inspections._publish(row, metadata, [], live=True)
                self.assertIsNone(row["rescue"]["live_frame"])

    async def test_new_inspection_or_plan_version_cannot_reset_the_flight_allowance(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"] and bool(row["rescue"]["reports"]))
        self.inspections.invalidate("Operator hold", "Drone1")
        with self.assertRaises(asyncio.CancelledError):
            await self.inspections.tasks["Drone1"]
        spent = self.inspections.lock_seconds["Drone1"]
        self.assertGreater(spent, 0)
        self.states["Drone1"]["mode"] = "BRAKE"
        started = await self.inspections.start("Drone1", "person", 0.3, 2)
        await self.inspections.tasks["Drone1"]
        candidate = self.inspections.rows[started["id"]]
        with self.assertRaisesRegex(AssertionError, "active observed plan step"):
            await self.inspections.follow_candidate(
                candidate["id"], candidate["latest"]["frame_id"], 0, 10
            )
        self.assertEqual(self.inspections.lock_seconds["Drone1"], spent)
        self.fleet.tracking_start.assert_awaited_once()
        self.assertNotIn("rescue", candidate)
        policy = self.receipt["plan"]["search"]
        self.assertEqual(
            self.inspections._remaining_lock({"plan_version": 2, "drone_id": "Drone1"}, policy),
            10 - spent,
        )
        self.assertEqual(
            self.inspections._remaining_lock({"plan_version": 1, "drone_id": "Drone2"}, policy), 10
        )
        self.inspections.reset_lock({"Drone1"})
        self.assertEqual(
            self.inspections._remaining_lock({"plan_version": 2, "drone_id": "Drone1"}, policy), 10
        )

    async def test_automatic_candidate_receives_only_shared_remaining_allowance(self):
        self.inspections.lock_seconds["Drone1"] = 7
        row = await self.begin()
        self.assertEqual(row["plan_version"], 1)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertGreater(self.inspections.lock_seconds["Drone1"], 7)
        self.assertLess(self.inspections.lock_seconds["Drone1"], 10)
        record = next((self.directory / "inspections" / row["id"]).glob("*-tracking.jsonl"))
        first = json.loads(record.read_text().splitlines()[0])
        self.assertEqual(first["allowance_s"], 3)

    async def test_slow_negative_discovery_captures_only_its_observation(self):
        def sam(*args):
            time.sleep(1.5)
            self.states["Drone1"]["mission_current"] = 3
            return {"width": 3840, "height": 2160, "detections": []}

        self.detect.side_effect = sam
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "complete")
        self.assertEqual(self.capture_count, 1)
        self.assertIsNone(row["error"])
        self.vision.assert_called_once()
        self.fleet.tracking_start.assert_not_awaited()
        self.fleet.execute.assert_not_awaited()

    async def test_failed_session_close_is_reported_once_without_retry(self):
        row = await self.begin()
        track = self.tracks[0]

        def failed():
            track.status = "closed"
            raise TimeoutError("SAM 3.1 detector close timed out")

        track.close.side_effect = failed
        with self.assertRaisesRegex(RuntimeError, "SAM 3.1 detector close timed out"):
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        track.close.assert_called_once()
        self.assertEqual(row["status"], "failed")

    async def test_failed_close_is_terminal_for_outer_cleanup_without_another_request(self):
        track = SimpleNamespace(status="tracking", http=Mock())

        def failed():
            track.status = "closed"
            raise TimeoutError("SAM 3.1 detector close timed out")

        track.close = Mock(side_effect=failed)
        with self.assertRaisesRegex(TimeoutError, "SAM 3.1 detector close timed out"):
            await close_track(track)
        await close_track(track)
        track.close.assert_called_once()
        track.http.close.assert_called_once()

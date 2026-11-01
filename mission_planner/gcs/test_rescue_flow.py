"""Image-driven search interruptions through review, continuation and operator cancellation."""

import asyncio
import copy
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

from mission_planner.agents.segmentation import encode_mask
from mission_planner.environment import ROOT
from mission_planner.gcs.geodesy import NAMES, ORIGIN, point_in_polygon
from mission_planner.gcs.inspection import Inspections, close_track
from mission_planner.gcs.planner import compile_plan, mission_item
from mission_planner.gcs.test_planner import airborne, search_plan
from mission_planner.gcs.test_validation import readback
from mission_planner.gcs.tracking import ground_observation
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
        self.frame = np.full((1080, 1920, 3), 45, np.uint8)
        texture = np.random.default_rng(92).integers(30, 240, (220, 120, 3), np.uint8)
        self.frame[400:620, 800:920] = texture
        self.frame[430:600, 1200:1280] = texture[:170, :80]
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
                    "zoom": 2,
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
            displacement = (self.capture_count - 1) * 2
            moved = cv2.warpAffine(
                self.frame,
                np.array([[1, 0, displacement], [0, 1, 0]], np.float32),
                (1920, 1080),
                borderValue=(45, 45, 45),
            )
            ok, encoded = cv2.imencode(".jpg", moved, [cv2.IMWRITE_JPEG_QUALITY, 95])
            assert ok
            return {
                "drone_id": "Drone1",
                "camera_id": "fpv",
                "horizontal_fov_deg": 53.13010235415598,
                "camera_view_revision": 1,
                "camera_position_world_m": {
                    "x": self.states["Drone1"]["north_m"],
                    "y": self.states["Drone1"]["east_m"],
                    "z": terrain_height(0, 0) + 35,
                },
                "camera_pose_local_ned": {
                    "orientation": {"x": 0, "y": -0.5, "z": 0, "w": 3**0.5 / 2}
                },
                "width": 1920,
                "height": 1080,
                "timestamp_ns": self.capture_count * 200_000_000,
                "captured_at_ms": time.time() * 1000,
            }, encoded.tobytes()

        self.capture = SimpleNamespace(
            capture=AsyncMock(side_effect=capture),
            aim=AsyncMock(),
            view=AsyncMock(
                side_effect=lambda target, zoom: {
                    "target": target,
                    "zoom": zoom,
                    "horizontal_fov_deg": 53.13010235415598,
                    "revision": 1,
                }
            ),
            reset=AsyncMock(),
            close=AsyncMock(),
        )

        def sam(client, path, body, timing):
            boxes = [[800, 400, 920, 620], [1200, 430, 1280, 600]][: len(self.decisions)]
            return {
                "width": 1920,
                "height": 1080,
                "detections": {
                    "person": [
                        {
                            "box_xyxy": box,
                            "confidence": 0.8,
                            "mask_area_px": (box[2] - box[0]) * (box[3] - box[1]),
                            "mask": encode_mask(
                                np.ones((box[3] - box[1], box[2] - box[0]), bool), box[:2]
                            ),
                        }
                        for box in boxes
                    ]
                },
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

        self.sam = Mock(side_effect=sam)
        self.glimmer = Mock(return_value=SimpleNamespace(http=Mock(), report=report))
        self.track_error = None
        self.tracks = []

        def make_track(http):
            track = SimpleNamespace(http=http, timestamp_ns=0, status="new")

            def start(jpeg, timestamp_ns, detection, threshold):
                track.timestamp_ns = timestamp_ns
                track.status = "seeded"
                track.seed = copy.deepcopy(detection)
                return {"status": "seeded", "detection": None, "error": None}

            def advance(jpeg, timestamp_ns):
                assert timestamp_ns > track.timestamp_ns
                track.timestamp_ns = timestamp_ns
                if self.track_error is not None:
                    track.status = "lost"
                    return {"status": "lost", "detection": None, "error": self.track_error}
                shift = round(timestamp_ns / 200_000_000 - 1) * 2
                detection = copy.deepcopy(track.seed)
                box = detection["box_xyxy"]
                box[0] += shift
                box[2] += shift
                detection["mask"] = encode_mask(
                    np.ones((box[3] - box[1], box[2] - box[0]), bool), box[:2]
                )
                track.status = "tracking"
                return {"status": "tracking", "detection": detection, "error": None}

            def close():
                assert track.status != "closed", "Test temporal client was closed twice"
                track.status = "closed"

            track.start, track.advance, track.close = (
                Mock(side_effect=start),
                Mock(side_effect=advance),
                Mock(side_effect=close),
            )
            self.tracks.append(track)
            return track

        self.temporal = Mock(side_effect=make_track)
        for name, replacement in (
            ("mission_planner.gcs.inspection.ForwardCapture", Mock(return_value=self.capture)),
            ("mission_planner.gcs.inspection.endpoint", Mock(return_value=Mock())),
            ("mission_planner.gcs.inspection.post", self.sam),
            ("mission_planner.gcs.inspection.Glimmer", self.glimmer),
            ("mission_planner.gcs.inspection.TemporalTrack", self.temporal),
            ("mission_planner.gcs.inspection.sampling_start", lambda name, now: now),
            (
                "mission_planner.gcs.tracking.ground_point",
                lambda metadata, box: {
                    "north_m": 30 + (box[0] - 800) / 10,
                    "east_m": 0,
                    "ground_m": terrain_height(30, 0),
                },
            ),
        ):
            patcher = patch(name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.inspections = Inspections(self.mission)

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
        self.assertGreater(points[-1], points[0])
        self.assertEqual(self.states["Drone2"]["mode"], "AUTO")
        self.review_ready.set()
        return next(iter(self.inspections.rows.values()))

    async def test_unobserved_pattern_starts_no_capture_or_models(self):
        del self.receipt["plan"]["drones"][0]["steps"][0]["observe"]
        with patch("mission_planner.gcs.inspection.endpoint") as client:
            self.assertEqual(await self.inspections.start_search(self.receipt), [])
            client.assert_not_called()
        self.assertFalse(self.inspections.tasks)
        self.assertFalse(self.inspections.cameras)
        self.capture.capture.assert_not_awaited()
        self.sam.assert_not_called()
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
        self.assertEqual(self.sam.call_count, 1)
        self.assertEqual(self.review_count, 1)
        self.fleet.tracking_resume.assert_awaited_once()
        target, zoom = self.capture.view.await_args_list[0].args
        self.assertAlmostEqual(target["x"], 20)
        self.assertAlmostEqual(target["y"], 0)
        self.assertAlmostEqual(target["z"], terrain_height(20, 0))
        self.assertEqual(zoom, 2)

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
        self.assertEqual(self.sam.call_count, 1)
        self.assertEqual(self.review_count, 1)
        self.fleet.tracking_resume.assert_awaited_once()
        target, zoom = self.capture.view.await_args_list[0].args
        self.assertEqual(target, {"x": 20, "y": 40, "z": terrain_height(20, 40)})
        self.assertEqual(zoom, 2)

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
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        self.sam.side_effect = sam
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 1))
        target, zoom = self.capture.view.await_args_list[0].args
        self.assertEqual(target, {"x": 0, "y": 0, "z": terrain_height(0, 0)})
        self.assertEqual(zoom, 2)
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
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        self.sam.side_effect = sam
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 1))
        target, zoom = self.capture.view.await_args_list[0].args
        self.assertAlmostEqual(target["x"], -720, delta=0.02)
        self.assertAlmostEqual(target["y"], 0, delta=0.02)
        self.assertEqual(target["z"], terrain_height(target["x"], target["y"]))
        self.assertEqual(zoom, 2)
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
        self.sam.assert_not_called()
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
        self.sam.assert_not_called()
        self.fleet.tracking_start.assert_not_awaited()
        self.fleet.execute.assert_not_awaited()
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertEqual(record["skipped"], "selected observation waypoint is not current")
        self.assertEqual(record["step_index"], 0)
        self.assertIn("camera_position_world_m", record)
        self.assertIsNone(record["response"])

    async def test_capture_pose_outside_observation_area_is_not_counted_or_inferred(self):
        capture = self.capture.capture.side_effect
        event = self.mission.event.side_effect

        async def outside_capture():
            metadata, jpeg = await capture()
            metadata["camera_position_world_m"]["x"] = 200
            return metadata, jpeg

        def finish_after_skip(kind, text, **fields):
            if "captured camera position is outside" in text:
                self.states["Drone1"]["mission_current"] = 3
            return event(kind, text, **fields)

        self.capture.capture.side_effect = outside_capture
        self.mission.event.side_effect = finish_after_skip
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 0))
        self.sam.assert_not_called()
        self.fleet.execute.assert_not_awaited()
        record = json.loads(
            next((self.directory / "inspections" / row["id"]).glob("*.json")).read_text()
        )
        self.assertEqual(
            record["skipped"], "captured camera position is outside its observation area"
        )
        self.assertEqual(record["camera_position_world_m"]["x"], 200)
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
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        self.sam.side_effect = sam
        with patch.object(
            self.inspections, "_search_state", wraps=self.inspections._search_state
        ) as state:
            await self.inspections.start_search(self.receipt)
            await self.until(lambda: state.called)
            row = next(iter(self.inspections.rows.values()))
            self.assertEqual(row["rescue"]["step_index"], 1)
            self.assertEqual(row["rescue"]["phase"], "waiting")
            self.capture.capture.assert_not_awaited()
            self.sam.assert_not_called()
            self.states["Drone1"].update(mission_current=2, north_m=20)
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["rescue"]["completed_steps"], [{"step_index": 1, "frames": 1}])
        self.assertEqual(self.sam.call_count, 1)
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
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        self.sam.side_effect = sam
        with patch(
            "mission_planner.gcs.inspection.point_in_polygon", wraps=point_in_polygon
        ) as inside:
            await self.inspections.start_search(self.receipt)
            await self.until(lambda: inside.called)
            self.capture.capture.assert_not_awaited()
            self.sam.assert_not_called()
            self.states["Drone1"]["north_m"] = -50
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(self.sam.call_count, 1)
        self.fleet.execute.assert_not_awaited()

    async def test_explicit_follow_cannot_bind_a_future_selected_step(self):
        await self.inspections.set_camera_view("Drone1", 30, 0, 0, 2)
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
                with self.assertRaisesRegex(AssertionError, "current observed waypoint step"):
                    await self.inspections.follow_candidate(
                        row["id"], row["latest"]["frame_id"], 0, 6
                    )
                self.assertNotIn("rescue", row)
        self.states["Drone1"]["mission_current"] = 2
        self.receipt["missions"]["Drone1"]["items"][2]["command"] = 19
        with self.assertRaisesRegex(AssertionError, "current observed waypoint step"):
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

    async def test_candidate_camera_centres_while_braking_before_temporal_seed(self):
        stopped = asyncio.Event()
        seeded = threading.Event()
        target = {"north_m": 30.0, "east_m": 10.0, "ground_m": 12.0}
        create = self.temporal.side_effect

        async def braking(name, sequences):
            self.states[name]["mode"] = "BRAKE"
            await stopped.wait()
            return copy.deepcopy(self.checkpoint)

        def tracked(http):
            track = create(http)
            start = track.start.side_effect

            def seed(*args):
                self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
                self.assertFalse(stopped.is_set())
                self.capture.aim.assert_awaited_with({"x": 30.0, "y": 10.0, "z": 12.0})
                seeded.set()
                return start(*args)

            track.start.side_effect = seed
            return track

        self.fleet.tracking_start.side_effect = braking
        self.temporal.side_effect = tracked
        with patch(
            "mission_planner.gcs.tracking.ground_observation",
            return_value={"ground": target, "location_error": None},
        ):
            try:
                await self.inspections.start_search(self.receipt)
                await self.until(seeded.is_set)
                self.fleet.tracking_follow.assert_not_awaited()
            finally:
                stopped.set()
                self.review_ready.set()

    async def test_candidate_camera_does_not_replace_a_new_view_after_projection(self):
        entered, finish = threading.Event(), threading.Event()

        def project(*args):
            entered.set()
            assert finish.wait(3), "Test did not release candidate projection"
            return {
                "ground": {"north_m": 30.0, "east_m": 10.0, "ground_m": 12.0},
                "location_error": None,
            }

        with patch("mission_planner.gcs.tracking.ground_observation", side_effect=project):
            try:
                await self.inspections.start_search(self.receipt)
                await self.until(entered.is_set)
                self.capture.view.side_effect = lambda target, zoom: {
                    "target": target,
                    "zoom": zoom,
                    "horizontal_fov_deg": 28.07,
                    "revision": 2,
                }
                await self.inspections.set_camera_view("Drone1", 40, 20, 0, 4)
                finish.set()
                await self.until(lambda: self.tracks and self.tracks[0].start.called)
                self.capture.aim.assert_not_awaited()
                self.assertEqual(self.inspections.camera_views["Drone1"]["revision"], 2)
                self.inspections.invalidate("Operator hold", "Drone1")
            finally:
                finish.set()
                self.review_ready.set()

    async def test_override_during_ground_projection_prevents_new_review_and_resume(self):
        entered, release = threading.Event(), threading.Event()
        report = Mock(wraps=self.glimmer.return_value.report)
        self.glimmer.return_value.report = report

        def project(metadata, box):
            entered.set()
            assert release.wait(5), "Test did not release its location calculation"
            return {"ground": None, "location_error": "Ground contact is obscured"}

        with patch("mission_planner.gcs.tracking.ground_observation", side_effect=project):
            try:
                await self.inspections.start_search(self.receipt)
                await self.until(lambda: entered.is_set())
                (review,) = self.inspections.reviews
                self.inspections.invalidate("Operator return_home", "Drone1")
                await asyncio.wait_for(self.inspections.flight_stopped("Drone1"), 1)
                self.states["Drone1"]["mode"] = "RTL"
                release.set()
                with self.assertRaisesRegex(AssertionError, "review authority was revoked"):
                    await asyncio.wait_for(review, 1)
                with self.assertRaises(asyncio.CancelledError):
                    await self.inspections.tasks["Drone1"]
            finally:
                release.set()
                self.review_ready.set()
        report.assert_not_called()
        self.assertEqual(self.review_count, 0)
        self.glimmer.return_value.http.close.assert_called_once()
        self.fleet.tracking_resume.assert_not_awaited()
        self.assertFalse(any(event["kind"] == "review" for event in self.events))
        self.assertEqual(self.states["Drone1"]["mode"], "RTL")

    async def test_negative_resumes_original_survey_without_candidate_wake(self):
        row = await self.begin()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.fleet.tracking_resume.assert_awaited_once()
        self.assertFalse(self.fleet.execute.called)
        self.assertEqual(self.sam.call_count, 1)
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
        for kind in ("crop", "context"):
            name = report["images"][kind + "_ref"].rsplit("/", 1)[1]
            self.assertIn(name, self.inspections.assets)
            self.assertTrue((self.directory / "evidence" / name).is_file())
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
            self.sam.assert_not_called()
            self.fleet.tracking_start.assert_not_awaited()
            self.fleet.execute.assert_not_awaited()
            row = next(iter(self.inspections.rows.values()))
            self.assertEqual(row["rescue"]["phase"], "waiting")
            with self.assertRaisesRegex(AssertionError, "observation step owns its camera"):
                await self.inspections.set_camera_view("Drone1", 30, 0, 0, 4)
            self.capture.view.assert_not_awaited()
            self.states["Drone1"]["north_m"] = 0
            await self.until(lambda: self.fleet.tracking_target.await_count >= 2)
            self.review_ready.set()
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.assertEqual(self.sam.call_count, 1)
        self.assertEqual(self.states["Drone1"]["mission_current"], 3)
        self.fleet.execute.assert_not_awaited()

    async def test_uncertain_review_preserves_lock_and_evidence_until_wonder_releases(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"])
        self.assertEqual(row["status"], "candidate")
        self.assertEqual(row["rescue"]["track"]["candidate_index"], 0)
        self.assertFalse(self.fleet.tracking_resume.called)
        target = row["rescue"]["track"]["ground"]
        await self.inspections.set_camera_view("Drone1", target["north_m"], target["east_m"], 0, 4)
        self.assertEqual(self.temporal.call_count, 1)
        evidence = self.inspections.evidence(row["id"])
        self.assertEqual(evidence["metadata"]["timestamp_ns"], 200_000_000)
        self.assertEqual(len(evidence["images"]), 4)
        images = row["rescue"]["reports"][0]["images"]
        registry = json.loads((self.directory / "evidence/assets.json").read_text())
        for offset, kind in enumerate(("crop", "context"), 2):
            name = images[kind + "_ref"].rsplit("/", 1)[1]
            self.assertIn(name, registry)
            original = (self.directory / "inspections" / row["id"] / name).read_bytes()
            self.assertEqual((self.directory / "evidence" / name).read_bytes(), original)
            self.assertEqual(evidence["images"][offset], original)
            self.assertEqual(
                evidence["image_order"][offset],
                {
                    "kind": "Glimmer " + kind,
                    "candidate_index": 0,
                    "frame_id": images["frame_id"],
                    "source_box_xyxy": images[kind + "_box"],
                },
            )
        self.inspections.disregard_and_continue(
            row["rescue"]["review_id"], "Operator clarified clothing", call_id="wonder-release-1"
        )
        with self.assertRaisesRegex(AssertionError, "candidate has been released"):
            await self.inspections.set_camera_view("Drone1", 30, 0, 0, 4)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.fleet.tracking_resume.assert_awaited_once()
        (result,) = [event for event in self.events if event["kind"] == "tool_result"]
        self.assertEqual(result["call_id"], "wonder-release-1")
        self.assertEqual(result["source"], "agent")
        self.assertTrue(result["result"]["ok"])
        self.assertFalse(any(event["kind"] == "tool_call" for event in self.events))

    async def test_one_negative_cannot_release_other_positive_candidate_or_switch_tracks(self):
        self.decisions = ["discard", "escalate"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"])
        self.assertEqual(
            [report["report"]["decision"] for report in row["rescue"]["reports"]], self.decisions
        )
        self.assertEqual(row["rescue"]["track"]["candidate_index"], 0)
        self.assertFalse(self.fleet.tracking_resume.called)
        with self.assertRaisesRegex(AssertionError, "positive or unresolved"):
            self.inspections.disregard_and_continue(
                row["rescue"]["review_id"], "Only first rejected", "glimmer"
            )

    async def test_operator_override_revokes_late_release_and_stops_target_updates(self):
        self.decisions = ["escalate"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"])
        review_id = row["rescue"]["review_id"]
        self.inspections.invalidate("Operator return_home", "Drone1")
        await self.inspections.flight_stopped("Drone1")
        self.states["Drone1"]["mode"] = "RTL"
        commands = self.fleet.tracking_target.await_count
        await self.inspections.close()
        self.assertEqual(self.fleet.tracking_target.await_count, commands)
        self.assertEqual(self.states["Drone2"]["mode"], "AUTO")
        with self.assertRaisesRegex(AssertionError, "no longer available"):
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
        detection = {"box_xyxy": [800, 400, 920, 620]}
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
        self.assertIn("could not locate", row["rescue"]["tracking_error"])
        latest = row["latest"]
        metadata = self.inspections.metadata[latest["frame_id"]]
        (detection,) = latest["detections"]
        observation = ground_observation(metadata, detection["box_xyxy"])
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
        self.assertEqual(self.sam.call_count, 1)
        self.fleet.tracking_target.assert_not_awaited()

    async def test_untrackable_uncertain_candidate_stays_held_and_notifies_wonder(self):
        self.track_error = "Temporal tracker could not locate the selected object"
        self.decisions = ["recheck"]
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: row["rescue"]["tracking_error"] is not None)
        self.review_ready.set()
        await self.until(lambda: row["rescue"]["pending"])
        self.assertEqual(row["status"], "candidate")
        self.assertEqual(row["rescue"]["reports"][0]["report"]["decision"], "recheck")
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.fleet.tracking_follow.assert_not_awaited()
        self.fleet.tracking_resume.assert_not_awaited()
        view = await self.inspections.set_camera_view("Drone1", 30, 0, 0, 4)
        self.assertEqual(view["zoom"], 4)
        self.assertEqual(row["camera_view"], self.inspections.camera_views["Drone1"])
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.fleet.execute.assert_not_awaited()
        self.fleet.tracking_target.assert_not_awaited()

    async def test_failed_camera_view_requires_explicit_verification_before_release(self):
        self.track_error = "Temporal tracker could not locate the selected object"
        self.decisions = ["recheck"]
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: row["rescue"]["tracking_error"] is not None)
        self.review_ready.set()
        await self.until(lambda: row["rescue"]["pending"])
        view = self.capture.view.side_effect
        self.capture.view.side_effect = TimeoutError("Camera FOV readback failed")
        with self.assertRaisesRegex(TimeoutError, "Camera FOV readback failed"):
            await self.inspections.set_camera_view("Drone1", 30, 0, 0, 4)
        self.assertIsNone(self.inspections.camera_views["Drone1"])
        self.assertTrue(row["camera_changing"])
        self.assertEqual(row["camera_view"]["zoom"], 2)
        review_id = row["rescue"]["review_id"]
        with self.assertRaisesRegex(AssertionError, "explicitly set and verify a camera view"):
            self.inspections.disregard_and_continue(review_id, "Candidate excluded")
        self.assertIsNone(self.inspections.releases[review_id]["reason"])
        self.fleet.tracking_resume.assert_not_awaited()
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.capture.view.side_effect = view
        await self.inspections.set_camera_view("Drone1", 30, 0, 0, 2)
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
            if self.temporal.called:
                raise TimeoutError("Tracking camera RPC timed out")
            return await acquire()

        self.capture.capture.side_effect = capture
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(RuntimeError, "Tracking camera RPC timed out"):
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "failed")
        self.assertFalse(row["rescue"]["reports"])
        self.assertGreater(self.inspections.lock_seconds[(1, "Drone1")], 0)
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
            if any(name.endswith("-crop.jpg") for name in names):
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
        self.assertEqual(self.review_count, 1)
        self.fleet.tracking_resume.assert_not_awaited()

    async def test_stale_sam_evidence_cannot_start_following(self):
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
            if self.sam.call_count == 2:
                self.states["Drone1"]["mission_current"] = 3
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        self.sam.side_effect = sam
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 6)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 2))
        self.assertEqual(row["sampling"]["missed_slots"], 2)
        self.assertTrue(row["rescue"]["pending"])
        self.assertIsNone(row["error"])
        self.fleet.execute.assert_not_awaited()
        self.fleet.tracking_start.assert_not_awaited()
        self.glimmer.assert_not_called()

    async def test_multiple_remaining_surveys_sample_only_their_own_steps_and_keep_coverage(self):
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
                mission_current=3 if self.sam.call_count == 1 else 6, north_m=150
            )
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        self.sam.side_effect = sam
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
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        self.capture.view.side_effect = view
        self.sam.side_effect = sam
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
        self.assertEqual(row["sampling"]["missed_slots"], 1)
        self.fleet.execute.assert_not_awaited()

    async def test_real_sam_failure_holds_only_affected_drone_and_publishes_pending_error(self):
        self.sam.side_effect = TimeoutError("SAM request timed out")
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(RuntimeError, "SAM request timed out"):
            await self.inspections.tasks["Drone1"]
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["error"]), ("failed", "SAM request timed out"))
        self.assertTrue(row["rescue"]["pending"])
        self.fleet.execute.assert_awaited_once_with(
            "hold",
            {"drone_id": "Drone1"},
            expected_mode="AUTO",
            expected_step=(self.receipt["missions"]["Drone1"]["items"], 1, 2),
        )
        self.assertEqual(self.states["Drone2"]["mode"], "AUTO")
        self.assertEqual(self.sam.call_count, 1)
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
        infer, report = self.sam.side_effect, self.glimmer.return_value.report
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

        self.sam.side_effect = sam
        self.glimmer.return_value.report = delayed_report
        self.fleet.tracking_start.side_effect = reject_expired_step
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(
            RuntimeError,
            "outside its observation step; hold not sent: flight control is no longer owned",
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
        self.assertEqual(len(self.inspections.evidence(row["id"])["images"]), 4)
        self.assertEqual(self.inspections.pending_events()[0]["id"], row["id"])
        with self.assertRaisesRegex(AssertionError, "no longer available for release"):
            self.inspections.disregard_and_continue(row["rescue"]["review_id"], "Do not resume")

    async def test_slow_rejected_track_processing_is_counted_before_scheduling_again(self):
        infer = self.sam.side_effect

        def sam(*args):
            if self.sam.call_count == 1:
                return infer(*args)
            if self.sam.call_count == 3:
                self.states["Drone1"]["mission_current"] = 3
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        def advance(*args):
            time.sleep(1.05)
            return {
                "status": "tracking",
                "detection": {"box_xyxy": [800, 400, 920, 620]},
                "error": None,
            }

        rejected = SimpleNamespace(
            advance=Mock(side_effect=advance),
            close=Mock(),
            http=Mock(),
            timestamp_ns=200_000_000,
            status="tracking",
        )
        self.sam.side_effect = sam
        with patch.object(self.inspections, "_lock", AsyncMock(return_value=rejected)):
            await self.inspections.start_search(self.receipt)
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 6)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual((row["status"], row["frames"]), ("complete", 3))
        self.assertEqual(row["sampling"]["missed_slots"], 1)
        cycles = [
            json.loads(line)
            for line in (self.directory / "inspections" / row["id"] / "sampling.jsonl")
            .read_text()
            .splitlines()
        ]
        self.assertGreaterEqual(cycles[1]["processing_s"], 1.05)
        self.assertGreaterEqual(cycles[2]["interval_s"], 1.99)
        self.fleet.execute.assert_not_awaited()

    async def test_autopilot_return_land_or_disarm_revokes_failure_hold_authority(self):
        for mode, armed in (("RTL", True), ("LAND", True), ("STABILIZE", False), ("GUIDED", True)):
            with self.subTest(mode=mode, armed=armed):
                self.states["Drone1"].update(mode="AUTO", armed=True)

                def sam(*args):
                    self.states["Drone1"].update(mode=mode, armed=armed)
                    raise TimeoutError("SAM timed out after autopilot change")

                self.sam.side_effect = sam
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
        self.assertEqual(record["error"], "Camera RPC timed out")
        self.assertGreaterEqual(record["cycle"]["capture_s"], 0)
        self.assertIsNone(record["cycle"]["request_s"])
        self.assertTrue(row["rescue"]["pending"])
        self.fleet.execute.assert_awaited_once_with(
            "hold",
            {"drone_id": "Drone1"},
            expected_mode="AUTO",
            expected_step=(self.receipt["missions"]["Drone1"]["items"], 1, 2),
        )
        self.sam.assert_not_called()

    async def test_failure_to_hold_reports_both_errors_without_retry(self):
        self.sam.side_effect = TimeoutError("SAM request timed out")
        self.fleet.execute.side_effect = TimeoutError("BRAKE acknowledgement timed out")
        await self.inspections.start_search(self.receipt)
        with self.assertRaisesRegex(
            RuntimeError, "SAM request timed out; hold failed: BRAKE acknowledgement timed out"
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
            raise TimeoutError("Late SAM timeout")

        self.sam.side_effect = sam
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

        self.sam.side_effect = TimeoutError("SAM request timed out")
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

    async def test_delayed_positive_uses_ordered_buffer_before_fresh_following(self):
        infer = self.sam.side_effect

        def sam(*args):
            time.sleep(1.05)
            return infer(*args)

        self.sam.side_effect = sam
        row = await self.begin()
        self.assertIsNone(row["rescue"]["tracking_error"])
        track = self.tracks[0]
        self.assertEqual(track.start.call_args.args[1], 200_000_000)
        stamps = [call.args[1] for call in track.advance.call_args_list]
        self.assertEqual(stamps, sorted(set(stamps)))
        self.assertGreater(len(stamps), 5)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(self.sam.call_count, 1)
        self.assertEqual(row["status"], "complete")
        self.fleet.tracking_resume.assert_awaited_once()

    async def test_pending_temporal_request_holds_before_request_finishes(self):
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
        await self.until(lambda: row["rescue"]["pending"])
        entered, finish = threading.Event(), threading.Event()
        track = self.tracks[0]
        advance = track.advance.side_effect

        def delayed(*args):
            entered.set()
            assert finish.wait(3), "Test did not complete pending temporal request"
            return advance(*args)

        track.advance.side_effect = delayed
        try:
            await self.until(entered.is_set)
            await asyncio.wait_for(self.until(lambda: self.fleet.execute.await_count == 1), 1.3)
            self.assertFalse(finish.is_set())
            track.close.assert_not_called()
            self.assertEqual(row["rescue"]["phase"], "held")
            self.assertIsNone(row["rescue"]["live_frame"])
            self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
            self.assertGreaterEqual(holds[0] - commands[-1], 1)
            self.assertLess(holds[0] - commands[-1], 1.2)
            self.assertIn("no valid flight update", row["rescue"]["tracking_error"])
            command_count = self.fleet.tracking_target.await_count
        finally:
            finish.set()
        await asyncio.sleep(0.25)
        self.assertEqual(self.fleet.tracking_target.await_count, command_count)
        self.assertEqual(self.temporal.call_count, 1)
        track.close.assert_called_once()
        record = next((self.directory / "inspections" / row["id"]).glob("*-tracking.jsonl"))
        records = [json.loads(line) for line in record.read_text().splitlines()]
        watchdogs = [r for r in records if "phase" in r and r["phase"] == "watchdog"]
        dispatches = [r for r in records if "command_at_ms" in r]
        self.assertEqual(len(watchdogs), 1)
        self.assertGreaterEqual(watchdogs[0]["command_age_s"], 1)
        self.assertEqual(watchdogs[0]["last_command_at_ms"], dispatches[-1]["command_at_ms"])

    async def test_valid_delayed_observations_keep_checked_position_following_active(self):
        self.decisions = ["recheck"]
        acquire, create = self.capture.capture.side_effect, self.temporal.side_effect
        self.dispatch_timings = []

        async def capture():
            await asyncio.sleep(0.25)
            return await acquire()

        def create_delayed(http):
            track = create(http)
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
        self.temporal.side_effect = create_delayed
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
        self.assertTrue(all(0.8 <= interval < 1 for interval in intervals))
        self.fleet.execute.assert_not_awaited()
        self.fleet.tracking_follow.assert_awaited_once()
        self.assertEqual(self.temporal.call_count, 1)

    async def test_late_dispatch_completion_cannot_restore_a_track_after_watchdog_hold(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"])
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

    async def test_slow_live_publication_does_not_delay_fresh_flight_commands(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        publish = self.inspections._publish
        commands = []

        async def delayed(*args, live=False):
            if live:
                entered.set()
                await finish.wait()
            return await publish(*args, live=live)

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
                self.assertEqual(dispatches[0]["publication"], "started")
                self.assertTrue(all(r["publication"] == "skipped_busy" for r in dispatches[1:]))
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

        async def failed(*args, live=False):
            if live:
                raise OSError("Evidence disk unavailable")
            return await publish(*args, live=live)

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
        self.assertEqual(self.temporal.call_count, 1)
        self.tracks[0].close.assert_called_once()

    async def test_operator_override_releases_authority_before_pending_remote_cleanup(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"])
        entered, finish = threading.Event(), threading.Event()
        track = self.tracks[0]
        advance = track.advance.side_effect

        def delayed(*args):
            entered.set()
            assert finish.wait(3), "Test did not release cancelled temporal request"
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
        await self.until(lambda: row["rescue"]["pending"])
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

    async def test_center_candidate_uses_current_identity_without_restarting_tracking(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"])
        target = copy.deepcopy(row["rescue"]["track"]["ground"])
        observed = await self.inspections.center_candidate(row["id"], 4)
        view = self.inspections.camera_views["Drone1"]
        self.assertEqual(view["target"]["north_m"], target["north_m"])
        self.assertEqual(view["target"]["east_m"], target["east_m"])
        self.assertEqual(view["zoom"], 4)
        self.assertEqual(self.temporal.call_count, 1)
        self.tracks[0].close.assert_not_called()
        self.assertEqual(observed["frame"], row["rescue"]["live_frame"])
        self.assertEqual(observed["frame"]["camera_view_revision"], view["revision"])

    async def test_hold_immediately_revokes_live_evidence_and_camera_authority(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"])
        selected = (await self.inspections.get_candidate(row["id"]))["frame"]
        original = copy.deepcopy(row["latest"])
        views = self.capture.view.await_count
        self.inspections.invalidate("Operator hold", "Drone1")
        with self.assertRaisesRegex(AssertionError, "no longer tracking"):
            await self.inspections.get_candidate(row["id"])
        with self.assertRaisesRegex(AssertionError, "no longer tracking"):
            await self.inspections.center_candidate(row["id"], 4)
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
        await self.until(lambda: row["rescue"]["pending"])
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
        self.assertEqual(self.sam.call_count, 1)
        self.assertEqual(self.temporal.call_count, 1)

    async def test_candidate_read_wait_stops_immediately_when_hold_revokes_owner(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"])
        row["rescue"]["live_frame"]["captured_at_ms"] -= 2000
        reading = asyncio.create_task(self.inspections.get_candidate(row["id"]))
        await asyncio.sleep(0)
        self.assertFalse(reading.done())
        self.inspections.invalidate("Operator hold", "Drone1")
        with self.assertRaisesRegex(AssertionError, "no longer tracking"):
            await asyncio.wait_for(reading, 0.2)
        self.assertEqual(self.sam.call_count, 1)
        self.assertEqual(self.temporal.call_count, 1)

    async def test_late_image_write_cannot_restore_invalidated_live_observation(self):
        self.inspections.directory.mkdir()
        for invalidation in ("lost", "revision", "stopped", "stale"):
            with self.subTest(invalidation=invalidation):
                metadata, image = await self.capture.capture()
                metadata["frame_id"] = "interleaving-" + invalidation
                row = {
                    "drone_id": "Drone1",
                    "plan_version": 1,
                    "status": "candidate",
                    "rescue": {"live_frame": None, "tracking_error": None},
                }
                self.inspections.camera_views["Drone1"] = {"revision": 1}
                entered, finish = asyncio.Event(), asyncio.Event()
                threaded = asyncio.to_thread

                async def write(method, *args):
                    entered.set()
                    await finish.wait()
                    return await threaded(method, *args)

                with patch("mission_planner.gcs.inspection.asyncio.to_thread", side_effect=write):
                    task = asyncio.create_task(
                        self.inspections._publish(row, metadata, image, [], live=True)
                    )
                    await entered.wait()
                    if invalidation == "lost":
                        row["rescue"]["tracking_error"] = "Candidate observation became stale"
                    elif invalidation == "revision":
                        self.inspections.camera_views["Drone1"] = {"revision": 2}
                    elif invalidation == "stopped":
                        row["status"] = "stopped"
                    else:
                        metadata["captured_at_ms"] -= 2000
                    finish.set()
                    await task
                self.assertIsNone(row["rescue"]["live_frame"])

    async def test_explicit_follow_uses_historical_seed_and_requires_fresh_temporal_result(self):
        await self.inspections.set_camera_view("Drone1", 30, 0, 0, 2)
        started = await self.inspections.start("Drone1", "person", 0.3, 2)
        await self.inspections.tasks["Drone1"]
        row = self.inspections.rows[started["id"]]
        identity = row["latest"]["frame_id"]
        self.inspections.metadata[identity]["captured_at_ms"] -= 6000
        self.states["Drone1"]["mode"] = "BRAKE"
        await self.inspections.follow_candidate(row["id"], identity, 0, 6)
        await self.until(lambda: self.fleet.tracking_target.await_count >= 2)
        self.assertEqual(row["latest"]["frame_id"], identity)
        self.assertIsNone(row["rescue"]["tracking_error"])
        self.assertLess(time.time() * 1000 - row["rescue"]["track"]["captured_at_ms"], 1000)
        self.assertEqual(self.sam.call_count, 1)
        self.assertEqual(self.temporal.call_count, 1)
        self.review_ready.set()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.fleet.tracking_resume.assert_awaited_once()

    async def test_new_inspection_cannot_reset_same_plan_candidate_allowance(self):
        self.decisions = ["recheck"]
        row = await self.begin()
        await self.until(lambda: row["rescue"]["pending"])
        self.inspections.invalidate("Operator hold", "Drone1")
        with self.assertRaises(asyncio.CancelledError):
            await self.inspections.tasks["Drone1"]
        spent = self.inspections.lock_seconds[(1, "Drone1")]
        self.assertGreater(spent, 0)
        self.states["Drone1"]["mode"] = "BRAKE"
        started = await self.inspections.start("Drone1", "person", 0.3, 2)
        await self.inspections.tasks["Drone1"]
        candidate = self.inspections.rows[started["id"]]
        with self.assertRaisesRegex(
            AssertionError, "remaining candidate allowance for Drone1 in plan 1"
        ):
            await self.inspections.follow_candidate(
                candidate["id"], candidate["latest"]["frame_id"], 0, 10
            )
        self.assertEqual(self.inspections.lock_seconds[(1, "Drone1")], spent)
        self.fleet.tracking_start.assert_awaited_once()
        self.assertNotIn("rescue", candidate)
        policy = self.receipt["plan"]["search"]
        self.assertEqual(
            self.inspections._remaining_lock({"plan_version": 2, "drone_id": "Drone1"}, policy), 10
        )
        self.assertEqual(
            self.inspections._remaining_lock({"plan_version": 1, "drone_id": "Drone2"}, policy), 10
        )

    async def test_cancelled_acquisition_charges_time_before_first_tracking_observation(self):
        entered, finish = threading.Event(), threading.Event()
        create = self.temporal.side_effect

        def create_pending(http):
            track = create(http)
            seed = track.start.side_effect

            def pending(*args):
                entered.set()
                assert finish.wait(3), "Test did not release cancelled acquisition"
                return seed(*args)

            track.start.side_effect = pending
            return track

        self.temporal.side_effect = create_pending
        await self.inspections.start_search(self.receipt)
        try:
            await self.until(entered.is_set)
            await asyncio.sleep(0.03)
            self.inspections.invalidate("Operator hold", "Drone1")
            await asyncio.wait_for(self.inspections.flight_stopped("Drone1"), 0.2)
            self.fleet.tracking_follow.assert_not_awaited()
        finally:
            finish.set()
            self.review_ready.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.inspections.tasks["Drone1"]
        self.assertGreaterEqual(self.inspections.lock_seconds[(1, "Drone1")], 0.03)
        self.assertLess(
            self.inspections._remaining_lock(
                next(iter(self.inspections.rows.values())), self.receipt["plan"]["search"]
            ),
            10,
        )
        self.fleet.tracking_target.assert_not_awaited()

    async def test_automatic_candidate_receives_only_shared_remaining_allowance(self):
        self.inspections.lock_seconds[(1, "Drone1")] = 7
        row = await self.begin()
        self.assertEqual(row["plan_version"], 1)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertGreater(self.inspections.lock_seconds[(1, "Drone1")], 7)
        self.assertLess(self.inspections.lock_seconds[(1, "Drone1")], 10)
        record = next((self.directory / "inspections" / row["id"]).glob("*-tracking.jsonl"))
        first = json.loads(record.read_text().splitlines()[0])
        self.assertEqual(first["allowance_s"], 3)

    async def test_full_optional_buffer_does_not_fail_negative_discovery(self):
        def sam(*args):
            time.sleep(3.5)
            self.states["Drone1"]["mission_current"] = 3
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        self.sam.side_effect = sam
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "complete")
        self.assertEqual(self.capture_count, 17)
        self.assertIsNone(row["error"])
        self.temporal.assert_not_called()
        self.fleet.execute.assert_not_awaited()

    async def test_positive_with_incomplete_buffer_is_reviewed_while_held(self):
        infer = self.sam.side_effect
        self.decisions = ["recheck"]

        def sam(*args):
            time.sleep(3.5)
            return infer(*args)

        self.sam.side_effect = sam
        await self.inspections.start_search(self.receipt)
        row = next(iter(self.inspections.rows.values()))
        await self.until(lambda: row["rescue"]["tracking_error"] is not None)
        self.assertIn("buffer filled", row["rescue"]["tracking_error"])
        self.review_ready.set()
        await self.until(lambda: row["rescue"]["pending"])
        self.assertEqual(row["rescue"]["phase"], "held")
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        self.temporal.assert_not_called()
        self.fleet.tracking_follow.assert_not_awaited()

    async def test_expired_discarded_identity_does_not_fail_later_discovery(self):
        infer = self.sam.side_effect

        def sam(*args):
            if self.sam.call_count == 1:
                return infer(*args)
            self.states["Drone1"]["mission_current"] = 3
            return {"width": 1920, "height": 1080, "detections": {"person": []}}

        rejected = SimpleNamespace(
            advance=Mock(),
            close=Mock(),
            http=Mock(),
            timestamp_ns=-3_000_000_000,
            status="tracking",
        )
        self.sam.side_effect = sam
        with patch.object(self.inspections, "_lock", AsyncMock(return_value=rejected)):
            await self.inspections.start_search(self.receipt)
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        row = next(iter(self.inspections.rows.values()))
        self.assertEqual(row["status"], "complete")
        rejected.advance.assert_not_called()
        rejected.close.assert_called_once()
        self.assertTrue(any("association expired" in event["text"] for event in self.events))
        self.assertEqual(self.sam.call_count, 2)
        self.fleet.execute.assert_not_awaited()

    async def test_failed_session_close_is_reported_once_without_retry(self):
        row = await self.begin()
        track = self.tracks[0]

        def failed():
            track.status = "closed"
            raise TimeoutError("SAM temporal close timed out")

        track.close.side_effect = failed
        with self.assertRaisesRegex(RuntimeError, "SAM temporal close timed out"):
            await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        track.close.assert_called_once()
        self.assertEqual(row["status"], "failed")

    async def test_expired_identity_during_resume_does_not_interrupt_checked_return(self):
        acquire, restore = self.capture.capture.side_effect, self.fleet.tracking_resume.side_effect
        restoring = asyncio.Event()

        async def capture():
            metadata, jpeg = await acquire()
            if self.fleet.tracking_resume.called:
                await restoring.wait()
                metadata["timestamp_ns"] += 3_000_000_000
            return metadata, jpeg

        async def resume(*args):
            restoring.set()
            await asyncio.sleep(0.05)
            await restore(*args)

        self.capture.capture.side_effect = capture
        self.fleet.tracking_resume.side_effect = resume
        row = await self.begin()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.assertTrue(
            any("expired during observation restoration" in event["text"] for event in self.events)
        )
        self.tracks[0].close.assert_called_once()
        self.fleet.execute.assert_not_awaited()

    async def test_lost_session_closes_before_review_outlives_remote_lease(self):
        self.track_error = "Selected object left the image"
        lease_clock = [0]
        closed = threading.Event()
        create = self.temporal.side_effect

        def leased(http):
            track = create(http)
            close = track.close.side_effect

            def checked_close():
                assert lease_clock[0] < 30, "Video session is absent or expired"
                close()
                closed.set()

            track.close.side_effect = checked_close
            return track

        self.temporal.side_effect = leased
        await self.inspections.start_search(self.receipt)
        await asyncio.wait_for(self.until(closed.is_set), 1)
        self.assertEqual(self.review_count, 0)
        row = next(iter(self.inspections.rows.values()))
        source = copy.deepcopy(row["latest"])
        self.assertEqual(self.states["Drone1"]["mode"], "BRAKE")
        lease_clock[0] = 31
        self.review_ready.set()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        for key in ("frame_id", "frame_ref", "overlay_ref"):
            self.assertEqual(row["latest"][key], source[key])
        self.tracks[0].close.assert_called_once()
        self.fleet.tracking_resume.assert_awaited_once()
        self.fleet.tracking_target.assert_not_awaited()

    async def test_lost_association_closes_before_long_saved_route_restoration_ends(self):
        lease_clock = [0]
        closed = threading.Event()
        started, finish = asyncio.Event(), asyncio.Event()
        acquire, create = self.capture.capture.side_effect, self.temporal.side_effect
        restore = self.fleet.tracking_resume.side_effect

        def leased(http):
            track = create(http)
            close = track.close.side_effect

            def checked_close():
                assert lease_clock[0] < 30, "Video session is absent or expired"
                close()
                closed.set()

            track.close.side_effect = checked_close
            return track

        async def capture():
            if self.fleet.tracking_resume.called:
                await started.wait()
                self.track_error = "Rejected candidate is no longer visible during return"
            return await acquire()

        async def resume(*args):
            started.set()
            await finish.wait()
            return await restore(*args)

        self.temporal.side_effect = leased
        self.capture.capture.side_effect = capture
        self.fleet.tracking_resume.side_effect = resume
        row = await self.begin()
        try:
            await asyncio.wait_for(self.until(closed.is_set), 1)
            self.assertFalse(self.inspections.tasks["Drone1"].done())
            self.assertEqual(row["rescue"]["phase"], "resuming")
            lease_clock[0] = 31
        finally:
            finish.set()
        await asyncio.wait_for(self.inspections.tasks["Drone1"], 5)
        self.assertEqual(row["status"], "complete")
        self.assertEqual(self.states["Drone1"]["mode"], "AUTO")
        self.tracks[0].close.assert_called_once()
        self.fleet.execute.assert_not_awaited()

    async def test_failed_close_is_terminal_for_outer_cleanup_without_another_request(self):
        track = SimpleNamespace(status="tracking", http=Mock())

        def failed():
            track.status = "closed"
            raise TimeoutError("SAM temporal close timed out")

        track.close = Mock(side_effect=failed)
        with self.assertRaisesRegex(TimeoutError, "SAM temporal close timed out"):
            await close_track(track)
        await close_track(track)
        track.close.assert_called_once()
        track.http.close.assert_called_once()

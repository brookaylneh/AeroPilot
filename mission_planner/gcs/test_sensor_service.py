"""Capture persistence, shared ingestion and per-connection feed delivery."""

import asyncio
import io
import json
import math
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.gcs.geodesy import NAMES, ORIGIN
from mission_planner.gcs.sensor_feed import sensor_message
from mission_planner.gcs.service import Mission
from mission_planner.search.state import Search
from world.cosys.capture import FrameCapture
from world.cosys.sensor import MissionSensor


def capture_record(name, index):
    timestamp = 1_793_188_800_000_000_000 + index * 1_000_000_000
    return {
        "schemaVersion": 1,
        "vehicleId": name,
        "captureId": f"{name}-{index}",
        "capturedAtUtc": datetime.fromtimestamp(timestamp / 1e9, timezone.utc).isoformat(),
        "timestampNs": timestamp,
        "imagePath": f"/mission/captures/{name}-{index}.jpg",
        "width": 3840,
        "height": 2160,
        "horizontalFovDegrees": 70,
        "coordinateSystem": "UE_LH_Z_UP_CENTIMETERS",
        "localNedOriginUeCm": dict.fromkeys("xyz", 0),
        "cameraPoseLocalNed": {
            "position": {"x": 0, "y": 0, "z": -30},
            "orientation": {"w": math.sqrt(0.5), "x": 0, "y": -math.sqrt(0.5), "z": 0},
        },
        "cameraPositionWorldM": {"x": 0, "y": 0, "z": 30},
        "validation": {
            "accepted": True,
            "nadirErrorDegrees": 0,
            "reasons": [],
            "captureUnixNs": timestamp,
        },
        "mavlinkSamples": [
            {
                "receivedAtUnixNs": timestamp + offset,
                "positionWorldM": {"x": north, "y": east, "z": 30},
            }
            for offset, north, east in (
                (-200_000_000, -3, -2),
                (-100_000_000, 0, -2),
                (100_000_000, 0, 2),
            )
        ],
        "sha256": "a" * 64,
        "captureLatencySeconds": 0.1,
        "acquisitionWallSeconds": 0.2,
        "transportBytes": 3840 * 2160 * 3,
    }


PREVIEW = {"jpeg_base64": "/9j/2Q==", "width": 960, "height": 540}


def capture_trigger(record):
    sample = record["mavlinkSamples"][0]
    return {
        "mission_current": 1,
        "received_at_ms": sample["receivedAtUnixNs"] / 1e6,
        "north_m": sample["positionWorldM"]["x"],
        "east_m": sample["positionWorldM"]["y"],
    }


class MemorySocket:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.updated = asyncio.Queue()

    async def send(self, raw):
        self.sent.append(json.loads(raw))
        self.updated.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def states(self, count):
        async def complete():
            while sum(message["type"] == "state" for message in self.sent) < count:
                await self.updated.get()

        await asyncio.wait_for(complete(), timeout=2)


class SensorServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        mission = object.__new__(Mission)
        mission.directory = Path(temporary.name)
        mission.log = io.StringIO()
        mission.id = str(uuid.uuid4())
        mission.case = {
            "id": str(uuid.uuid4()),
            "name": "Sensor test",
            "report": {
                key: "Test briefing"
                for key in ("description", "reportedCircumstances", "lastKnownLocation")
            },
        }
        mission.started = time.monotonic()
        mission.events, mission.event_sequence, mission.context_index = [], 0, 0
        mission.autonomy, mission.approvals = "Supervised", {}
        mission.agent_busy, mission.fleet_ready = False, False
        mission.fleet_error, mission.sensor_error = None, None
        mission.map_focus, mission.feed_focus = None, None
        mission.sensor_frames = {}
        mission.fleet = Mock()
        mission.fleet.execute = AsyncMock()
        mission.fleet.on_event = mission.fleet_event
        mission.search = Search(mission)
        self.mission = mission

    async def connect(self):
        socket = MemorySocket()
        task = asyncio.create_task(
            self.mission.connection(socket, {"type": "connect", "case_id": self.mission.case["id"]})
        )

        async def close():
            socket.incoming.put_nowait(None)
            await asyncio.wait_for(task, timeout=2)

        self.addAsyncCleanup(close)
        return socket

    async def test_view_focus_without_frame_never_arms_or_asks_for_flight_approval(self):
        result = await self.mission.invoke("open_feed", {"drone_id": "Drone3"}, "operator", "view")
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], {"drone_id": "Drone3", "opened": True})
        self.assertEqual(self.mission.feed_focus["drone_id"], "Drone3")
        self.assertEqual(self.mission.feed_focus["type"], "feed_focus")
        uuid.UUID(self.mission.feed_focus["id"])
        self.assertFalse(self.mission.approvals)
        self.assertFalse(self.mission.sensor_frames)
        self.mission.fleet.execute.assert_not_awaited()
        socket = await self.connect()
        await socket.states(2)
        self.assertEqual(
            [m for m in socket.sent if m["type"] == "feed_focus"], [self.mission.feed_focus]
        )

    async def test_each_connection_replays_latest_frames_then_only_changed_drone(self):
        self.mission.sensor_frames = {
            name: sensor_message(capture_record(name, 1), PREVIEW) for name in NAMES
        }
        socket = await self.connect()
        await socket.states(2)
        initial = [m for m in socket.sent if m["type"] == "sensor_frame"]
        self.assertEqual({m["frame"]["drone_id"] for m in initial}, set(NAMES))
        self.assertEqual(len(initial), len(NAMES))
        for index in (2, 3):
            self.mission.sensor_frames["Drone2"] = sensor_message(
                capture_record("Drone2", index), PREVIEW
            )
        await socket.states(3)
        messages = [m for m in socket.sent if m["type"] == "sensor_frame"]
        self.assertEqual(len(messages), len(NAMES) + 1)
        self.assertEqual(messages[-1]["frame"]["capture_id"], "Drone2-3")
        second = await self.connect()
        await second.states(2)
        replay = [m for m in second.sent if m["type"] == "sensor_frame"]
        self.assertEqual(len(replay), len(NAMES))
        self.assertEqual(
            next(m for m in replay if m["frame"]["drone_id"] == "Drone2")["frame"]["capture_id"],
            "Drone2-3",
        )
        self.assertEqual(
            len([m for m in socket.sent if m["type"] == "sensor_frame"]), len(NAMES) + 1
        )

    async def test_new_drone_frame_during_blocked_send_keeps_connection_alive(self):
        self.mission.sensor_frames["Drone1"] = sensor_message(capture_record("Drone1", 1), PREVIEW)
        socket = await self.connect()
        send = socket.send
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked_send(raw):
            message = json.loads(raw)
            if message["type"] == "sensor_frame" and message["frame"]["drone_id"] == "Drone1":
                entered.set()
                await release.wait()
            await send(raw)

        socket.send = blocked_send
        await asyncio.wait_for(entered.wait(), timeout=2)
        self.mission.sensor_frames["Drone2"] = sensor_message(capture_record("Drone2", 1), PREVIEW)
        release.set()
        await socket.states(3)
        frames = [message for message in socket.sent if message["type"] == "sensor_frame"]
        self.assertEqual([message["frame"]["drone_id"] for message in frames], ["Drone1", "Drone2"])

    async def sensor(self, accepted):
        directory = self.mission.directory / "captures"
        directory.mkdir()
        writer = object.__new__(FrameCapture)
        writer.directory = directory
        frame = capture_record("Drone1", 1)
        frame["validation"]["accepted"] = accepted
        if not accepted:
            frame["validation"]["reasons"] = ["Camera optical axis exceeds the nadir tolerance"]
        frame, preview = await asyncio.to_thread(
            writer._write, frame, bytes(frame["transportBytes"]), "frame-000001", 95
        )
        sensor = object.__new__(MissionSensor)
        sensor.capture = SimpleNamespace(
            directory=directory, capture=AsyncMock(return_value=(frame, preview))
        )
        sensor.fleet = self.mission.fleet
        sensor.metrics = io.StringIO()
        return sensor, frame, preview

    async def test_persisted_accepted_record_enters_shared_ingestion_exactly_once(self):
        sensor, record, preview = await self.sensor(True)

        async def consume(persisted, image_preview):
            path = sensor.capture.directory / f"{persisted['captureId']}.json"
            self.assertEqual(json.loads(path.read_text()), persisted)
            self.assertTrue(Path(persisted["imagePath"]).is_file())
            trigger = persisted["captureTrigger"]
            self.assertEqual(trigger["autoGroundTrackM"], 12)
            self.assertEqual(trigger["physicalGroundTrackM"], 12)
            self.assertAlmostEqual(trigger["acquiredGroundTrackM"], 17)
            self.assertAlmostEqual(trigger["acquiredGroundTrackSpacingM"], 12)
            self.assertAlmostEqual(trigger["cameraSpacingM"], math.sqrt(90))
            await self.mission.capture_received(persisted, image_preview)

        sensor.on_capture = AsyncMock(side_effect=consume)
        with (
            patch("mission_planner.agents.perception.geometry.terrain_height", return_value=0),
            patch.object(
                self.mission.search, "consume_capture", wraps=self.mission.search.consume_capture
            ) as ingestion,
        ):
            acquired = await sensor._capture(
                "Drone1",
                1,
                12,
                12,
                capture_trigger(record),
                {"position": {"x": -3, "y": -9, "z": 30}, "groundTrackM": 5},
            )
            self.assertEqual(acquired["position"], record["cameraPositionWorldM"])
            self.assertAlmostEqual(acquired["groundTrackM"], 17)
            sensor.on_capture.assert_awaited_once_with(record, preview)
            ingestion.assert_called_once_with(record, "unprocessed")
            with self.assertRaisesRegex(AssertionError, "Duplicate capture record"):
                await self.mission.capture_received(record, preview)
        coverage = self.mission.search.coverage_summary()
        self.assertEqual(coverage["captures"], 1)
        self.assertEqual(coverage["negative_observations"], 0)
        saved = json.loads((self.mission.directory / "search.json").read_text())
        self.assertEqual(saved["summary"]["coverage"], coverage)
        message = self.mission.sensor_frames["Drone1"]["frame"]
        self.assertEqual(message["timestamp_ns"], str(record["timestampNs"]))
        self.assertEqual(message["pose"]["altitude_msl_m"], 30 + ORIGIN["altitude_msl_m"])

    async def test_rejected_record_is_persisted_without_ingestion_or_preview(self):
        sensor, record, preview = await self.sensor(False)
        sensor.on_capture = AsyncMock(side_effect=self.mission.capture_received)
        await sensor._capture("Drone1", 1, 12, 12, capture_trigger(record), None)
        sensor.on_capture.assert_not_awaited()
        self.assertIsNone(preview)
        self.assertEqual(self.mission.search.coverage_summary()["captures"], 0)
        self.assertFalse(self.mission.sensor_frames)
        path = sensor.capture.directory / f"{record['captureId']}.json"
        self.assertFalse(json.loads(path.read_text())["validation"]["accepted"])
        self.assertIn("capture rejected", self.mission.events[-1]["text"])

    async def test_invalid_callback_fails_before_mutating_coverage(self):
        for accepted, preview in ((False, PREVIEW), (True, None)):
            frame = capture_record("Drone1", 1)
            frame["validation"]["accepted"] = accepted
            with self.assertRaises(AssertionError):
                await self.mission.capture_received(frame, preview)
            self.assertFalse(self.mission.search.captures)
            self.assertFalse(self.mission.sensor_frames)

    async def test_images_stay_out_of_periodic_state_history_and_model_context(self):
        message = sensor_message(capture_record("Drone1", 1), PREVIEW)
        self.mission.sensor_frames["Drone1"] = message
        await self.mission.invoke("open_feed", {"drone_id": "Drone1"}, "operator", "view")
        socket = await self.connect()
        await socket.states(2)
        non_images = [m for m in socket.sent if m["type"] != "sensor_frame"]
        self.assertTrue(any(m["type"] == "history" for m in non_images))
        for value in (
            json.dumps(non_images),
            json.dumps(self.mission.state()),
            self.mission.log.getvalue(),
            self.mission.instructions(True),
            self.mission.instructions(False),
        ):
            self.assertNotIn("jpeg_base64", value)
            self.assertNotIn(PREVIEW["jpeg_base64"], value)
        self.assertIn(message, socket.sent)


if __name__ == "__main__":
    unittest.main()

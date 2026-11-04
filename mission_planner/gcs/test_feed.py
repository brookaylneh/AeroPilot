"""Public map delivery and camera focus never issue flight commands."""

import asyncio
import json
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.gcs.fences import default_fences
from mission_planner.gcs.geodesy import NAMES
from mission_planner.gcs.service import Mission


class MemorySocket:
    def __init__(self):
        self.request = SimpleNamespace(path="/")
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

    def received(self, kind):
        return [message for message in self.sent if message["type"] == kind]

    async def states(self, count, kind="state"):
        async def complete():
            while len(self.received(kind)) < count:
                await self.updated.get()

        await asyncio.wait_for(complete(), timeout=2)


class FeedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        mission = object.__new__(Mission)
        with patch.dict("os.environ", {"SNR_MISSION_DATA_DIR": temporary.name}):
            mission.initialize_run(None, active=True)
        self.addCleanup(mission.log.close)
        mission.clearing = False
        mission.effort = "high"
        mission.fleet_ready = False
        mission.fleet_error = None
        mission.fleet = Mock(
            fences=default_fences(),
            fences_verified=True,
            fence_error=None,
            command_lock=asyncio.Lock(),
        )
        mission.fleet.snapshot.return_value = [
            {
                "id": name,
                "armed": True,
                "camera_available": False,
                "link_lost": False,
                "ekf_healthy": True,
            }
            for name in NAMES
        ]
        mission.fleet.execute = AsyncMock()
        self.mission = mission

    async def connect(self):
        socket = MemorySocket()
        enter_mission = patch(
            "world.cosys.client.enter_mission",
            side_effect=AssertionError("Browser connection must not change world mode"),
        )
        enter_mission.start()
        self.addCleanup(enter_mission.stop)
        task = asyncio.create_task(
            self.mission.connection(socket, {"type": "connect", "case_id": None})
        )

        async def close():
            socket.incoming.put_nowait(None)
            await asyncio.wait_for(task, timeout=2)

        self.addAsyncCleanup(close)
        return socket

    async def test_general_session_publishes_public_map_without_briefing(self):
        mission = await self.mission.execute("get_mission", {}, "operator", "mission")
        self.assertIsNone(mission["briefing"])
        self.assertIsNone(mission["case_id"])
        socket = await self.connect()
        await socket.states(2)
        for message in socket.sent:
            self.assertEqual(message["type"] == "history", "landmarks" in message)
            if message["type"] == "history":
                self.assertEqual(message["landmarks"], self.mission.landmarks)
                self.assertTrue(message["landmarks"])
        self.assertEqual(
            {message["type"] for message in socket.sent}, {"history", "plans", "state"}
        )
        self.mission.fleet.execute.assert_not_awaited()

    async def test_plan_receipts_travel_once_per_replacement_clearing_and_reset(self):
        socket = await self.connect()
        await socket.states(2)
        self.assertEqual(
            socket.received("plans"),
            [{"type": "plans", "preview": None, "active": None, "previous": None}],
        )
        self.mission.plan_preview = {"plan": {"version": 1}}
        await socket.states(2, "plans")
        # The service clears a receipt and installs its replacement within one tick; the
        # replacement may occupy the discarded receipt's memory and must still travel.
        self.mission.plan_preview = None
        self.mission.plan_preview = {"plan": {"version": 1}}
        await socket.states(3, "plans")
        self.assertEqual(socket.received("plans")[-1]["preview"], {"plan": {"version": 1}})
        self.mission.plan_preview, self.mission.plan_active = None, {"plan": {"version": 1}}
        await socket.states(4, "plans")
        self.assertEqual(
            socket.received("plans")[-1],
            {
                "type": "plans",
                "preview": None,
                "active": {"plan": {"version": 1}},
                "previous": None,
            },
        )
        count = len(socket.received("state"))
        await socket.states(count + 3)
        self.assertEqual(len(socket.received("plans")), 4)
        self.assertTrue(all("active" not in m["planner"] for m in socket.received("state")))
        self.mission.id, self.mission.events = str(uuid.uuid4()), []
        self.mission.plan_active = None
        await socket.states(5, "plans")
        self.assertEqual(len(socket.received("history")), 2)
        self.assertEqual(socket.received("plans")[-1]["active"], None)
        self.mission.plan_previous = {"plan": {"version": 2}}
        again = await self.connect()
        await again.states(1)
        self.assertEqual(
            again.received("plans"),
            [
                {
                    "type": "plans",
                    "preview": None,
                    "active": None,
                    "previous": {"plan": {"version": 2}},
                }
            ],
        )
        self.mission.fleet.execute.assert_not_awaited()

    async def test_uploaded_camera_opens_without_flight_command(self):
        self.mission.fleet_ready = True
        self.mission.fleet.snapshot.return_value[2]["camera_available"] = True
        result = await self.mission.invoke("open_feed", {"drone_id": "Drone3"}, "operator", "view")
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], {"drone_id": "Drone3", "opened": True})
        self.assertEqual(self.mission.feed_focus["drone_id"], "Drone3")
        self.assertEqual(self.mission.feed_focus["type"], "feed_focus")
        uuid.UUID(self.mission.feed_focus["id"])
        self.assertFalse(self.mission.approvals)
        self.mission.fleet.execute.assert_not_awaited()
        for _ in range(2):
            socket = await self.connect()
            await socket.states(2)
            self.assertEqual(
                [m for m in socket.sent if m["type"] == "feed_focus"], [self.mission.feed_focus]
            )

    async def test_unavailable_camera_rejects_focus_without_command_or_approval(self):
        self.mission.fleet_ready = True
        vehicle = self.mission.fleet.snapshot.return_value[2]
        for available, lost, error in (
            (False, False, "no active uploaded flight plan"),
            (True, True, "telemetry link lost"),
        ):
            with self.subTest(available=available, lost=lost):
                vehicle.update(camera_available=available, link_lost=lost)
                result = await self.mission.invoke(
                    "open_feed", {"drone_id": "Drone3"}, "operator", "view"
                )
                self.assertFalse(result["ok"])
                self.assertIn(error, result["error"])
                self.assertIsNone(self.mission.feed_focus)
                self.assertFalse(self.mission.approvals)
                self.mission.fleet.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

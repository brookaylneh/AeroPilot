"""Ordered processing, visible terminal failures and explicit capture sources."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mission_planner.agents.perception.ingestion import Ingestion, load_replay
from mission_planner.environment import ROOT


def capture(path):
    timestamp = int(Path(path).stem)
    return {
        "vehicleId": "Drone1",
        "timestampNs": timestamp,
        "sha256": str(timestamp),
        "file": "frame.jpg",
    }, b"frame"


class OrderedIngestion(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.reader = patch(
            "mission_planner.agents.perception.ingestion.read_capture", side_effect=capture
        )
        self.reader.start()
        self.addCleanup(self.reader.stop)

    async def create(self, process, source="recorded"):
        ingestion = Ingestion(
            self.directory, source, SimpleNamespace(process=process), Mock(), Mock()
        )
        self.addAsyncCleanup(ingestion.close)
        return ingestion

    async def test_failed_frame_stays_visible_and_next_frame_is_processed_once(self):
        calls = []

        def process(record, payload, identity, source, lane, stage):
            calls.append(record["timestampNs"])
            if record["timestampNs"] == 1:
                raise RuntimeError("Inference failed")
            return {"frame_id": identity, "timings": {}}

        ingestion = await self.create(process)
        first = await ingestion.ingest("1.json", "Drone2")
        second = await ingestion.ingest("2.json", "Drone2")
        await asyncio.wait_for(ingestion.drain(), 2)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(ingestion.frames[first]["status"], "failed")
        self.assertEqual(ingestion.frames[first]["error"], "RuntimeError: Inference failed")
        self.assertEqual(ingestion.frames[second]["status"], "complete")
        self.assertEqual(ingestion.frames[second]["drone_id"], "Drone1")
        self.assertEqual(ingestion.frames[second]["replay_lane"], "Drone2")
        with self.assertRaises(AssertionError):
            await ingestion.ingest("1.json", "Drone2")
        self.assertEqual(calls, [1, 2])
        persisted = json.loads(self.directory.joinpath("frames.json").read_text())
        self.assertEqual([f["status"] for f in persisted], ["failed", "complete"])
        ingestion.on_complete.assert_called_once()

    async def test_busy_lane_exposes_backlog_and_age_without_overtaking(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def process(record, payload, identity, source, lane, stage):
            calls.append(record["timestampNs"])
            entered.set()
            assert release.wait(2), "Test did not release processor"
            return {"frame_id": identity, "timings": {}}

        ingestion = await self.create(process)
        try:
            first = await ingestion.ingest("1.json", "Drone1")
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            await ingestion.ingest("2.json", "Drone1")
            lane = ingestion.summary()["lanes"][0]
            self.assertEqual(lane["current_frame"], first)
            self.assertEqual(lane["queued"], 1)
            self.assertGreaterEqual(lane["oldest_age_s"], 0)
            self.assertEqual(calls, [1])
        finally:
            release.set()
        await asyncio.wait_for(ingestion.drain(), 2)
        self.assertEqual(calls, [1, 2])

    async def test_recorded_requires_lane_and_live_forbids_it(self):
        ingestion = await self.create(Mock())
        with self.assertRaises(AssertionError):
            await ingestion.ingest("1.json")
        self.assertFalse(ingestion.frames)
        live = Ingestion(
            self.directory / "live", "live", SimpleNamespace(process=Mock()), Mock(), Mock()
        )
        self.addAsyncCleanup(live.close)
        with self.assertRaises(AssertionError):
            await live.ingest("1.json", "Drone1")
        self.assertFalse(live.frames)

    async def test_closing_while_reading_rejects_before_queueing_to_stopped_workers(self):
        entered, release = threading.Event(), threading.Event()

        def blocked_read(path):
            entered.set()
            assert release.wait(2), "Test did not release capture read"
            return capture(path)

        processor = SimpleNamespace(process=Mock())
        ingestion = Ingestion(self.directory, "recorded", processor, Mock(), Mock())
        with patch(
            "mission_planner.agents.perception.ingestion.read_capture", side_effect=blocked_read
        ):
            pending = asyncio.create_task(ingestion.ingest("1.json", "Drone1"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await ingestion.close()
                release.set()
                with self.assertRaisesRegex(AssertionError, "closed while reading this record"):
                    await pending
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                if not ingestion.closed:
                    await ingestion.close()
        self.assertTrue(all(task.done() for task in ingestion.tasks))
        self.assertFalse(ingestion.frames)
        self.assertTrue(all(queue.empty() for queue in ingestion.queues.values()))
        processor.process.assert_not_called()

    def test_replay_manifest_rejects_missing_source_and_path_escape(self):
        path = self.directory / "manifest.json"
        record = self.directory / "1.json"
        record.write_text("{}")
        manifest = {
            "schemaVersion": 1,
            "id": "chosen",
            "label": "Recorded frames",
            "source": "recorded",
            "records": [{"record": "1.json", "replay_lane": "Drone1", "frame_id": "one"}],
        }
        path.write_text(json.dumps(manifest))
        self.assertEqual(load_replay(path)["records"][0]["record"], str(record))
        del manifest["source"]
        path.write_text(json.dumps(manifest))
        with self.assertRaises(AssertionError):
            load_replay(path)
        manifest["source"] = "recorded"
        manifest["records"][0]["record"] = "../../AGENTS.md"
        path.write_text(json.dumps(manifest))
        with self.assertRaises(AssertionError):
            load_replay(path)


if __name__ == "__main__":
    unittest.main()

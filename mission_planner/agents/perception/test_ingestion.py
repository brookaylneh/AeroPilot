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
            self.directory,
            source,
            SimpleNamespace(detect=process, verify=lambda result, stage: result),
            Mock(),
            Mock(),
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

    async def test_busy_lane_bounds_sam_concurrency_and_exposes_backlog(self):
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
            active_ids = [first]
            for index in range(2, 5):
                active_ids.append(await ingestion.ingest(str(index) + ".json", "Drone1"))
            await ingestion.ingest("5.json", "Drone1")
            await asyncio.sleep(0.01)
            lane = ingestion.summary()["lanes"][0]
            self.assertEqual([f["frame_id"] for f in lane["active"]], active_ids)
            self.assertEqual(lane["queued"], 1)
            self.assertGreaterEqual(lane["oldest_age_s"], 0)
            self.assertEqual(sorted(calls), [1, 2, 3, 4])
        finally:
            release.set()
        await asyncio.wait_for(ingestion.drain(), 2)
        self.assertEqual(sorted(calls), [1, 2, 3, 4, 5])

    async def test_recorded_requires_lane_and_live_forbids_it(self):
        ingestion = await self.create(Mock())
        with self.assertRaises(AssertionError):
            await ingestion.ingest("1.json")
        self.assertFalse(ingestion.frames)
        live = Ingestion(
            self.directory / "live", "live", SimpleNamespace(detect=Mock()), Mock(), Mock()
        )
        self.addAsyncCleanup(live.close)
        with self.assertRaises(AssertionError):
            await live.ingest("1.json", "Drone1")
        self.assertFalse(live.frames)

    async def test_verification_and_failures_cannot_reorder_belief_or_block_sam(self):
        first_detect, release_first, first_verify, release_verify = (
            threading.Event() for _ in range(4)
        )
        detected, verified, committed = [], [], []

        def detect(record, payload, identity, source, lane, stage):
            timestamp = record["timestampNs"]
            stage("SAM")
            if timestamp == 1:
                first_detect.set()
                assert release_first.wait(3)
            detected.append(timestamp)
            if timestamp == 2:
                raise RuntimeError("Terminal SAM failure")
            return {"frame_id": identity, "timestamp": timestamp, "timings": {}}

        def verify(result, stage):
            stage("Glimmer")
            if result["timestamp"] == 1:
                first_verify.set()
                assert release_verify.wait(3)
            verified.append(result["timestamp"])
            return result

        ingestion = Ingestion(
            self.directory,
            "recorded",
            SimpleNamespace(detect=detect, verify=verify),
            lambda result: committed.append(result["timestamp"]),
            Mock(),
        )
        try:
            ids = [await ingestion.ingest(str(i) + ".json", "Drone1") for i in range(1, 6)]
            self.assertTrue(await asyncio.to_thread(first_detect.wait, 2))
            for _ in range(100):
                if len(detected) == 4:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(sorted(detected), [2, 3, 4, 5])
            self.assertFalse(committed)
            self.assertEqual(ingestion.frames[ids[1]]["status"], "failed")
            self.assertEqual(ingestion.frames[ids[1]]["failure_stage"], "SAM")
            self.assertEqual(ingestion.summary()["lanes"][0]["failed"], 1)
            release_first.set()
            self.assertTrue(await asyncio.to_thread(first_verify.wait, 2))
            self.assertFalse(committed)
            self.assertEqual(sorted(verified), [3, 4, 5])
            await ingestion.ingest("6.json", "Drone1")
            for _ in range(100):
                if 6 in verified:
                    break
                await asyncio.sleep(0.01)
            self.assertIn(6, detected)
            self.assertIn(6, verified)
            self.assertFalse(committed)
            closing = asyncio.create_task(ingestion.close())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release_verify.set()
            await asyncio.wait_for(closing, 3)
            self.assertEqual(committed, [1, 3, 4, 5, 6])
            self.assertEqual(ingestion.frames[ids[1]]["status"], "failed")
            self.assertEqual(ingestion.frames[ids[1]]["failure_stage"], "SAM")
            self.assertTrue(all(task.done() for task in ingestion.tasks))
            self.assertEqual(ingestion.summary()["lanes"][0]["active"], [])
        finally:
            release_first.set()
            release_verify.set()
            if not ingestion.closed:
                await ingestion.close()

    async def test_full_verification_pool_leaves_sam_dispatch_available(self):
        full, release, fifth_detected = (threading.Event() for _ in range(3))
        lock, entered = threading.Lock(), []

        def detect(record, payload, identity, source, lane, stage):
            if record["timestampNs"] == 5:
                fifth_detected.set()
            return {"frame_id": identity, "timings": {}}

        def verify(result, stage):
            with lock:
                entered.append(result["frame_id"])
                if len(entered) == 4:
                    full.set()
            assert release.wait(3)
            return result

        ingestion = Ingestion(
            self.directory,
            "recorded",
            SimpleNamespace(detect=detect, verify=verify),
            Mock(),
            Mock(),
        )
        try:
            for index in range(1, 5):
                await ingestion.ingest(str(index) + ".json", "Drone1")
            self.assertTrue(await asyncio.to_thread(full.wait, 2))
            await ingestion.ingest("5.json", "Drone1")
            self.assertTrue(await asyncio.to_thread(fifth_detected.wait, 2))
            self.assertEqual(len(entered), 4)
        finally:
            release.set()
            await asyncio.wait_for(ingestion.close(), 3)
        self.assertTrue(all(frame["status"] == "complete" for frame in ingestion.frames.values()))

    async def test_closing_while_reading_rejects_before_queueing_to_stopped_workers(self):
        entered, release = threading.Event(), threading.Event()

        def blocked_read(path):
            entered.set()
            assert release.wait(2), "Test did not release capture read"
            return capture(path)

        processor = SimpleNamespace(detect=Mock())
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
        processor.detect.assert_not_called()

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

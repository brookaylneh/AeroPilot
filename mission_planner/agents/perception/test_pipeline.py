"""Exact calibration transforms, prompt separation and durable queue failures."""

import asyncio
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from mission_planner.agents.models import Detection
from mission_planner.agents.perception.configuration import (
    CAPTURE_FIELDS,
    STATE_FIELDS,
    crop,
    encode,
    jpeg,
    load_configuration,
    prepare_frame,
    read_capture,
)
from mission_planner.agents.perception.ingestion import Ingestion
from mission_planner.agents.perception.pipeline import Processor
from mission_planner.environment import ROOT


def recorded():
    path = ROOT / ".local/data/calibration/adult-kelvin/frame-00-+00-Drone1.json"
    raw = json.loads(path.read_text())
    record = {key: raw[key] for key in CAPTURE_FIELDS}
    for key in ("vehicleStateBefore", "vehicleStateAfter"):
        record[key] = {field: raw[key][field] for field in STATE_FIELDS}
    return record, path.with_name(record["file"]).read_bytes()


class Configuration(unittest.TestCase):
    def test_saved_capture_rejects_truth_extra_fields_and_changed_image(self):
        record, payload = recorded()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "frame.json"
            path.with_name(record["file"]).write_bytes(payload)
            path.write_text(encode(record))
            self.assertEqual(read_capture(path), (record, payload))
            changed = {**record, "observation": {"groundWorldM": {"x": 0}}}
            path.write_text(encode(changed))
            with self.assertRaises(AssertionError):
                read_capture(path)
            for branch, axes in (
                ("cameraPositionWorldM", None),
                ("vehicleStateAfter", "velocityLocalNedMps"),
            ):
                changed = copy.deepcopy(record)
                target = changed[branch] if axes is None else changed[branch][axes]
                target["actorPosition"] = {"x": 0, "y": 0, "z": 0}
                path.write_text(encode(changed))
                with self.assertRaises(AssertionError):
                    read_capture(path)
            changed = copy.deepcopy(record)
            changed["vehicleStateAfter"]["actorTelemetry"] = {}
            path.write_text(encode(changed))
            with self.assertRaises(AssertionError):
                read_capture(path)
            path.write_text(encode(record))
            path.with_name(record["file"]).write_bytes(payload + b"changed")
            with self.assertRaises(AssertionError):
                read_capture(path)

    def test_transforms_match_calibration_bytes_including_black_padding(self):
        from mission_planner.agents.perception.infer_calibration import jpeg as calibration_jpeg

        _, payload = recorded()
        with Image.open(io.BytesIO(payload)) as original:
            expected = calibration_jpeg(original.resize((1920, 1080), Image.Resampling.LANCZOS))
            self.assertEqual(prepare_frame(payload), expected)
            for box in ([950, 500, 970, 550], [0, 0, 10, 20], [1900, 1060, 1920, 1080]):
                center = [(box[0] + box[2]), (box[1] + box[3])]
                native = [
                    round(value)
                    for value in [
                        center[0] - 256,
                        center[1] - 256,
                        center[0] + 256,
                        center[1] + 256,
                    ]
                ]
                expected = calibration_jpeg(
                    original.crop(tuple(native)).resize((512, 512), Image.Resampling.LANCZOS)
                )
                actual, actual_box, padding = crop(payload, box)
                self.assertEqual(actual, expected)
                self.assertEqual(actual_box, native)
                self.assertEqual(any(padding), box[0] in (0, 1900))
        native1080 = jpeg(Image.new("RGB", (1920, 1080), (255, 255, 255)))
        actual, box, padding = crop(native1080, [0, 0, 10, 10])
        self.assertEqual(box, [-123, -123, 133, 133])
        self.assertEqual(padding, [123, 123, 0, 0])
        with Image.open(io.BytesIO(actual)) as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))

    def test_only_exact_person_configuration_is_calibrated(self):
        record, payload = recorded()
        config, _ = load_configuration()
        config = {**config, "person_batching": "dedicated"}
        requests, questions = [], []

        def detect(image, prompts, threshold):
            requests.append(prompts)
            self.assertEqual(threshold, 0.3)
            return {p: [Detection((950, 500, 970, 550), 0.8, 50)] for p in prompts}

        def decide(images, question):
            questions.append(question)
            self.assertEqual(len(images), 1)
            return {
                "yes": config["glimmer_threshold"],
                "no": 1 - config["glimmer_threshold"],
                "outside_mass": 0.02,
            }

        with tempfile.TemporaryDirectory() as temporary:
            processor = Processor(
                temporary,
                ["person wearing red", "backpack"],
                config,
                SimpleNamespace(detect=detect),
                SimpleNamespace(decide=decide),
            )
            result = processor.process(
                record, payload, "fixture", "recorded", "Drone2", lambda stage: None
            )
        self.assertEqual(requests, [["person"], ["person wearing red", "backpack"]])
        self.assertEqual(questions[0], "Does this image show a person?")
        self.assertTrue(result["calibrated_complete"])
        self.assertEqual([d["calibrated"] for d in result["detections"]], [True, False, False])
        self.assertTrue(all(d["qualified"] for d in result["detections"]))
        self.assertTrue(all(d["drone_id"] == "Drone1" for d in result["detections"]))


class Queue(unittest.IsolatedAsyncioTestCase):
    async def test_failed_frame_is_terminal_and_next_frame_preserves_order(self):
        completed, calls = [], []
        record, payload = recorded()
        rows = {str(i): {**record, "timestampNs": record["timestampNs"] + i} for i in range(3)}

        def process(record, payload, identity, source, lane, stage):
            calls.append(record["timestampNs"])
            stage("SAM")
            if record["timestampNs"] == rows["0"]["timestampNs"]:
                raise RuntimeError("inference failed")
            return {"frame_id": identity, "timings": {}}

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch(
                "mission_planner.agents.perception.ingestion.read_capture",
                side_effect=lambda path: (rows[str(path)], payload),
            ),
        ):
            ingestion = Ingestion(
                temporary,
                "recorded",
                SimpleNamespace(process=process),
                lambda result: completed.append(result["frame_id"]),
                lambda: None,
            )
            ids = [await ingestion.ingest(str(i), "Drone1") for i in range(3)]
            with self.assertRaises(AssertionError):
                await ingestion.ingest("2", "Drone1")
            with self.assertRaises(AssertionError):
                await ingestion.ingest("2")
            await asyncio.wait_for(ingestion.close(), timeout=10)
            summary = ingestion.summary()
            self.assertEqual(
                [f["status"] for f in summary["frames"]], ["failed", "complete", "complete"]
            )
            self.assertEqual(summary["frames"][0]["error"], "RuntimeError: inference failed")
            self.assertEqual(summary["frames"][0]["failure_stage"], "SAM")
            self.assertFalse(summary["frames"][0]["belief_committed"])
            self.assertEqual(completed, ids[1:])
            self.assertEqual(calls, [rows[str(i)]["timestampNs"] for i in range(3)])
            saved = json.loads(Path(temporary, "frames.json").read_text())
            self.assertEqual(saved[0]["status"], "failed")
            self.assertFalse(Path(temporary, ids[0] + "-result.json").exists())
            timings = json.loads(Path(temporary, ids[-1] + "-result.json").read_text())["timings"]
            self.assertLessEqual(timings["received_at_ms"], timings["queued_at_ms"])
            self.assertGreaterEqual(
                timings["record_arrival_to_belief_s"], timings["release_to_belief_s"]
            )
            with self.assertRaises(AssertionError):
                await ingestion.ingest("2", "Drone1")

    async def test_live_source_rejects_frozen_recording(self):
        record, payload = recorded()
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch(
                "mission_planner.agents.perception.ingestion.read_capture",
                return_value=(record, payload),
            ),
        ):
            ingestion = Ingestion(temporary, "live", None, None, lambda: None)
            with self.assertRaises(AssertionError):
                await ingestion.ingest("capture.json")
            self.assertFalse(ingestion.frames)
            await ingestion.close()

    async def test_failed_display_after_commit_keeps_explicit_belief_marker(self):
        record, payload = recorded()

        def commit(result):
            result["belief"] = {"kind": "negative"}
            raise RuntimeError("display failed after belief commit")

        def process(record, payload, identity, source, lane, stage):
            return {"frame_id": identity, "timings": {}}

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch(
                "mission_planner.agents.perception.ingestion.read_capture",
                return_value=(record, payload),
            ),
        ):
            ingestion = Ingestion(
                temporary, "recorded", SimpleNamespace(process=process), commit, lambda: None
            )
            identity = await ingestion.ingest("capture.json", "Drone1")
            await ingestion.close()
            state = ingestion.frames[identity]
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["failure_stage"], "Belief")
            self.assertTrue(state["belief_committed"])


if __name__ == "__main__":
    unittest.main()

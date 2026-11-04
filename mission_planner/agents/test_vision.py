"""SAM session identity, source geometry, mask integrity and bounded concurrency."""

import asyncio
import base64
import copy
import hashlib
import io
import json
import os
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import httpx
import numpy as np
from PIL import Image

from mission_planner.agents.services.sam_video import (
    LiveFrames,
    SamVideo,
    detections,
    encode_mask,
    source_box,
)
from mission_planner.agents.services.vision import VideoService
from mission_planner.agents.vision import VisionTrack, check_target, decode_mask, vision_http


def jpeg(size=(3840, 2160)):
    stream = io.BytesIO()
    Image.new("RGB", size).save(stream, format="JPEG")
    return stream.getvalue()


def mask_box(box):
    x1, y1, x2, y2 = (int(v) for v in box)
    stream = io.BytesIO()
    Image.new("L", (x2 - x1, y2 - y1), 255).save(stream, format="PNG")
    return {"origin_xy": [x1, y1], "png_base64": base64.b64encode(stream.getvalue()).decode()}


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.image = jpeg()
        self.calls = []
        self.mutate = lambda action, result: result

        def respond(request):
            action = request.url.path.rsplit("/", 1)[1]
            if action == "frame":
                self.assertEqual(request.headers["content-type"], "image/jpeg")
                body = dict(request.url.params)
                for key in ("frame_index", "timestamp_ns"):
                    body[key] = int(body[key])
                body["jpeg"] = request.content
            else:
                body = json.loads(request.content)
            self.calls.append(body)
            if action == "start":
                result = {
                    **body,
                    "instance_id": "worker-1",
                    "model": "sam3.1",
                    "tracker": "Object Multiplex",
                }
            elif action == "close":
                result = {**body, "status": "closed"}
            else:
                result = {k: v for k, v in body.items() if k != "jpeg"}
                result.update(
                    width=3840,
                    height=2160,
                    inference_height=1080,
                    sha256=hashlib.sha256(body["jpeg"]).hexdigest(),
                    detections=[
                        {
                            "track_id": 1,
                            "class_name": "bear",
                            "box_xyxy": [100, 200, 500, 700],
                            "confidence": 0.8,
                            "observations": body["frame_index"] + 1,
                            "mask": mask_box([100, 200, 500, 700]),
                        }
                    ],
                )
            return httpx.Response(200, json=self.mutate(action, result))

        self.http = httpx.Client(
            base_url="https://vision.test/", transport=httpx.MockTransport(respond)
        )
        self.addCleanup(self.http.close)
        self.client = VisionTrack(self.http, "Drone1", "bear", 0.3)

    def test_original_jpeg_order_identity_and_prompt(self):
        self.client.open()
        first = self.client.advance(self.image, 100)
        second = self.client.advance(self.image, 200)
        self.assertEqual([first["frame_index"], second["frame_index"]], [0, 1])
        self.assertEqual(self.calls[1]["jpeg"], self.image)
        self.assertEqual(self.calls[0]["target"], "bear")
        self.client.close()
        with self.assertRaisesRegex(AssertionError, "already closed"):
            self.client.close()
        self.assertEqual(len(self.calls), 4)

    def test_distinct_drone_sessions_share_the_local_socket(self):
        with patch.dict(os.environ, {"SNR_VISION_SOCKET": "/tmp/vision-test.sock"}):
            clients = [VisionTrack(vision_http(), f"Drone{i}", "person", 0.3) for i in range(1, 5)]
        try:
            self.assertEqual(len({c.session_id for c in clients}), 4)
            self.assertEqual({str(c.http.base_url) for c in clients}, {"http://vision/"})
            self.assertEqual({c.drone_id for c in clients}, {f"Drone{i}" for i in range(1, 5)})
            self.assertTrue(all(c.height == 1080 for c in clients))
        finally:
            for client in clients:
                client.http.close()

    def test_visible_prompts_and_fixed_qualified_preparation(self):
        for target in ("person", "brown bear", "orange life jacket", "cabin roof"):
            check_target(target)
        for height in (720, 640, 1088, 2160):
            with self.assertRaisesRegex(AssertionError, "1080p preparation"):
                VisionTrack(self.http, "Drone1", "bear", 0.3, height)
        self.client.open()
        with self.assertRaisesRegex(AssertionError, "original 3840x2160"):
            self.client.advance(jpeg((1920, 1080)), 1)
        self.assertEqual(len(self.calls), 1)

    def test_wrong_identity_source_size_hash_and_duplicate_track_fail_once(self):
        changes = (
            {"instance_id": "another-worker"},
            {"timestamp_ns": 12},
            {"sha256": "wrong"},
            {"width": 1920},
            {"frame_index": 5},
            {"inference_height": 2160},
        )
        for change in changes:
            with self.subTest(change=change):
                self.mutate = lambda action, result: (
                    {**result, **change} if action == "frame" else result
                )
                client = VisionTrack(self.http, "Drone1", "bear", 0.3)
                client.open()
                before = len(self.calls)
                with self.assertRaises(AssertionError):
                    client.advance(self.image, 10)
                with self.assertRaisesRegex(AssertionError, "not active"):
                    client.advance(self.image, 11)
                self.assertEqual(len(self.calls), before + 1)
        self.mutate = lambda action, result: (
            {**result, "detections": result["detections"] * 2} if action == "frame" else result
        )
        self.client.open()
        with self.assertRaisesRegex(AssertionError, "duplicate identities"):
            self.client.advance(self.image, 100)

    def test_timeout_is_terminal_and_never_retried(self):
        self.client.open()
        with patch.object(self.http, "send", side_effect=httpx.ReadTimeout("timed out")) as send:
            with self.assertRaises(httpx.ReadTimeout):
                self.client.advance(self.image, 10)
            with self.assertRaisesRegex(AssertionError, "not active"):
                self.client.advance(self.image, 20)
            send.assert_called_once()

    def test_missing_mask_is_terminal(self):
        def remove_mask(action, result):
            if action == "frame":
                del result["detections"][0]["mask"]
            return result

        self.mutate = remove_mask
        self.client.open()
        with self.assertRaisesRegex(AssertionError, "fields"):
            self.client.advance(self.image, 1)
        with self.assertRaisesRegex(AssertionError, "not active"):
            self.client.advance(self.image, 2)


class MaskTests(unittest.TestCase):
    def test_mask_crop_roundtrip_preserves_holes_and_original_coordinates(self):
        pixels = np.zeros((1080, 1920), dtype=bool)
        pixels[100:110, 300:320] = True
        pixels[103:107, 306:310] = False
        encoded = encode_mask(pixels)
        png, size = decode_mask(encoded, 3840, 2160)
        self.assertEqual(encoded["origin_xy"], [600, 200])
        self.assertEqual(size, [40, 20])
        with Image.open(io.BytesIO(png)) as image:
            actual = np.asarray(image) > 0
        expected = np.repeat(np.repeat(pixels[100:110, 300:320], 2, axis=0), 2, axis=1)
        np.testing.assert_array_equal(actual, expected)

    def test_invalid_mask_formats_bounds_binary_values_and_empty_masks_raise(self):
        valid = mask_box([10, 20, 30, 40])
        invalid = [
            {**valid, "png_base64": "invalid!"},
            {**valid, "origin_xy": [-1, 0]},
            {**valid, "origin_xy": [3830, 2150]},
            {**valid, "extra": 1},
        ]
        for mode, color in (("L", 0), ("L", 127), ("RGB", "white")):
            stream = io.BytesIO()
            Image.new(mode, (20, 20), color).save(stream, format="PNG")
            invalid.append({**valid, "png_base64": base64.b64encode(stream.getvalue()).decode()})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((AssertionError, ValueError)):
                decode_mask(value, 3840, 2160)
        with self.assertRaisesRegex(AssertionError, "nonempty"):
            encode_mask(np.zeros((1080, 1920), dtype=bool))

    def test_sam_normalized_xywh_is_projected_without_padding(self):
        self.assertEqual(source_box([0.25, 0.25, 0.25, 0.25]), [960, 540, 1920, 1080])
        for box in ([-0.1, 0, 0.2, 0.2], [0, 0, 0, 1], [0.9, 0, 0.2, 1], [float("nan"), 0, 1, 1]):
            with self.assertRaises(AssertionError):
                source_box(box)

    def test_native_zero_id_and_consecutive_valid_observations(self):
        masks = np.zeros((1, 1080, 1920), dtype=bool)
        masks[0, 100:111, 200:221] = True
        prediction = {
            "out_obj_ids": np.array([0]),
            "out_probs": np.array([0.8]),
            "out_boxes_xywh": np.array([[200 / 1920, 100 / 1080, 20 / 1920, 10 / 1080]]),
            "out_binary_masks": masks,
            "frame_stats": None,
        }
        first = detections(prediction, "person", 0.3, {})
        self.assertEqual((first[0]["track_id"], first[0]["observations"]), (1, 1))
        second = detections(prediction, "person", 0.3, {1: 1})
        self.assertEqual(second[0]["observations"], 2)
        self.assertEqual(detections(prediction, "person", 0.9, {1: 2}), [])
        self.assertEqual(detections(prediction, "person", 0.3, {})[0]["observations"], 1)
        dropped = copy.deepcopy(prediction)
        dropped["frame_stats"] = {"num_obj_dropped": 1}
        with self.assertRaisesRegex(AssertionError, "capacity"):
            detections(dropped, "person", 0.3, {})

    def test_reader_never_accesses_future_pixels_and_retains_two_images(self):
        frames = LiveFrames(16)
        for index in range(100):
            frames.append(index)
            self.assertEqual(frames[index], index)
            self.assertLessEqual(len(frames.images), 2)
            with self.assertRaisesRegex(AssertionError, "unavailable"):
                frames[index + 1]
        self.assertEqual(len(frames), 100)
        with self.assertRaises(KeyError):
            frames[0]


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.model = Mock(compiled=True)
        self.model.compile_stats.return_value = {"unique_graphs": 13}
        adapter = object.__new__(SamVideo)
        adapter.model = Mock()
        language = adapter.model.detector.backbone.language_backbone
        language.context_length = 32
        language.tokenizer.encode.side_effect = str.split
        self.model.open.side_effect = adapter.open
        self.model.usage.return_value = {
            "frames": 0,
            "retained_images": 0,
            "retained_tracking_frames": 0,
        }
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.service = VideoService(self.model, self.executor)
        self.addCleanup(self.executor.shutdown)
        self.addCleanup(self.service.preparation.shutdown)

    def open(self, index):
        return self.service.start(
            {
                "session_id": str(uuid.uuid4()),
                "drone_id": f"Drone{index}",
                "target": f"person {index}",
                "threshold": 0.3,
                "inference_height": 1080,
            }
        )

    def body(self, opened, index=0):
        return {
            "session_id": opened["session_id"],
            "instance_id": opened["instance_id"],
            "frame_index": index,
            "timestamp_ns": index + 1,
            "jpeg": b"jpeg",
        }

    async def test_one_model_serializes_gpu_with_parallel_preparation_and_four_states(self):
        opened = [self.open(i) for i in range(1, 5)]
        self.assertEqual(len({o["instance_id"] for o in opened}), 1)
        self.assertEqual(len({id(r["state"]) for r in self.service.sessions.values()}), 4)
        barrier = threading.Barrier(4)
        preparations, inferences = set(), set()
        calls = []

        def prepare(data):
            preparations.add(threading.get_ident())
            barrier.wait(timeout=2)
            return data

        def infer(state, prepared):
            inferences.add(threading.get_ident())
            calls.append(state["target"])
            time.sleep(0.002)
            return {"detections": [], "timings": {}}

        self.model.advance.side_effect = infer
        with patch("mission_planner.agents.services.vision.prepare_frame", side_effect=prepare):
            results = await asyncio.gather(*(self.service.advance(self.body(o)) for o in opened))
        self.assertEqual(len(preparations), 4)
        self.assertEqual(len(inferences), 1)
        self.assertEqual(set(calls), {f"person {i}" for i in range(1, 5)})
        self.assertEqual([r["frame_index"] for r in results], [0] * 4)
        for opened_session in opened:
            await self.service.close(
                {key: opened_session[key] for key in ("session_id", "instance_id")}
            )
        self.assertFalse(self.service.sessions)

    async def test_busy_session_rejects_second_frame_and_other_drones_queue_fairly(self):
        opened = [self.open(i) for i in range(1, 5)]
        entered, finish = threading.Event(), threading.Event()
        order = []

        def infer(state, prepared):
            order.append(state["target"])
            if len(order) == 1:
                entered.set()
                assert finish.wait(2)
            return {"detections": [], "timings": {}}

        self.model.advance.side_effect = infer
        with patch("mission_planner.agents.services.vision.prepare_frame", return_value={}):
            first = asyncio.create_task(self.service.advance(self.body(opened[0])))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            try:
                with self.assertRaisesRegex(AssertionError, "busy"):
                    await self.service.advance(self.body(opened[0]))
                others = []
                for row in opened[1:]:
                    others.append(asyncio.create_task(self.service.advance(self.body(row))))
                    await asyncio.sleep(0.01)
            finally:
                finish.set()
            await asyncio.gather(first, *others)
        self.assertEqual(order, ["person 1", "person 2", "person 3", "person 4"])

    async def test_gpu_failure_is_terminal_without_session_repair(self):
        opened = self.open(1)
        self.model.advance.side_effect = RuntimeError("CUDA failure")
        with patch("mission_planner.agents.services.vision.prepare_frame", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "CUDA failure"):
                await self.service.advance(self.body(opened))
            with self.assertRaisesRegex(AssertionError, "CUDA failure"):
                await self.service.advance(self.body(opened))
        self.model.advance.assert_called_once()
        self.assertEqual(self.service.health()["status"], "failed")
        self.assertEqual(self.service.sessions[opened["session_id"]]["status"], "failed")

    async def test_duplicate_drone_and_out_of_order_frame_never_run_inference(self):
        opened = self.open(1)
        with self.assertRaisesRegex(AssertionError, "already has"):
            self.open(1)
        with self.assertRaisesRegex(AssertionError, "out of order"):
            await self.service.advance(self.body(opened, 2))
        self.model.advance.assert_not_called()

    async def test_prompt_is_rejected_before_native_tokenizer_can_truncate_it(self):
        opened = self.open(1)
        body = {
            key: opened[key]
            for key in ("session_id", "drone_id", "target", "threshold", "inference_height")
        }
        body.update(session_id=str(uuid.uuid4()), drone_id="Drone2", target="x " * 31)
        with self.assertRaisesRegex(AssertionError, "30 text-token budget"):
            self.service.start(body)
        self.assertEqual(len(self.service.sessions), 1)


if __name__ == "__main__":
    unittest.main()

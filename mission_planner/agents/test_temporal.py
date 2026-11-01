import io
import json
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import httpx
import numpy as np
from PIL import Image

from mission_planner.agents import segmentation
from mission_planner.agents.segmentation import encode_mask
from mission_planner.agents.services.sam_tracking import (
    VideoTracks,
    check_update,
    extract_mask,
    frame,
    trim_state,
)
from mission_planner.agents.temporal import TemporalTrack


class TemporalClientTests(unittest.TestCase):
    def setUp(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1920, 1080), "white").save(buffer, format="JPEG")
        self.jpeg = buffer.getvalue()
        self.detection = {
            "box_xyxy": [10, 20, 16, 28],
            "confidence": 0.8,
            "mask_area_px": 48,
            "mask": encode_mask(np.ones((8, 6), dtype=bool), [10, 20]),
        }
        self.calls = []

    def response(self, request):
        body = json.loads(request.content)
        self.calls.append((request.url.path, body))
        if request.url.path.endswith("/close"):
            return httpx.Response(200, json={**body, "status": "closed"})
        seeded = request.url.path.endswith("/start")
        return httpx.Response(
            200,
            json={
                "session_id": body["session_id"],
                "instance_id": "sam-instance",
                "frame_index": body["frame_index"],
                "timestamp_ns": body["timestamp_ns"],
                "width": 1920,
                "height": 1080,
                "status": "seeded" if seeded else "tracking",
                "detection": None if seeded else self.detection,
                "error": None,
                "raw_prediction": None
                if seeded
                else {
                    "mask": self.detection["mask"],
                    "mask_area_px": self.detection["mask_area_px"],
                    "presence_logit": 1.3862943611198906,
                },
                "extraction": None
                if seeded
                else {
                    "area_threshold_px": 16,
                    "component_areas_px": [self.detection["mask_area_px"]],
                    "removed_component_areas_px": [],
                    "removed_pixels": 0,
                    "remaining_components": 1,
                },
            },
        )

    def client(self, handler=None):
        http = httpx.Client(
            base_url="https://sam.example/", transport=httpx.MockTransport(handler or self.response)
        )
        self.addCleanup(http.close)
        return TemporalTrack(http)

    def test_ordered_remote_track_uses_exact_seed_and_echoes(self):
        track = self.client()
        timing = {}
        seeded = track.start(self.jpeg, 100, self.detection, 0.5, timing)
        self.assertIsNone(seeded["detection"])
        self.assertIs(seeded["client_timing"], timing)
        self.assertTrue(timing["complete"])
        self.assertEqual(timing["status_code"], 200)
        self.assertGreaterEqual(timing["received_at_unix_s"], timing["started_at_unix_s"])
        self.assertGreaterEqual(timing["completed_at_unix_s"], timing["received_at_unix_s"])
        result = track.advance(self.jpeg, 200)
        self.assertIsNot(result["client_timing"], timing)
        self.assertGreaterEqual(result["client_timing"]["roundtrip_s"], 0)
        self.assertEqual(result["detection"], self.detection)
        self.assertEqual(result["frame_index"], 1)
        track.close()
        self.assertEqual(
            [p for p, _ in self.calls], ["/tracking/start", "/tracking/frame", "/tracking/close"]
        )
        self.assertEqual(set(self.calls[0][1]["seed"]), {"box_xyxy", "mask_area_px", "mask"})
        self.assertEqual(self.calls[1][1]["instance_id"], "sam-instance")
        self.assertEqual(track.http.timeout.read, 5)

    def test_frame_timestamp_must_advance_before_network(self):
        track = self.client()
        track.start(self.jpeg, 100, self.detection, 0.5)
        with self.assertRaisesRegex(AssertionError, "timestamp must increase"):
            track.advance(self.jpeg, 100)
        self.assertEqual(len(self.calls), 1)

    def test_failed_start_is_never_retried(self):
        def failure(request):
            self.calls.append(request)
            return httpx.Response(503)

        track = self.client(failure)
        with self.assertRaises(httpx.HTTPStatusError):
            track.start(self.jpeg, 100, self.detection, 0.5)
        with self.assertRaisesRegex(AssertionError, "only be started once"):
            track.start(self.jpeg, 100, self.detection, 0.5)
        track.close()
        self.assertEqual(len(self.calls), 1)

    def test_replaced_container_revokes_track(self):
        def replaced(request):
            response = self.response(request)
            data = response.json()
            if request.url.path.endswith("/frame"):
                data["instance_id"] = "other-instance"
            return httpx.Response(200, json=data)

        track = self.client(replaced)
        track.start(self.jpeg, 100, self.detection, 0.5)
        with self.assertRaisesRegex(AssertionError, "container changed"):
            track.advance(self.jpeg, 200)
        with self.assertRaisesRegex(AssertionError, "not active"):
            track.advance(self.jpeg, 300)
        self.assertEqual(len(self.calls), 2)

    def test_lost_object_stops_without_reacquisition(self):
        def lost(request):
            response = self.response(request)
            data = response.json()
            if request.url.path.endswith("/frame"):
                data.update(status="lost", detection=None, error="SAM target is not visible")
            return httpx.Response(200, json=data)

        track = self.client(lost)
        track.start(self.jpeg, 100, self.detection, 0.5)
        self.assertEqual(track.advance(self.jpeg, 200)["status"], "lost")
        with self.assertRaisesRegex(AssertionError, "not active"):
            track.advance(self.jpeg, 300)
        track.close()
        self.assertEqual(len(self.calls), 3)

    def test_invalid_response_status_revokes_track(self):
        def invalid(request):
            data = self.response(request).json()
            if request.url.path.endswith("/frame"):
                data.update(status="seeded", detection=None)
            return httpx.Response(200, json=data)

        track = self.client(invalid)
        track.start(self.jpeg, 100, self.detection, 0.5)
        with self.assertRaisesRegex(AssertionError, "invalid lifecycle status"):
            track.advance(self.jpeg, 200)
        with self.assertRaisesRegex(AssertionError, "not active"):
            track.advance(self.jpeg, 300)
        self.assertEqual(len(self.calls), 2)

    def test_mask_probability_and_identity_are_checked(self):
        for field, value in (("frame_index", 99), ("timestamp_ns", 999), ("session_id", "wrong")):
            with self.subTest(field=field):

                def incorrect(request):
                    data = self.response(request).json()
                    data[field] = value
                    return httpx.Response(200, json=data)

                with self.assertRaises(AssertionError):
                    self.client(incorrect).start(self.jpeg, 100, self.detection, 0.5)
        track = self.client()
        track.start(self.jpeg, 100, self.detection, 0.5)
        self.detection["mask_area_px"] = 47
        with self.assertRaisesRegex(AssertionError, "area disagrees"):
            track.advance(self.jpeg, 200)

    def test_service_validates_frame(self):
        track = self.client()
        body = track._frame(self.jpeg, 100)
        self.assertEqual(frame(body).size, (1920, 1080))
        body["frame_index"] = True
        with self.assertRaises(AssertionError):
            frame(body)


class TemporalExtractionTests(unittest.TestCase):
    def test_sprinkles_removed_before_geometry_without_mutating_raw_mask(self):
        raw = np.zeros((1080, 1920), dtype=bool)
        raw[100:110, 100:110] = True
        raw[300:304, 400:404] = True
        raw[700, 900] = True
        original = raw.copy()
        pixels, stats = extract_mask(raw)
        np.testing.assert_array_equal(raw, original)
        self.assertFalse(np.shares_memory(raw, pixels))
        self.assertEqual(stats["component_areas_px"], [100, 16, 1])
        self.assertEqual(stats["removed_component_areas_px"], [16, 1])
        self.assertEqual(stats["removed_pixels"], 17)
        self.assertEqual(stats["remaining_components"], 1)
        self.assertEqual(int(pixels.sum()), 100)
        ys, xs = pixels.nonzero()
        self.assertEqual([xs.min(), ys.min(), xs.max() + 1, ys.max() + 1], [100, 100, 110, 110])

    def test_seventeen_pixel_region_is_not_a_sprinkle(self):
        raw = np.zeros((1080, 1920), dtype=bool)
        raw[100:110, 100:110] = True
        raw[300, 400:417] = True
        pixels, stats = extract_mask(raw)
        np.testing.assert_array_equal(pixels, raw)
        self.assertEqual(stats["area_threshold_px"], 16)
        self.assertEqual(stats["removed_pixels"], 0)
        self.assertEqual(stats["remaining_components"], 2)

    def test_sole_small_object_is_preserved(self):
        for area in (1, 2, 16, 17):
            with self.subTest(area=area):
                raw = np.zeros((1080, 1920), dtype=bool)
                raw[100, 100 : 100 + area] = True
                pixels, stats = extract_mask(raw)
                np.testing.assert_array_equal(pixels, raw)
                self.assertEqual(stats["area_threshold_px"], min(16, area // 2))
                self.assertEqual(stats["remaining_components"], 1)

    def test_diagonal_is_connected_and_holes_are_not_filled(self):
        raw = np.zeros((1080, 1920), dtype=bool)
        raw[100:110, 100:110] = True
        raw[104:106, 104:106] = False
        raw[110, 110] = True
        pixels, stats = extract_mask(raw)
        np.testing.assert_array_equal(pixels, raw)
        self.assertEqual(stats["component_areas_px"], [97])

    def test_empty_foreground_and_equal_small_regions_are_explicit(self):
        raw = np.zeros((1080, 1920), dtype=bool)
        pixels, stats = extract_mask(raw)
        self.assertFalse(pixels.any())
        self.assertEqual(stats["component_areas_px"], [])
        self.assertEqual(stats["remaining_components"], 0)
        raw[100:104, 100:104] = raw[200:204, 200:204] = True
        pixels, stats = extract_mask(raw)
        self.assertFalse(pixels.any())
        self.assertEqual(stats["removed_component_areas_px"], [16, 16])
        self.assertEqual(stats["remaining_components"], 0)


class TemporalMemoryTests(unittest.TestCase):
    def test_seed_reaches_sam_exactly_without_extraction(self):
        pixels = np.zeros((1080, 1920), dtype=bool)
        pixels[100:110, 100:110] = True
        pixels[300, 400] = True
        seed = {
            "box_xyxy": [100, 100, 401, 301],
            "mask_area_px": 101,
            "mask": encode_mask(pixels, [0, 0]),
        }
        tracks = VideoTracks.__new__(VideoTracks)
        tracks.instance_id, tracks.sessions = "sam-instance", {}
        tracks._prepare = Mock()
        tracks.model = Mock()

        def add_masks(state, index, identities, masks):
            np.testing.assert_array_equal(masks, pixels[None])
            return index, identities, None, None

        tracks.model.add_new_masks.side_effect = add_masks
        torch = SimpleNamespace(
            inference_mode=nullcontext,
            autocast=lambda *args, **kwargs: nullcontext(),
            bfloat16=object(),
            from_numpy=lambda values: values,
            cuda=SimpleNamespace(synchronize=Mock()),
        )
        body = {
            "session_id": "test",
            "frame_index": 0,
            "timestamp_ns": 100,
            "jpeg": "unused",
            "seed": seed,
            "threshold": 0.5,
        }
        with (
            patch.dict("sys.modules", {"torch": torch, "segmentation": segmentation}),
            patch(
                "mission_planner.agents.services.sam_tracking.frame",
                return_value=Image.new("RGB", (1920, 1080)),
            ),
            patch("mission_planner.agents.services.sam_tracking.extract_mask") as extract,
        ):
            result = tracks.start(body)
        extract.assert_not_called()
        self.assertEqual(result["status"], "seeded")
        self.assertIsNone(result["detection"])
        self.assertIsNone(result["raw_prediction"])
        self.assertIsNone(result["extraction"])

    def advance(self, pixels):
        logits = Mock()
        logits.numel.return_value = 1
        logits.sigmoid.return_value.item.return_value = 0.8
        logits.item.return_value = 1.3862943611198906
        masks = MagicMock(shape=(1, 1, 1080, 1920))
        thresholded = Mock()
        thresholded.cpu.return_value.numpy.return_value = pixels
        masks.__getitem__.return_value.__gt__.return_value = thresholded
        tracks = VideoTracks.__new__(VideoTracks)
        tracks.instance_id = "sam-instance"
        tracks._prepare = Mock()
        tracks.model = Mock()
        tracks.model.propagate_in_video.return_value = [(1, [1], None, masks, logits)]
        tracks.sessions = {
            "test": {
                "state": {},
                "status": "seeded",
                "frame_index": 0,
                "timestamp_ns": 100,
                "threshold": 0.5,
            }
        }
        torch = SimpleNamespace(
            inference_mode=nullcontext,
            autocast=lambda *args, **kwargs: nullcontext(),
            bfloat16=object(),
            isfinite=lambda values: np.isfinite([values.sigmoid().item()]),
            cuda=SimpleNamespace(synchronize=Mock()),
        )
        body = {
            "session_id": "test",
            "instance_id": "sam-instance",
            "frame_index": 1,
            "timestamp_ns": 200,
            "jpeg": "unused",
        }
        with (
            patch.dict("sys.modules", {"torch": torch, "segmentation": segmentation}),
            patch("mission_planner.agents.services.sam_tracking.frame"),
            patch("mission_planner.agents.services.sam_tracking.trim_state"),
        ):
            result = tracks.advance(body)
        return result, tracks, body

    def test_sam31_propagation_returns_masks_and_presence_logits(self):
        pixels = np.zeros((1080, 1920), dtype=bool)
        pixels[20:28, 10:16] = True
        pixels[20, 900] = True
        result, _, _ = self.advance(pixels)
        self.assertEqual(result["status"], "tracking")
        self.assertEqual(result["detection"]["box_xyxy"], [10, 20, 16, 28])
        self.assertEqual(result["detection"]["confidence"], 0.8)
        self.assertEqual(result["detection"]["mask_area_px"], 48)
        self.assertEqual(result["raw_prediction"]["mask_area_px"], 49)
        self.assertEqual(result["extraction"]["removed_pixels"], 1)
        _, _, raw = segmentation.decode_mask(result["raw_prediction"]["mask"], 1920, 1080, 49)
        np.testing.assert_array_equal(raw, pixels)

    def test_ambiguous_regions_lose_track_and_retain_raw_evidence(self):
        pixels = np.zeros((1080, 1920), dtype=bool)
        pixels[20:28, 10:16] = True
        pixels[300, 400:417] = True
        result, tracks, body = self.advance(pixels)
        self.assertEqual(result["status"], "lost")
        self.assertIsNone(result["detection"])
        self.assertIn("2 foreground components", result["error"])
        self.assertEqual(result["extraction"]["remaining_components"], 2)
        self.assertEqual(result["raw_prediction"]["presence_logit"], 1.3862943611198906)
        _, _, raw = segmentation.decode_mask(result["raw_prediction"]["mask"], 1920, 1080, 65)
        np.testing.assert_array_equal(raw, pixels)
        with self.assertRaisesRegex(AssertionError, "cannot continue"):
            check_update(tracks.sessions["test"], {**body, "frame_index": 2, "timestamp_ns": 300})

    def test_unchanged_observation_reuses_lossless_encoding(self):
        pixels = np.zeros((1080, 1920), dtype=bool)
        pixels[100:110, 100:110] = True
        result, _, _ = self.advance(pixels)
        self.assertEqual(result["status"], "tracking")
        self.assertIs(result["detection"]["mask"], result["raw_prediction"]["mask"])

    def test_empty_prediction_is_retained_as_exact_empty_foreground(self):
        result, _, _ = self.advance(np.zeros((1080, 1920), dtype=bool))
        self.assertEqual(result["status"], "lost")
        self.assertEqual(result["raw_prediction"]["mask_area_px"], 0)
        self.assertIsNone(result["raw_prediction"]["mask"])
        self.assertEqual(result["extraction"]["component_areas_px"], [])

    def test_explicit_acquisition_is_distinct_from_continuing_a_track(self):
        row = {"status": "seeded", "frame_index": 0, "timestamp_ns": 1_000_000_000}
        body = {"frame_index": 1, "timestamp_ns": 11_000_000_000}
        check_update(row, body)
        row["status"] = "tracking"
        with self.assertRaisesRegex(AssertionError, "gap longer than two seconds"):
            check_update(row, body)
        body["timestamp_ns"] = 2_000_000_000
        check_update(row, body)
        body["frame_index"] = 3
        with self.assertRaisesRegex(AssertionError, "consecutive"):
            check_update(row, body)

    def test_feature_cache_excludes_flat_detector_outputs(self):
        tracks = VideoTracks.__new__(VideoTracks)
        pixels, batch = Mock(), object()
        pixels.cuda.return_value.unsqueeze.return_value = batch
        tracks._image = Mock(return_value=pixels)
        tracking_heads = {"interactive": {}, "sam2_backbone_out": {}}

        def forward(image, **flags):
            self.assertIs(image.tensors, batch)
            self.assertIsNone(image.mask)
            return (
                {**tracking_heads, "vision_features": np.zeros((1, 1, 1, 1))}
                if flags["need_sam3_out"]
                else tracking_heads
            )

        tracks.model = SimpleNamespace(forward_image=forward)
        module = SimpleNamespace(NestedTensor=SimpleNamespace)
        state = {}
        with patch.dict("sys.modules", {"sam3.model.data_misc": module}):
            tracks._prepare(state, 7, object())
        self.assertEqual(set(state["cached_features"][7][1]), {"interactive", "sam2_backbone_out"})
        self.assertEqual(set(state["images"]), {7})

    def test_long_sequence_retains_seed_and_recent_memory(self):
        state = {
            "images": {99: object()},
            "cached_features": {99: object()},
            "output_dict": {
                "cond_frame_outputs": {0: object()},
                "non_cond_frame_outputs": dict.fromkeys(range(1, 100)),
            },
            "output_dict_per_obj": {
                0: {
                    "cond_frame_outputs": {0: object()},
                    "non_cond_frame_outputs": dict.fromkeys(range(1, 100)),
                }
            },
            "frames_already_tracked": dict.fromkeys(range(100)),
        }
        trim_state(state, 99)
        self.assertEqual(set(state["output_dict"]["cond_frame_outputs"]), {0})
        self.assertEqual(set(state["output_dict"]["non_cond_frame_outputs"]), set(range(84, 100)))
        self.assertEqual(
            set(state["output_dict_per_obj"][0]["non_cond_frame_outputs"]), set(range(84, 100))
        )
        self.assertEqual(set(state["frames_already_tracked"]), {0, *range(84, 100)})

    def test_abandoned_session_lease_releases_slot(self):
        tracks = VideoTracks.__new__(VideoTracks)
        tracks.sessions = {"expired": {"updated": 1}, "current": {"updated": 40}}
        with patch("mission_planner.agents.services.sam_tracking.time.monotonic", return_value=50):
            self.assertEqual(tracks.expire(), ["expired"])
        self.assertEqual(set(tracks.sessions), {"current"})

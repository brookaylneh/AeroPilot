"""Forward evidence preserves full candidates and makes uncertainty explicit."""

import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from mission_planner.agents.review import TEMPLATE, instructions, review_candidate


class ReviewTests(unittest.TestCase):
    def setUp(self):
        output = io.BytesIO()
        Image.new("RGB", (3840, 2160), (120, 30, 90)).save(output, format="JPEG")
        self.jpeg = output.getvalue()
        self.look_for = {
            "objective": "Find the missing hiker",
            "target": "Person described by the operator",
            "attributes": [
                {"id": "jacket", "text": "Red jacket"},
                {"id": "backpack", "text": "Blue backpack"},
            ],
            "ignore": [],
        }
        self.metadata = {
            "frame_id": "00000000-0000-4000-8000-000000000001",
            "sha256": hashlib.sha256(self.jpeg).hexdigest(),
            "drone_id": "Drone2",
            "captured_at_ms": 123456789,
            "camera_id": "evidence_zoom",
            "zoom": 4,
            "source_frame_id": "00000000-0000-4000-8000-000000000002",
            "source_track_id": 17,
            "source_captured_at_ms": 123456000,
            "camera_position_world_m": {"x": 20, "y": 30, "z": 40},
        }
        self.detection = {"track_id": 17, "box_xyxy": [800, 200, 1000, 900], "confidence": 0.8}
        self.result = {
            "report": {
                "observations": [
                    {"attribute_id": "jacket", "visible": "yes", "note": "Red clothing visible"},
                    {"attribute_id": "backpack", "visible": "cannot_tell", "note": "Back obscured"},
                ],
                "reasoning": "Visible clothing is consistent; backpack cannot be assessed.",
                "decision": "recheck",
                "action": "other_angle",
                "reason": "Backpack obscured",
            },
            "reasoning_tokens": 12,
            "reasoning_content": "Measured reasoning",
            "usage": {"prompt_tokens": 30, "completion_tokens": 20},
            "timings": {},
        }
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.prefix = Path(self.temporary.name) / "candidate-0"
        self.glimmer = Mock()
        self.glimmer.report.return_value = self.result

    def review(self):
        return review_candidate(
            self.glimmer,
            self.look_for,
            "human",
            self.metadata,
            self.jpeg,
            self.detection,
            self.prefix,
        )

    def test_requested_bear_is_reviewed_as_bear_without_a_person_gate(self):
        self.look_for = {
            "objective": "Check the reported sighting",
            "target": "bear",
            "attributes": [],
            "ignore": [],
        }
        text = instructions(self.look_for, "bear", self.metadata, self.detection)
        self.assertIn("Target: bear", text)
        self.assertIn("requested target", text)
        self.assertNotIn("not a person", text)
        self.result["report"]["observations"] = []
        self.result["report"]["decision"] = "escalate"
        result = review_candidate(
            self.glimmer,
            self.look_for,
            "bear",
            self.metadata,
            self.jpeg,
            self.detection,
            self.prefix,
        )
        self.assertEqual(result["report"]["decision"], "escalate")
        self.assertIn("Target: bear", self.glimmer.report.call_args.args[2])

    def test_only_the_byte_identical_original_4k_frame_is_sent(self):
        self.review()
        self.assertEqual(self.glimmer.report.call_args.args[0], [self.jpeg])
        self.assertEqual(list(self.prefix.parent.iterdir()), [self.prefix.with_suffix(".json")])
        text = self.glimmer.report.call_args.args[2]
        self.assertIn("selected track 17", text)
        self.assertNotIn(str(self.detection["box_xyxy"]), text)

    def test_lower_resolution_review_is_rejected_before_request(self):
        stream = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(stream, format="JPEG")
        self.jpeg = stream.getvalue()
        with self.assertRaisesRegex(AssertionError, "original 3840x2160"):
            self.review()
        self.glimmer.report.assert_not_called()

    def test_forward_template_never_claims_nadir_scale_or_target_identity(self):
        text = instructions(self.look_for, "human", self.metadata, self.detection)
        self.assertIn("4x evidence camera on Drone2", text)
        self.assertIn("123456789", text)
        self.assertIn("backpack: Blue backpack", text)
        self.assertIn("cannot_tell, never evidence of a mismatch", text)
        self.assertIn("Do not establish personal identity", text)
        self.assertNotIn("View: nadir", text)
        self.assertEqual(TEMPLATE, "zoom-analyst-v4-single-photo")
        self.assertIn("empty zoom view does not disprove", text)
        self.assertNotIn(str(self.detection["box_xyxy"]), text)

    def test_uncertainty_and_reasoning_counts_are_preserved_with_original_capture(self):
        before = copy.deepcopy(self.metadata)
        result = self.review()
        self.assertEqual(result["report"]["decision"], "recheck")
        self.assertEqual(result["reasoning_tokens"], 12)
        self.assertEqual(self.metadata, before)
        log = json.loads(self.prefix.with_suffix(".json").read_text())
        self.assertEqual(log["metadata"], before)
        self.assertEqual(log["result"], self.result)
        self.assertIsNone(log["error"])
        self.assertEqual(len(log["images"]), 4)
        self.assertEqual(log["images"]["sha256"], hashlib.sha256(self.jpeg).hexdigest())
        self.glimmer.report.assert_called_once()

    def test_manifest_identifies_exact_review_images_without_changing_model_result(self):
        before = copy.deepcopy(self.result)
        result = self.review()
        self.assertEqual(self.result, before)
        self.assertEqual({key: value for key, value in result.items() if key != "images"}, before)
        manifest = result["images"]
        self.assertEqual(manifest["frame_id"], self.metadata["frame_id"])
        log = json.loads(self.prefix.with_suffix(".json").read_text())
        self.assertEqual(log["result"], before)
        self.assertEqual(manifest, log["images"])
        self.assertEqual(manifest["sha256"], hashlib.sha256(self.jpeg).hexdigest())
        self.assertEqual(self.glimmer.report.call_args.args[0], [self.jpeg])

    def test_missing_attribute_is_a_logged_failure_without_retry(self):
        self.result["report"]["observations"].pop()
        with self.assertRaisesRegex(RuntimeError, "each attribute exactly once"):
            self.review()
        log = json.loads(self.prefix.with_suffix(".json").read_text())
        self.assertIn("each attribute exactly once", log["error"])
        self.glimmer.report.assert_called_once()

    def test_uncertainty_without_an_advisory_action_remains_a_review_result(self):
        self.result["report"]["action"] = "none"
        expected = copy.deepcopy(self.result)
        result = self.review()
        self.assertEqual(result["report"], expected["report"])
        self.assertEqual(result["report"]["decision"], "recheck")
        log = json.loads(self.prefix.with_suffix(".json").read_text())
        self.assertEqual(log["result"], expected)
        self.assertIsNone(log["error"])
        self.glimmer.report.assert_called_once()

    def test_network_failure_is_logged_once_and_never_becomes_discard(self):
        self.glimmer.report.side_effect = TimeoutError("Glimmer timed out")
        with self.assertRaisesRegex(RuntimeError, "Glimmer timed out"):
            self.review()
        log = json.loads(self.prefix.with_suffix(".json").read_text())
        self.assertIsNone(log["result"])
        self.assertIn("TimeoutError", log["error"])
        self.glimmer.report.assert_called_once()


if __name__ == "__main__":
    unittest.main()

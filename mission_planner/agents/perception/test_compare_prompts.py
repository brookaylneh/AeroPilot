"""Prompt equivalence includes every candidate and both sides of qualification."""

import copy
import unittest

from mission_planner.agents.perception.compare_prompts import compare, compare_frame

THRESHOLD = 0.1329642298905696


def detection(box=(10, 10, 49, 49), score=0.8, yes=0.9):
    return {
        "prompt": "person",
        "box_xyxy": list(box),
        "sam_confidence": score,
        "glimmer_yes": yes,
        "outside_mass": 0.01,
        "qualified": yes >= THRESHOLD,
        "crop_ref": "crop.jpg",
    }


def frame(first, second):
    return {
        "frame_id": "one",
        "input_sha256": "a" * 64,
        "variants": {
            "dedicated": {"detections": first, "sam_responses": []},
            "combined": {"detections": second, "sam_responses": []},
        },
    }


class Equivalence(unittest.TestCase):
    def test_reordered_boxes_match_by_location_and_threshold_equality_qualifies(self):
        a = detection(yes=THRESHOLD)
        b = detection(box=(100, 100, 140, 140), yes=THRESHOLD - 1e-8)
        result = compare([frame([a, b], [b, a])], 0.3, THRESHOLD)
        self.assertTrue(result["all_equivalent"])
        self.assertEqual(result["minimum_observed_iou"], 1)
        self.assertEqual(result["changed_candidate_decisions"], 0)
        self.assertEqual(result["comparisons"][0]["qualified_candidates"], [1, 1])
        self.assertEqual(result["comparisons"][0]["unqualified_candidates"], [1, 1])

    def test_empty_frame_decision_is_equivalent_but_empty_corpus_is_not_proof(self):
        row = frame([], [])
        result = compare_frame(row, 0.3, THRESHOLD)
        self.assertTrue(result["equivalent"])
        self.assertTrue(result["same_frame_decision"])
        with self.assertRaisesRegex(AssertionError, "observed person"):
            compare([row], 0.3, THRESHOLD)

    def test_additional_unqualified_candidate_fails_even_with_same_frame_decision(self):
        a, b = detection(), detection(box=(100, 100, 140, 140), yes=0.1)
        result = compare([frame([a], [a, b])], 0.3, THRESHOLD)
        self.assertFalse(result["all_equivalent"])
        self.assertEqual(result["unmatched_candidates"], [0, 1])
        self.assertEqual(result["changed_frame_decisions"], 0)

    def test_candidate_decision_change_fails_despite_other_qualified_box(self):
        a = detection()
        b = detection(box=(100, 100, 140, 140), yes=THRESHOLD)
        changed = {**b, "qualified": False, "glimmer_yes": THRESHOLD - 1e-8}
        result = compare([frame([a, b], [a, changed])], 0.3, THRESHOLD)
        self.assertFalse(result["all_equivalent"])
        self.assertEqual(result["changed_candidate_decisions"], 1)
        self.assertEqual(result["changed_frame_decisions"], 0)

    def test_iou_boundary_and_score_difference_are_independent_gates(self):
        a = detection(score=0.625)
        b = detection(box=(11, 10, 50, 49), score=0.6328125)
        result = compare_frame(frame([a], [b]), 0.3, THRESHOLD)
        self.assertEqual(result["pairs"][0]["iou"], 0.95)
        self.assertTrue(result["equivalent"])
        for changed in (
            {**b, "box_xyxy": [11.01, 10, 50.01, 49]},
            {**b, "sam_confidence": 0.640625},
        ):
            self.assertFalse(compare_frame(frame([a], [changed]), 0.3, THRESHOLD)["equivalent"])

    def test_invalid_qualification_and_duplicate_frame_fail_loudly(self):
        row = frame([detection()], [detection()])
        invalid = copy.deepcopy(row)
        invalid["variants"]["combined"]["detections"][0]["qualified"] = False
        with self.assertRaises(AssertionError):
            compare_frame(invalid, 0.3, THRESHOLD)
        with self.assertRaises(AssertionError):
            compare([row, row], 0.3, THRESHOLD)
        invalid = copy.deepcopy(row)
        invalid["variants"]["combined"]["detections"][0]["sam_confidence"] = 0.3
        with self.assertRaises(AssertionError):
            compare_frame(invalid, 0.3, THRESHOLD)


if __name__ == "__main__":
    unittest.main()

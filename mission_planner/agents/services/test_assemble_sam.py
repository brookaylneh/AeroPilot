import unittest

import numpy as np

from mission_planner.agents.services.assemble_sam import (
    PRESERVED_PREFIXES,
    UNUSED_TRAINING_KEYS,
    assemble,
)


class AssembleSamTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            prefix + "weight": np.ones((2, 3), np.float32) for prefix in PRESERVED_PREFIXES
        }
        self.base["detector.backbone.weight"] = np.ones((2, 3), np.float32)
        self.base["detector.transformer.weight"] = np.ones((3, 2), np.float32)
        self.detector = {key: np.zeros(1, np.float32) for key in UNUSED_TRAINING_KEYS}
        self.detector.update(
            {
                "backbone.weight": np.zeros((2, 3), np.float32),
                "transformer.weight": np.zeros((3, 2), np.float32),
            }
        )

    def test_replaces_detector_and_preserves_mask_and_video_components(self):
        result = assemble(self.base, self.detector)
        self.assertEqual(set(result), set(self.base))
        for prefix in PRESERVED_PREFIXES:
            self.assertIs(result[prefix + "weight"], self.base[prefix + "weight"])
        self.assertIs(result["detector.backbone.weight"], self.detector["backbone.weight"])
        self.assertTrue((self.base["detector.backbone.weight"] == 1).all())

    def test_rejects_missing_trained_weights(self):
        del self.detector["transformer.weight"]
        with self.assertRaisesRegex(AssertionError, "Incomplete trained detector"):
            assemble(self.base, self.detector)

    def test_rejects_unexpected_weights(self):
        self.detector["unknown.weight"] = np.zeros(1, np.float32)
        with self.assertRaisesRegex(AssertionError, "Unexpected trained parameter"):
            assemble(self.base, self.detector)

    def test_rejects_mask_head_replacement(self):
        self.detector["segmentation_head.weight"] = np.zeros((2, 3), np.float32)
        with self.assertRaisesRegex(AssertionError, "overwrite a preserved head"):
            assemble(self.base, self.detector)

    def test_rejects_shape_or_precision_changes(self):
        for value, error in (
            (np.zeros((1, 3), np.float32), "shape"),
            (np.zeros((2, 3), np.float16), "precision"),
        ):
            with self.subTest(error=error):
                self.detector["backbone.weight"] = value
                with self.assertRaisesRegex(AssertionError, error):
                    assemble(self.base, self.detector)

    def test_rejects_incomplete_official_components(self):
        for prefix in PRESERVED_PREFIXES:
            with self.subTest(prefix=prefix):
                base = dict(self.base)
                del base[prefix + "weight"]
                with self.assertRaisesRegex(AssertionError, "Missing base component"):
                    assemble(base, self.detector)

    def test_requires_known_image_only_branch(self):
        del self.detector[sorted(UNUSED_TRAINING_KEYS)[0]]
        with self.assertRaisesRegex(AssertionError, "fourth FPN level"):
            assemble(self.base, self.detector)


if __name__ == "__main__":
    unittest.main()

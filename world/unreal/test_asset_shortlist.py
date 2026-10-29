"""Keep approved-source provenance and metric preview evidence reviewable."""

import hashlib
import json
import math
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class AssetShortlist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock = json.loads((ROOT / "world/unreal/valley-assets.lock.json").read_text())

    def test_every_required_category_has_at_most_three_candidates(self):
        candidates = self.lock["candidates"]
        categories = Counter(c["category"] for c in candidates)
        self.assertEqual(
            set(categories),
            {
                "trees",
                "ground_textures",
                "grass",
                "rocks",
                "logs",
                "water",
                "bridge",
                "tower",
                "signs",
                "vehicles",
                "tent",
            },
        )
        self.assertTrue(all(1 <= count <= 3 for count in categories.values()))
        self.assertEqual(len({c["id"] for c in candidates}), len(candidates))

    def test_candidate_provenance_distinguishes_sources_from_originals(self):
        sources = {entry["sha256"] for entry in self.lock["rendererSources"]}
        generator = ROOT / "world/unreal/render_asset_preview.py"
        self.assertIn(hashlib.sha256(generator.read_bytes()).hexdigest(), sources)
        for candidate in self.lock["candidates"]:
            with self.subTest(candidate=candidate["id"]):
                license = candidate["license"]
                self.assertIs(license["aiUsePermitted"], True)
                self.assertIsNot(license["listingNoAiFlag"], True)
                self.assertTrue(license["aiUseBasis"])
                self.assertIs(candidate["readiness"]["importedIntoUnreal"], False)
                self.assertIs(candidate["readiness"]["runtimeBudgetValidated"], False)
                self.assertIs(candidate["readiness"]["readyForPlacement"], False)
                self.assertGreaterEqual(candidate["costUsd"], 0)
                if candidate["previewInput"]["kind"] == "gltf":
                    self.assertEqual(license["id"], "CC0-1.0")
                    self.assertTrue(license["url"])
                    self.assertTrue(license["quote"])
                    self.assertEqual(len(license["evidence"]["sha256"]), 64)
                    files = candidate["acquisition"]["files"]
                    self.assertEqual(len(files), len(candidate["sourceFiles"]))
                    self.assertEqual(
                        sum(f["bytes"] for f in files),
                        sum(f["size"] for f in candidate["sourceFiles"]),
                    )
                    self.assertTrue(all(len(f["sha256"]) == 64 for f in files))
                    self.assertEqual(len(candidate["sourceEvidence"]["sha256"]), 64)
                else:
                    self.assertEqual(license["id"], "PROJECT-ORIGINAL")
                    self.assertIsNone(license["url"])
                    self.assertIsNone(license["quote"])
                    self.assertIsNone(license["evidence"])
                    self.assertEqual(license["proposedPublicLicense"], "CC0-1.0")
                    self.assertIs(license["publicLicenseGranted"], False)
                    self.assertEqual(
                        candidate["preview"]["generatedAsset"]["license"], license["id"]
                    )
                    self.assertEqual(
                        candidate["version"]["sha256"],
                        candidate["preview"]["generatorSha256"],
                    )
                    self.assertIn(candidate["version"]["sha256"], sources)
                    self.assertIs(license["createdWithGenerativeAi"], True)

    def test_previews_use_actual_geometry_and_the_sensor_camera(self):
        for candidate in self.lock["candidates"]:
            with self.subTest(candidate=candidate["id"]):
                preview = candidate["preview"]
                self.assertIsNotNone(preview)
                self.assertEqual(len(preview["boundsMetres"]), 3)
                self.assertEqual(
                    preview["triangles"], preview["singleAssetTriangles"] * preview["instances"]
                )
                self.assertGreater(preview["triangles"], 0)
                self.assertEqual(
                    preview["texturePixels"],
                    sum(t["width"] * t["height"] for t in preview["textures"]),
                )
                self.assertTrue(all(math.isfinite(v) and v >= 0 for v in preview["boundsMetres"]))
                if candidate["category"] == "trees":
                    self.assertLessEqual(preview["boundsMetres"][2], 18)
                if candidate["category"] == "grass":
                    self.assertEqual(preview["close"]["instances"], 1)
                    self.assertLess(max(preview["close"]["framedBoundsMetres"]), 1)
                nadir = preview["nadir"]
                self.assertEqual(nadir["cameraPositionMetres"], [0, 0, 30])
                self.assertEqual(nadir["cameraTargetMetres"], [0, 0, 0])
                self.assertEqual(nadir["projection"], "PERSP")
                self.assertAlmostEqual(nadir["horizontalFovDegrees"], 70, places=4)
                self.assertEqual(nadir["resolution"][0] / nadir["resolution"][1], 16 / 9)
                for view in (nadir, preview["close"]):
                    self.assertGreater(view["renderSeconds"], 0)
                    self.assertEqual(len(view["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()

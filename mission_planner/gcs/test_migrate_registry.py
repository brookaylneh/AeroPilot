"""Old JSON-array registries convert once into the line format the web server reads."""

import json
import tempfile
import unittest
from pathlib import Path

from mission_planner.gcs.migrate_registry import migrate


class MigrateRegistry(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.missions = Path(self.temporary.name)
        self.evidence = self.missions / "mission-a" / "evidence"
        self.evidence.mkdir(parents=True)
        for name in ("b-frame.jpg", "a-frame.jpg", "a-track-1-mask.png"):
            (self.evidence / name).write_bytes(b"x")
        (self.evidence / "assets.json").write_text(
            json.dumps(["b-frame.jpg", "a-frame.jpg", "a-track-1-mask.png", "a-frame.jpg"])
        )

    def test_converts_once_and_keeps_the_original(self):
        self.assertEqual(migrate(self.missions), [self.evidence])
        self.assertEqual(
            (self.evidence / "assets.txt").read_text(),
            "a-frame.jpg\na-track-1-mask.png\nb-frame.jpg\n",
        )
        self.assertTrue((self.evidence / "assets.json").exists())
        with self.assertRaises(FileExistsError):
            migrate(self.missions)

    def test_missing_registered_asset_fails_before_writing(self):
        (self.evidence / "b-frame.jpg").unlink()
        with self.assertRaisesRegex(AssertionError, "missing"):
            migrate(self.missions)
        self.assertFalse((self.evidence / "assets.txt").exists())

    def test_missions_without_old_registries_are_untouched(self):
        (self.evidence / "assets.json").unlink()
        self.assertEqual(migrate(self.missions), [])


if __name__ == "__main__":
    unittest.main()

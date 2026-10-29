import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from world.unreal.reconstruct_assets import input_body


class ReconstructionInputsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.views = []
        for role in ("front", "back", "left", "top"):
            data = role.encode()
            (self.root / (role + ".png")).write_bytes(data)
            self.views.append(
                {
                    "view": role,
                    "path": role + ".png",
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        self.patch = patch("world.unreal.reconstruct_assets.ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_list_preserves_every_view_and_order(self):
        roles = [view["view"] for view in self.views]
        body = input_body(
            {"views": self.views, "input": {"quality": "high"}, "imageFields": {"images": roles}}
        )
        self.assertEqual(body["quality"], "high")
        self.assertEqual(
            [base64.b64decode(url.split(",")[1]).decode() for url in body["images"]], roles
        )

    def test_named_inputs_preserve_roles(self):
        fields = {view["view"] + "_image_url": view["view"] for view in self.views}
        body = input_body({"views": self.views, "input": {}, "imageFields": fields})
        for field, role in fields.items():
            self.assertEqual(base64.b64decode(body[field].split(",")[1]).decode(), role)

    def test_missing_or_duplicate_view_fails_before_submission(self):
        for roles in (["front"], ["front", "back", "left", "left"]):
            with self.subTest(roles=roles), self.assertRaises(AssertionError):
                input_body({"views": self.views, "input": {}, "imageFields": {"images": roles}})

    def test_changed_reference_fails_before_submission(self):
        (self.root / "back.png").write_bytes(b"changed")
        with self.assertRaises(AssertionError):
            input_body({"views": self.views, "input": {}, "imageFields": {}})


if __name__ == "__main__":
    unittest.main()

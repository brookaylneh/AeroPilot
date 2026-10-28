"""Credential handoff must not expose secrets or persist failed authentication."""

import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from world.unreal.characters import quixel_auth


class QuixelAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / ".secrets").mkdir()
        self.path = self.root / ".secrets/quixel-auth.json"
        self.token = "test-only-placeholder-credential"
        self.connection = Mock()
        self.connection.getresponse.return_value.status = 200
        self.connection.getresponse.return_value.read.return_value = b'{"user": "test"}'
        for target, value in (
            ("ROOT", self.root),
            ("load_env", Mock()),
            ("getpass.getpass", Mock(return_value="Bearer " + self.token)),
            ("http.client.HTTPSConnection", Mock(return_value=self.connection)),
        ):
            patcher = patch("world.unreal.characters.quixel_auth." + target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_success_is_private_and_does_not_print_token(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            quixel_auth.main()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.path.read_text()), {"token": self.token})
        self.assertNotIn(self.token, output.getvalue())
        self.connection.close.assert_called_once()

    def test_failure_or_redirect_does_not_save_or_follow(self):
        for status in (401, 302):
            with self.subTest(status=status):
                self.connection.reset_mock()
                self.connection.getresponse.return_value.status = status
                with self.assertRaisesRegex(AssertionError, f"HTTP {status}"):
                    quixel_auth.main()
                self.assertFalse(self.path.exists())
                self.connection.request.assert_called_once()
                self.connection.close.assert_called_once()

    def test_existing_credential_is_preserved_without_request(self):
        self.path.write_text("existing credential")
        with self.assertRaisesRegex(AssertionError, "already exists"):
            quixel_auth.main()
        self.assertEqual(self.path.read_text(), "existing credential")
        self.connection.request.assert_not_called()

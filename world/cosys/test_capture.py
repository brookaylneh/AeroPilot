"""Verify in-memory capture preserves pixels, file encoding and frozen-state cleanup."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from PIL import Image

from world.cosys import client


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 125, 60, 20])
        response = SimpleNamespace(
            width=2,
            height=2,
            message="",
            image_data_uint8=self.rgb,
            time_stamp=123,
            camera_position=client.airsim.Vector3r(1, 2, 3),
            camera_orientation=client.airsim.Quaternionr(0, 0, 0, 1),
        )
        self.connection = Mock()
        self.connection.simGetImages.return_value = [response]
        self.connection.simGetCameraInfo.return_value = SimpleNamespace(fov=90)
        for target, value in [
            ("connect", self.connection),
            ("state", {"landed": True}),
            ("origin", {"x": 0, "y": 0, "z": 0}),
        ]:
            patched = patch.object(client, target, return_value=value)
            patched.start()
            self.addCleanup(patched.stop)

    def test_png_pixels_and_frozen_observation(self):
        encoded, info = client.capture_frame("Drone1", freeze=True, observe=lambda _: {"fixed": 1})
        self.assertEqual(Image.open(io.BytesIO(encoded)).tobytes(), self.rgb)
        self.assertEqual(info["observation"], {"fixed": 1})
        self.assertEqual(info["sha256"], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(info["pngBytes"], len(encoded))
        self.assertEqual(self.connection.simPause.call_args_list, [call(True), call(False)])

    def test_file_wrapper_preserves_jpeg_bytes_and_metadata(self):
        encoded, _ = client.capture_frame("Drone1", jpeg_quality=95)
        with tempfile.TemporaryDirectory() as directory:
            info = client.capture("Drone1", directory, "frame", jpeg_quality=95)
            path = Path(directory) / info["file"]
            self.assertEqual(path.read_bytes(), encoded)
            self.assertEqual(json.loads(path.with_suffix(".json").read_text()), info)
            self.assertEqual(info["jpegBytes"], len(encoded))

    def test_failed_rpc_releases_frozen_physics(self):
        self.connection.simGetImages.side_effect = TimeoutError("Sensor RPC timed out")
        with self.assertRaisesRegex(TimeoutError, "Sensor RPC timed out"):
            client.capture_frame("Drone1", freeze=True)
        self.assertEqual(self.connection.simPause.call_args_list, [call(True), call(False)])


if __name__ == "__main__":
    unittest.main()

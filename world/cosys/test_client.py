"""Offline geometry and telemetry checks. Never calls the simulator."""

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from world.cosys import client


class CoordinateTests(unittest.TestCase):
    def test_studio_reset_requires_its_own_native_acknowledgement(self):
        with (
            patch.dict("os.environ", {"SNR_DRONE_DATA_DIR": "/drone-data"}),
            patch.object(client.uuid, "uuid4", return_value="reset-id"),
            patch.object(client, "write_json") as write,
            patch.object(client.time, "sleep") as sleep,
            patch.object(
                client,
                "native",
                side_effect=[
                    {"mode": "studio", "modeRequestId": "previous-reset"},
                    {"mode": "studio", "modeRequestId": "reset-id"},
                ],
            ),
        ):
            client.reset_session()
        write.assert_called_once_with(
            Path("/drone-data/mission-request.json"), {"mode": "studio", "id": "reset-id"}
        )
        sleep.assert_called_once_with(0.1)

    def test_mode_timeout_does_not_send_another_request(self):
        with (
            patch.dict("os.environ", {"SNR_DRONE_DATA_DIR": "/drone-data"}),
            patch.object(client, "write_json") as write,
            patch.object(client.time, "monotonic", side_effect=[0, 21]),
            patch.object(client, "native") as native,
        ):
            with self.assertRaisesRegex(TimeoutError, "did not acknowledge studio"):
                client.reset_session()
        write.assert_called_once()
        native.assert_not_called()

    def test_vehicle_local_ned_roundtrip_with_nonzero_origin(self):
        origin = {"x": -81417.2, "y": 250, "z": 1145}
        local = {"x": 123.25, "y": -42.5, "z": -20.0}
        with patch.object(client, "origin", return_value=origin):
            world = client.to_world("Drone2", local)
            self.assertAlmostEqual(world["x"], -690.922)
            self.assertAlmostEqual(world["y"], -40.0)
            self.assertAlmostEqual(world["z"], 31.45)
            restored = client.to_local("Drone2", world)
            for axis in "xyz":
                self.assertAlmostEqual(restored[axis], local[axis])

    def test_port_and_full_world_validation(self):
        with patch.object(client, "terrain_height", return_value=10):
            client.validate_waypoint({"x": 790, "y": -790, "z": 20})
            client.validate_waypoint({"x": -738, "y": -27, "z": 35})
            for point in [
                {"x": -816, "y": 0, "z": 35},
                {"x": -816, "y": 50, "z": 35},
                {"x": 900, "y": 0, "z": 20},
                {"x": 0, "y": 0, "z": 10.5},
                {"x": math.nan, "y": 0, "z": 20},
            ]:
                with self.subTest(point=point), self.assertRaises(ValueError):
                    client.validate_waypoint(point)

    def test_atomic_reports_reject_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            client.write_json(path, {"passed": True})
            self.assertEqual(json.loads(path.read_text()), {"passed": True})
            with self.assertRaises(ValueError):
                client.write_json(path, {"latency": math.nan})
            self.assertEqual(json.loads(path.read_text()), {"passed": True})

    def test_native_telemetry_rejects_missing_aircraft_and_stale_frames(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"SNR_DRONE_DATA_DIR": directory}),
        ):
            path = Path(directory) / "runtime.json"
            report = {
                "schemaVersion": 1,
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "vehicles": [{"id": name} for name in client.NAMES],
            }
            path.write_text(json.dumps(report))
            self.assertEqual(len(client.native()["vehicles"]), 4)
            report["vehicles"].pop()
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(AssertionError, "Incomplete"):
                client.native()
            report["vehicles"].append({"id": "Drone4"})
            report["capturedAt"] = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(RuntimeError, "stale"):
                client.native()


if __name__ == "__main__":
    unittest.main()

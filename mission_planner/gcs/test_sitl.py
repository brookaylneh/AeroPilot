"""Coupled launch safety and port isolation without starting aircraft."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mission_planner.gcs.geodesy import ROOT
from mission_planner.gcs.sitl import SAFETY_PARAMETERS, check_ports, launches


class SitlLaunch(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.settings = json.loads((ROOT / "world/cosys/settings.json").read_text())
        self.settings["ApiServerPort"] = 41452
        for index, vehicle in enumerate(self.settings["Vehicles"].values()):
            vehicle.update(
                VehicleType="ArduCopter",
                LocalHostIp="127.0.0.1",
                UdpIp="127.0.0.1",
                UdpPort=9203 + index * 10,
                ControlPortLocal=9202 + index * 10,
            )
        self.settings_path = self.directory / "settings.json"
        self.settings_path.write_text(json.dumps(self.settings))
        environment = patch.dict(
            "os.environ",
            {
                "SNR_SITL_BINARY": "/usr/bin/true",
                "SNR_SITL_MODEL": "airsim-copter",
                "SNR_SITL_INSTANCE_BASE": "20",
                "SNR_SITL_PORT_STEP": "10",
                "SNR_MAVLINK_BASE_PORT": "14620",
                "SNR_SITL_RC_BASE_PORT": "5701",
                "SNR_COSYS_SETTINGS": str(self.settings_path),
                "SNR_COSYS_PORT": "41452",
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_four_airsim_instances_and_safety_defaults(self):
        aircraft = launches(self.directory)
        self.assertEqual(len(aircraft), 4)
        self.assertEqual(
            [launch["ports"] for launch in aircraft],
            [
                [14620, 5701, 9203],
                [14621, 5711, 9213],
                [14622, 5721, 9223],
                [14623, 5731, 9233],
            ],
        )
        for index, launch in enumerate(aircraft):
            args = launch["arguments"]
            self.assertEqual(args[args.index("--instance") + 1], str(20 + index))
            self.assertEqual(args[args.index("--sysid") + 1], str(1 + index))
            self.assertEqual(args[args.index("--model") + 1], "airsim-copter")
        parameters = {}
        for path in aircraft[0]["arguments"][-1].split(","):
            for line in Path(path).read_text().splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    key, value = line.split()
                    parameters[key] = float(value)
        self.assertEqual(parameters["FRAME_TYPE"], 1)
        self.assertEqual(parameters["SCHED_LOOP_RATE"], 300)
        for key, expected in SAFETY_PARAMETERS.items():
            self.assertEqual(parameters[key], expected, key)

    def test_model_is_required(self):
        with patch.dict("os.environ") as environment:
            del environment["SNR_SITL_MODEL"]
            with self.assertRaises(KeyError):
                launches(self.directory)

    def test_unknown_model_fails(self):
        with patch.dict("os.environ", {"SNR_SITL_MODEL": "airsim"}):
            with self.assertRaisesRegex(AssertionError, "Unsupported SITL model"):
                launches(self.directory)

    def test_cross_slot_sensor_port_fails(self):
        self.settings["Vehicles"]["Drone2"]["UdpPort"] = 9013
        self.settings_path.write_text(json.dumps(self.settings))
        with self.assertRaisesRegex(AssertionError, "Drone2"):
            launches(self.directory)

    def test_changed_spawn_fails(self):
        self.settings["Vehicles"]["Drone3"]["X"] += 1
        self.settings_path.write_text(json.dumps(self.settings))
        with self.assertRaisesRegex(AssertionError, "Drone3"):
            launches(self.directory)

    def test_changed_origin_fails(self):
        self.settings["OriginGeopoint"]["Latitude"] += 1
        self.settings_path.write_text(json.dumps(self.settings))
        with self.assertRaisesRegex(AssertionError, "OriginGeopoint"):
            launches(self.directory)

    def test_builtin_benchmark_is_explicit(self):
        with patch.dict("os.environ", {"SNR_SITL_MODEL": "quad"}):
            aircraft = launches(self.directory)
        self.assertEqual(aircraft[0]["ports"], [14620, 5701])
        self.assertFalse((self.directory / "safety.parm").exists())
        self.assertEqual(len(aircraft[0]["arguments"][-1].split(",")), 1)

    def test_ss_detects_loopback_wildcard_and_ipv6_collisions(self):
        for address in ("127.0.0.1", "0.0.0.0", "[::]"):
            with self.subTest(address=address):
                output = f"udp UNCONN 0 0 {address}:9203 0.0.0.0:*\n"
                with patch(
                    "subprocess.run", return_value=subprocess.CompletedProcess([], 0, output)
                ):
                    with self.assertRaisesRegex(AssertionError, "Listener collision"):
                        check_ports([9203])

    def test_ss_failure_propagates(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ss", 5)):
            with self.assertRaises(subprocess.TimeoutExpired):
                check_ports([9203])

    def test_duplicate_listeners_fail(self):
        with self.assertRaisesRegex(AssertionError, "Repeated listener ports"):
            check_ports([9203, 9203])


if __name__ == "__main__":
    unittest.main()

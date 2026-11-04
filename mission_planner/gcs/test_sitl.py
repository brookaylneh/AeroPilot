"""Coupled launch safety and port isolation without starting aircraft."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mission_planner.gcs.geodesy import ROOT
from mission_planner.gcs.sitl import SAFETY_PARAMETERS, check_ports, launches
from world.cosys.configure_drones import generate
from world.cosys.terrain import WORLD


@unittest.skipUnless(
    (ROOT / ".local/vendor/ardupilot").is_dir(),
    "needs the vendored ArduPilot source under .local/vendor",
)
class SitlLaunch(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.settings = generate(WORLD, 41452, 20)
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
            self.assertEqual(launch["expected_parameters"]["ATC_ACC_Y_MAX"], 20)
        parameters = {}
        for path in aircraft[0]["arguments"][-1].split(","):
            for line in Path(path).read_text().splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    key, value = line.split()
                    parameters[key] = float(value)
        self.assertEqual(parameters["FRAME_TYPE"], 1)
        self.assertEqual(parameters["SCHED_LOOP_RATE"], 184)
        self.assertEqual(parameters["ATC_ACC_Y_MAX"], 20)
        self.assertEqual(parameters["BATT_MONITOR"], 4)
        self.assertEqual(parameters["BATT_CAPACITY"], 50000)
        self.assertEqual(parameters["SIM_BATT_CAP_AH"], 50)
        self.assertEqual(parameters["SIM_BATT_VOLTAGE"], 12.6)
        self.assertEqual(parameters["BATT_FS_LOW_ACT"], 2)
        self.assertEqual(parameters["BATT_LOW_VOLT"], 10.5)
        self.assertEqual(parameters["BATT_LOW_TIMER"], 10)
        self.assertEqual(parameters["RTL_ALT_M"], 80)
        self.assertEqual(parameters["RTL_CONE_SLOPE"], 0)
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
        safety = (self.directory / "safety.parm").read_text()
        self.assertIn("BATT_FS_LOW_ACT 2\n", safety)
        self.assertNotIn("SCHED_LOOP_RATE", safety)
        self.assertNotIn("SIM_BATT_CAP_AH", safety)
        self.assertNotIn("ATC_ACC_Y_MAX", safety)
        self.assertNotIn("ATC_ACC_Y_MAX", aircraft[0]["expected_parameters"])
        self.assertEqual(len(aircraft[0]["arguments"][-1].split(",")), 2)

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

    def test_patches_apply_to_pinned_upstream_without_fuzz(self):
        source = ROOT / ".local/vendor/ardupilot"
        for relative in (
            "libraries/AP_RCProtocol/AP_RCProtocol_UDP.cpp",
            "libraries/SITL/SIM_AirSim.cpp",
            "libraries/SITL/SIM_AirSim.h",
            "libraries/SITL/SIM_GPS.cpp",
        ):
            target = self.directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                subprocess.run(
                    ["git", "-C", str(source), "show", "HEAD:" + relative],
                    check=True,
                    capture_output=True,
                    timeout=5,
                ).stdout
            )
        for name in ("loopback", "airsim-liveness", "gps-cadence", "airsim-battery"):
            with (ROOT / f"mission_planner/gcs/sitl-{name}.patch").open() as patch_file:
                subprocess.run(
                    ["patch", "--batch", "--fuzz=0", "-p1"],
                    cwd=self.directory,
                    stdin=patch_file,
                    check=True,
                    capture_output=True,
                    timeout=5,
                )


if __name__ == "__main__":
    unittest.main()

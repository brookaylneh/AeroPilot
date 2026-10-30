"""Reject settings that decouple the four autopilots from their authored spawn frame."""

import copy
import importlib
import json
import unittest

from mission_planner.gcs import geodesy

configure = importlib.import_module("world.cosys.configure-drones")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.world = json.loads((configure.ROOT / "world/cosys/world.json").read_text())

    def test_rgb_lumen_preserves_native_resolution_and_fixed_exposure(self):
        settings = configure.generate(self.world, 41451, 0)
        self.assertEqual(
            settings, json.loads((configure.ROOT / "world/cosys/settings.json").read_text())
        )
        for vehicle in settings["Vehicles"].values():
            captures = vehicle["Cameras"]["front_center"]["CaptureSettings"]
            scene = next(capture for capture in captures if capture["ImageType"] == 0)
            self.assertEqual((scene["Width"], scene["Height"]), (3840, 2160))
            self.assertIs(scene["ForceUpdate"], False)
            for capture in [*settings["CameraDefaults"]["CaptureSettings"], *captures]:
                self.assertIs(capture["LumenGIEnable"], True)
                self.assertIs(capture["LumenReflectionEnable"], True)
                self.assertEqual(capture["AutoExposureMethod"], 2)
                self.assertIs(capture["AutoExposureApplyPhysicalCameraExposure"], False)
                self.assertEqual(capture["AutoExposureCompensation"], -13)
                self.assertEqual(capture["WhiteBalanceTemperature"], 6500)
                self.assertEqual(capture["WhiteBalanceTint"], 0)
                self.assertEqual(capture["MotionBlurAmount"], 0)
                self.assertEqual(capture["BloomIntensity"], 0)

    def test_deployment_and_coupling_slots_do_not_share_udp_ports(self):
        slots = [
            configure.generate(self.world, 41451, 0),
            configure.generate(self.world, 41452, 20),
        ]
        ports = [
            vehicle[key]
            for settings in slots
            for vehicle in settings["Vehicles"].values()
            for key in ("UdpPort", "ControlPortLocal")
        ]
        self.assertEqual(len(ports), len(set(ports)))
        for settings in slots:
            for name, home in geodesy.homes().items():
                vehicle = settings["Vehicles"][name]
                self.assertAlmostEqual(
                    settings["OriginGeopoint"]["Altitude"] - vehicle["Z"],
                    home["altitude_msl_m"],
                )

    def test_rejects_wrong_frame_or_player_start(self):
        changes = [
            ("coordinateSystem", "NED_METERS"),
            ("north", "+Y"),
            ("east", "+X"),
            ("moveWorldOrigin", True),
            ("playerStartUeCm", {"x": 1, "y": 0, "z": 4000}),
            ("playerStartUeCm", {"x": 0, "y": 0, "z": 0}),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value), self.assertRaises(AssertionError):
                configure.generate({**self.world, key: value}, 41452, 20)

    def test_rejects_incomplete_or_mismatched_fleet(self):
        world = copy.deepcopy(self.world)
        del world["vehicles"]["Drone4"]
        with self.assertRaises(AssertionError):
            configure.generate(world, 41452, 20)
        for axis in "xyz":
            world = copy.deepcopy(self.world)
            world["vehicles"]["Drone2"]["spawnUeCm"][axis] += 100
            with self.subTest(axis=axis), self.assertRaises(AssertionError):
                configure.generate(world, 41452, 20)

    def test_rejects_reserved_or_overflowing_ports(self):
        for rpc, instance in ((0, 20), (65536, 20), (41452, -1), (41452, 5651)):
            with self.subTest(rpc=rpc, instance=instance), self.assertRaises(AssertionError):
                configure.generate(self.world, rpc, instance)


if __name__ == "__main__":
    unittest.main()

import unittest

from mission_planner.gcs.battery import (
    CUTOFF_PERCENT,
    VOLTAGE_FLOOR_PERCENT,
    available_seconds,
    required_percent,
)


class BatteryTests(unittest.TestCase):
    def test_ten_percent_charge_floor_does_not_hide_the_voltage_limit(self):
        self.assertEqual(CUTOFF_PERCENT, 10)
        self.assertAlmostEqual(VOLTAGE_FLOOR_PERCENT, 45.54555555555556)
        self.assertAlmostEqual(available_seconds(100), 1960.36)
        self.assertAlmostEqual(available_seconds(80), 1240.36)
        self.assertEqual(available_seconds(VOLTAGE_FLOOR_PERCENT), 0)
        self.assertLess(available_seconds(45), 0)
        self.assertLess(available_seconds(10), 0)
        self.assertAlmostEqual(required_percent(1960.36), 100)
        self.assertGreater(required_percent(1961), 100)

    def test_invalid_charge_and_duration_raise(self):
        for percent in (-1, 101, float("nan"), float("inf")):
            with self.subTest(percent=percent), self.assertRaises(AssertionError):
                available_seconds(percent)
        for seconds in (-1, float("nan"), float("inf")):
            with self.subTest(seconds=seconds), self.assertRaises(AssertionError):
                required_percent(seconds)

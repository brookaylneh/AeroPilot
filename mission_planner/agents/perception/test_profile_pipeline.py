"""Busy-time reduction distinguishes overlapping work from elapsed time."""

import unittest

from mission_planner.agents.perception.profile_pipeline import occupancy, percentiles


class Profile(unittest.TestCase):
    def test_overlapping_requests_are_counted_once_for_busy_time(self):
        self.assertEqual(occupancy([(0, 2), (1, 3), (5, 6)]), {"0": 2, "1": 3, "2": 1})

    def test_adjacent_intervals_do_not_invent_overlap(self):
        self.assertEqual(occupancy([(0, 1), (1, 2)]), {"1": 2})

    def test_missing_failure_measurements_remain_explicit(self):
        values = percentiles([None, 1, 3])
        self.assertEqual(values["measured"], 2)
        self.assertEqual(values["unmeasured"], 1)
        self.assertEqual(values["mean"], 2)
        self.assertEqual(percentiles([])["percentiles"], None)
        with self.assertRaises(AssertionError):
            percentiles([float("nan")])


if __name__ == "__main__":
    unittest.main()

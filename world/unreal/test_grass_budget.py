import copy
import json
import unittest
from pathlib import Path

from world.unreal.grass_budget import LIMIT, cell_bound, scene_budget


class GrassBudgetTests(unittest.TestCase):
    def setUp(self):
        self.grass = json.loads(Path(__file__).with_name("valley-grass.json").read_text())

    def test_closed_cell_boundaries(self):
        self.assertGreaterEqual(cell_bound(32, 32), 12)
        self.assertEqual(cell_bound(168, 16), 394)

    def test_four_independent_mission_sources(self):
        result = scene_budget(self.grass, 4, 20_000, 64)
        self.assertEqual(result["profiles"]["mission"]["sources"], 4)
        self.assertLessEqual(result["worstCaseSceneInstances"], LIMIT)

    def test_excessive_reach_fails(self):
        grass = copy.deepcopy(self.grass)
        grass["profiles"]["studio"][-1]["reachM"] = 1600
        with self.assertRaises(AssertionError):
            scene_budget(grass, 4, 20_000, 64)


if __name__ == "__main__":
    unittest.main()

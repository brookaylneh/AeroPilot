import copy
import unittest

from world.unreal.blender.spec import check, load


class SpecTests(unittest.TestCase):
    def test_tracked_families_pass(self):
        structures = sorted(load("structures"))
        self.assertEqual(
            structures,
            [
                "antenna-mast",
                "fire-lookout",
                "ranger-cabin",
                "snow-survey-hut",
                "timber-footbridge",
                "vault-toilet",
            ],
        )

    def test_schema_failures_are_loud(self):
        family = {"schemaVersion": 1, "assets": load("structures")}
        for mutate in (
            lambda a: a.pop("collision"),
            lambda a: a.update(extra=1),
            lambda a: a.update(dimensionsM=[9.6, 6.6]),
            lambda a: a.update(toleranceM=0),
            lambda a: a.update(buriedM=-0.1),
            lambda a: a.update(textureSize=3000),
            lambda a: a["panels"].update(side=[0, 0]),
            lambda a: a["panels"].update(top=[0, 0]),
            lambda a: a["correspondences"][0].update(pixel=[0, 2880]),
            lambda a: a["colourRegions"].update(roof=[[10, 10, 5, 20]]),
        ):
            broken = copy.deepcopy(family)
            mutate(broken["assets"]["ranger-cabin"])
            with self.assertRaises((AssertionError, KeyError)):
                check(broken)
        for mutate in (
            lambda a: a["panels"].update(front=[0, 0]),
            lambda a: a["correspondences"].append(
                family["assets"]["ranger-cabin"]["correspondences"][0]
            ),
            lambda a: a.update(reference="vault-toilet"),
        ):
            broken = copy.deepcopy(family)
            mutate(broken["assets"]["vault-toilet"])
            with self.assertRaises(AssertionError):
                check(broken)


if __name__ == "__main__":
    unittest.main()

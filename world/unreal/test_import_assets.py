import copy
import json
import math
import unittest
from pathlib import Path

from world.unreal.blender.spec import load
from world.unreal.import_assets import (
    EXTERNAL,
    camel,
    check_dimensions,
    family_spec,
    images,
    names,
    textures,
    ue_matrix,
)

# Height above grade in metres per lodgepole variant (valley facts, cap 18 m).
LODGEPOLE_M = {"mature": (15, 18), "edge": (10, 15), "sapling": (1, 3)}


def apply(matrix, local):
    """A local point through the row-major Unreal matrix (row vectors)."""
    return [sum(local[r] * matrix[r][c] for r in range(3)) + matrix[3][c] for c in range(3)]


class ImportTests(unittest.TestCase):
    def test_names_follow_the_content_convention(self):
        self.assertEqual(names("lodgepole-mature"), "SM_Lodgepole_Mature")
        self.assertEqual(names("granite-hero-boulder"), "SM_Granite_HeroBoulder")

    def test_slot_names_survive_interchange_sanitising(self):
        self.assertEqual(camel("Mill-scale steel"), "MillScaleSteel")
        self.assertEqual(camel("Mill_scale_steel"), "MillScaleSteel")
        self.assertEqual(camel("MI_GraniteHeroBoulder"), "MIGraniteHeroBoulder")

    def test_structure_slots_import_only_their_images(self):
        albedo, normal = {"name": "deck_albedo"}, {"name": "n.jpg", "strength": 1.2}
        slot = {"baseColor": albedo, "roughness": 0.4, "normal": [normal]}
        self.assertEqual(images(slot), [("BC", albedo), ("N", normal)])
        with self.assertRaises(AssertionError):
            images(slot | {"normal": [normal, normal]})

    def test_build_frame_maps_to_unreal_centimetres(self):
        # A Blender quarter turn about +Z sends front +X to left +Y; Unreal's left is -Y.
        c, s = math.cos(math.pi / 2), math.sin(math.pi / 2)
        quarter = [[2 * c, -2 * s, 0.0], [2 * s, 2 * c, 0.0], [0.0, 0.0, 2.0]]
        matrix = ue_matrix(quarter, [1.0, 2.0, 3.0], [0.5, -0.5, 0.0])
        for got, expected in zip(apply(matrix, [0, 0, 0]), [150, -150, 300]):
            self.assertAlmostEqual(got, expected)
        for got, expected in zip(apply(matrix, [100, 0, 0]), [150, -350, 300]):
            self.assertAlmostEqual(got, expected)
        for got, expected in zip(apply(matrix, [0, 0, 100]), [150, -150, 500]):
            self.assertAlmostEqual(got, expected)

    def test_reflections_are_rejected(self):
        with self.assertRaises(AssertionError):
            ue_matrix([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0, 0], [0, 0, 0])

    def test_dimensions_hold_to_five_percent(self):
        spec = {"dimensionsM": [6.0, 5.0, 17.0]}
        self.assertEqual(check_dimensions([[0, 0, 0], [612, 490, 1700]], spec), [6.12, 4.9, 17.0])
        with self.assertRaises(AssertionError):
            check_dimensions([[0, 0, 0], [600, 500, 1600]], spec)

    def test_conifers_respect_species_heights(self):
        for asset, spec in load("conifers").items():
            low, high = LODGEPOLE_M[asset.split("-")[1]]
            self.assertTrue(low <= spec["dimensionsM"][2] - spec["buriedM"] <= high, asset)
            self.assertFalse(spec["collision"], asset)

    def test_textures_group_by_material_and_kind(self):
        kinds = ("BC", "N", "N_GL", "ORM")
        images = [f"T_LodgepoleMatureBark_{k}" for k in kinds]
        groups = textures(Path("/b"), "lodgepole-mature", images)
        self.assertEqual(sorted(groups["LodgepoleMatureBark"]), ["BC", "N", "ORM"])
        self.assertEqual(groups["LodgepoleMatureBark"]["N"], Path("/b/T_LodgepoleMatureBark_N.png"))
        for images in (["T_LodgepoleMatureBark_BC"], ["T_Bark_BC", "T_Bark_ORM"]):
            with self.assertRaises(AssertionError):
                textures(Path("/b"), "lodgepole-mature", images)

    def test_external_builds_must_match_their_entry(self):
        entry = json.loads(EXTERNAL.read_text())["assets"]["command-van"]
        report = {"spec": {"dimensionsM": [6.03, 2.22, 2.69]}, "details": {"external": entry}}
        spec, content = family_spec("external", "command-van", report)
        self.assertEqual((spec, content), (report["spec"], "/Game/Valley/Vehicles"))
        stale = copy.deepcopy(report)
        stale["details"]["external"]["yawDegrees"] += 90
        with self.assertRaises(AssertionError):
            family_spec("external", "command-van", stale)

    def test_external_meshes_have_distinct_names(self):
        assets = json.loads(EXTERNAL.read_text())["assets"]
        meshes = {names(a) for a in assets}
        self.assertEqual(len(meshes), len(assets))


if __name__ == "__main__":
    unittest.main()

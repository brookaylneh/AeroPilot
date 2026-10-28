"""Prepare or assemble a roster recipe using Epic's MetaHuman editor APIs."""

import argparse
import json
import math
import sys
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import metahuman  # noqa: E402
import render_metahuman  # noqa: E402


def prepare(recipe, subsystem):
    report = {"create": metahuman.create(recipe, subsystem)}
    report.update(style(recipe, subsystem))
    return report


def style(recipe, subsystem):
    report = {}
    character = unreal.load_asset(recipe["asset_folder"] + "/" + recipe["asset_name"])
    assert subsystem.try_add_object_to_edit(character)
    try:
        makeup = unreal.MetaHumanCharacterMakeupSettings()
        subsystem.commit_makeup_settings(character, makeup)
        actual = character.get_editor_property("makeup_settings")
        assert not actual.foundation.apply_foundation
        for name in ("eyes", "blush", "lips"):
            assert getattr(actual, name).type == getattr(makeup, name).type
        if "freckles" in recipe:
            skin = character.get_editor_property("skin_settings")
            freckles = skin.freckles
            values = recipe["freckles"]
            freckles.mask = getattr(unreal.MetaHumanCharacterFrecklesMask, values["mask"])
            for name in ("density", "strength", "saturation", "tone_shift"):
                assert 0 <= values[name] <= 1
                freckles.set_editor_property(name, values[name])
            skin.freckles = freckles
            subsystem.commit_skin_settings(character, skin)
            actual = character.get_editor_property("skin_settings").freckles
            assert actual.mask == freckles.mask
            for name in ("density", "strength", "saturation", "tone_shift"):
                assert math.isclose(getattr(actual, name), values[name], abs_tol=1e-5)
            report["freckles"] = values
        report["body_constraints"] = [
            {
                "name": str(constraint.name),
                "target_measurement": constraint.target_measurement,
                "min_measurement": constraint.min_measurement,
                "max_measurement": constraint.max_measurement,
            }
            for constraint in subsystem.get_body_constraints(character, False)
        ]
        settings = character.get_editor_property("face_evaluation_settings")
        report["face_control_readback"] = {}
        for name, value in recipe["face"].items():
            actual = settings.get_editor_property(name)
            assert math.isclose(actual, value, abs_tol=1e-5), (name, actual, value)
            report["face_control_readback"][name] = actual
        collection = subsystem.get_preview_collection(character)
        instance = collection.default_instance
        slots = {"Hair", "Eyebrows", "Eyelashes", "Beard", "Mustache", "Outfits"}
        for data in list(instance.get_slot_selection_data()):
            if str(data.selection.slot_name) in slots:
                instance.set_single_slot_selection(
                    data.selection.slot_name, unreal.MetaHumanPaletteItemKey()
                )
        assert not any(
            str(data.selection.slot_name) in slots for data in instance.get_slot_selection_data()
        )
        subsystem.on_edit_preview_collection(character)
        assert unreal.EditorAssetLibrary.save_loaded_asset(character, only_if_is_dirty=False)
        report["makeup_disabled"] = True
    finally:
        subsystem.remove_object_to_edit(character)
    report["wardrobe"] = metahuman.wardrobe(recipe, subsystem)
    report["cloud"] = metahuman.cloud(recipe, subsystem)
    return report


def finish(recipe, subsystem):
    report = metahuman.assemble(recipe, subsystem)
    return apply_materials(recipe, report)


def materials(recipe, subsystem):
    return apply_materials(recipe, metahuman.inventory(recipe))


def apply_materials(recipe, report):
    parameters = {}
    instance_root = recipe["assembly_folder"] + "/" + recipe["asset_name"]
    grooms = 0
    garments = 0
    for item in report["materials"]:
        path = item["asset"]
        if path.startswith(instance_root + "/Grooms/") and "hairMelanin" in item["scalars"]:
            values = dict(recipe["groom_color"])
            if "Eyelashes" in path:
                values["WhiteAmount"] = 0
            parameters[path] = values
            grooms += 1
        if path.startswith(instance_root + "/Clothing/"):
            matches = [name for name in ("Shirt", "Short") if name in path]
            assert len(matches) == 1, path
            color = recipe["garment_colors"][matches[0]]
            parameters[path] = {
                name: color for name in ("diffuse_color_1", "diffuse_color_2", "B_diffuse_color_1")
            }
            garments += 1
    assert grooms >= 3 and garments == 2, (grooms, garments)
    recipe["material_parameters"] = parameters
    report["material_readback"] = metahuman.materials(recipe)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("stage", choices=("prepare", "style", "finish", "materials", "render"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.stage == "render":
        sys.argv = [
            str(HERE / "render_metahuman.py"),
            str(args.recipe),
            str(args.output / "cameras.json"),
            str(args.output / "render"),
        ]
        render_metahuman.main()
        return
    recipe = json.loads(args.recipe.read_text())
    report_path = args.output / (args.stage + ".json")
    assert not report_path.exists(), report_path
    subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
    operations = {"prepare": prepare, "style": style, "finish": finish, "materials": materials}
    report = operations[args.stage](recipe, subsystem)
    with report_path.open("x") as stream:
        json.dump(report, stream, indent=2)
    unreal.log("SNR_ROSTER_DONE " + str(report_path))


if __name__ == "__main__":
    main()
